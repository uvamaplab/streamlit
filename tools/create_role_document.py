"""Create-only write of ONE synthetic role document for the dashboard test. PREPARED; needs approval.

Fixed target: Cosmos account cosmosmapltest, database cosmicworks, container roles_old (partition key
/pid, verified live October 29). Fixed document id: study:maplsynth. The only input is the Clerk user
id of the real development-portal test user (supreme-haddock-40), which must be supplied; nothing is
invented. The document is what apimmapl dashboard/v1 GET /roles point-reads for that user:

    {"id": "study:maplsynth", "pid": "<user id>", "roles": ["admin"], "synthetic": true}

Create-only: a POST without the upsert header, so an existing document answers 409 and nothing is
overwritten. Entra token for https://cosmos.azure.com (the operator's identity); account keys are
never used. Cosmos returns the created document; only its id and status are printed.

    python -m tools.create_role_document --pid user_XXXX              # dry run
    python -m tools.create_role_document --pid user_XXXX --execute    # needs approval
"""
import argparse
import json
import re
import sys
from email.utils import formatdate

ACCOUNT = "cosmosmapltest"
DATABASE = "cosmicworks"
CONTAINER = "roles_old"
DOC_ID = "study:maplsynth"
_CLERK_USER = re.compile(r"^user_[A-Za-z0-9]{10,64}$")


def document(pid):
    if not _CLERK_USER.match(pid or ""):
        raise SystemExit("--pid must be a Clerk user id (user_...), supplied explicitly.")
    return {"id": DOC_ID, "pid": pid, "roles": ["admin"], "synthetic": True}


def create(pid, token, post):
    """POSTs the document; `post(url, headers, body)` returns (status, body). Returns the status."""
    doc = document(pid)
    url = f"https://{ACCOUNT}.documents.azure.com/dbs/{DATABASE}/colls/{CONTAINER}/docs"
    headers = {
        "Authorization": f"type=aad&ver=1.0&sig={token}",
        "x-ms-version": "2018-12-31",
        "x-ms-date": formatdate(usegmt=True),
        "x-ms-documentdb-partitionkey": json.dumps([pid]),
        "Content-Type": "application/json",
        # Deliberately no x-ms-documentdb-is-upsert: an existing document must be refused, not replaced.
    }
    status, _ = post(url, headers, json.dumps(doc))
    if status == 409:
        raise SystemExit(f"{DOC_ID} already exists for that user; not overwriting it.")
    if status != 201:
        raise SystemExit(f"Create failed with HTTP {status}.")
    return status


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    doc = document(args.pid)
    if not args.execute:
        print(f"Would create {DOC_ID} in {ACCOUNT}/{DATABASE}/{CONTAINER} for the supplied user (dry run).")
        return 0
    import requests
    from azure.identity import DefaultAzureCredential
    token = DefaultAzureCredential().get_token("https://cosmos.azure.com/.default").token

    def post(url, headers, body):
        r = requests.post(url, headers=headers, data=body, timeout=30, allow_redirects=False)
        return r.status_code, None

    print(f"{doc['id']}: HTTP {create(args.pid, token, post)} (created)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
