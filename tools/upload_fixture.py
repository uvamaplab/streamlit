"""Bounded upload of the synthetic dashboard fixture. PREPARED; running it needs owner approval.

Target is fixed: account outputsmapltest, container maplsynth, path datums/. Nothing else can be
named. Credentials are the operator's Entra identity (DefaultAzureCredential); account keys are never
used.

Order and checks:
  1. The local table must be the fixture (maplab.fixture = dashboard-test-input).
  2. The container must exist (created separately). Anything in it outside datums/ refuses the run.
  3. If datums/_delta_log exists remotely, it must carry the fixture marker, or the run refuses.
  4. Data files upload first, the _delta_log last, so an interrupted run leaves no readable table
     pointing at missing files. Every upload is create-only: an existing blob with the same size and
     MD5 is skipped (a resumed run), a different one stops the run.
  5. verify() reads the remote table back and compares version, schema, marker, row count and the
     synthetic participants with the local table. A marker alone is never taken as completeness.

    python -m tools.upload_fixture --local <out>/datums            # dry run: lists what it would do
    python -m tools.upload_fixture --local <out>/datums --execute  # needs approval
"""
import argparse
import base64
import hashlib
import os
import sys

ACCOUNT = "outputsmapltest"
CONTAINER = "maplsynth"
PREFIX = "datums/"
LOG_DIR = "_delta_log/"


class Refused(SystemExit):
    pass


def local_files(local_table):
    """(relative path, absolute path) pairs, data files first and the Delta log last."""
    files = []
    for root, _, names in os.walk(local_table):
        for n in names:
            full = os.path.join(root, n)
            files.append((os.path.relpath(full, local_table).replace(os.sep, "/"), full))
    data = sorted(f for f in files if not f[0].startswith(LOG_DIR))
    log = sorted(f for f in files if f[0].startswith(LOG_DIR))
    return data + log


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def check_local(local_table):
    from deltalake import DeltaTable
    from tools.synthetic_fixture import FIXTURE_PROPERTY, FIXTURE_VALUE
    if DeltaTable(local_table).metadata().configuration.get(FIXTURE_PROPERTY) != FIXTURE_VALUE:
        raise Refused("The local table is not the synthetic fixture.")


def check_remote(container, marker_reader):
    """Refuses unrelated data. `marker_reader` returns the remote table's configuration, or None."""
    names = [b.name for b in container.list_blobs()]
    outside = [n for n in names if not n.startswith(PREFIX)]
    if outside:
        raise Refused(f"{CONTAINER} holds {len(outside)} blob(s) outside {PREFIX}; not touching it.")
    if any(n.startswith(PREFIX + LOG_DIR) for n in names):
        config = marker_reader()
        from tools.synthetic_fixture import FIXTURE_PROPERTY, FIXTURE_VALUE
        if not config or config.get(FIXTURE_PROPERTY) != FIXTURE_VALUE:
            raise Refused(f"{PREFIX} exists and is not the synthetic fixture; not touching it.")
    return set(names)


def upload(container, local_table, existing, execute):
    """Create-only upload. Returns (uploaded, skipped)."""
    from azure.storage.blob import ContentSettings
    uploaded, skipped = [], []
    for rel, full in local_files(local_table):
        name = PREFIX + rel
        digest = md5_of(full)
        if name in existing:
            props = container.get_blob_client(name).get_blob_properties()
            same = props.size == os.path.getsize(full) and bytes(props.content_settings.content_md5 or b"") == digest
            if not same:
                raise Refused(f"{name} already exists with different content; stopping.")
            skipped.append(name)
            continue
        if execute:
            with open(full, "rb") as f:
                container.upload_blob(name, f, overwrite=False, content_settings=ContentSettings(content_md5=bytearray(digest)))
        uploaded.append(name)
    return uploaded, skipped


def verify(local_table, remote_table):
    """Compares a remote DeltaTable with the local fixture. Returns a list of problems (empty = OK)."""
    from tools.synthetic_fixture import FIXTURE_PROPERTY, FIXTURE_VALUE, PARTICIPANTS
    from deltalake import DeltaTable
    local = DeltaTable(local_table)
    problems = []
    if remote_table.version() != local.version():
        problems.append(f"version {remote_table.version()} != {local.version()}")
    if remote_table.schema().to_arrow() != local.schema().to_arrow():
        problems.append("schema differs")
    if remote_table.metadata().configuration.get(FIXTURE_PROPERTY) != FIXTURE_VALUE:
        problems.append("fixture marker missing")
    lt, rt = local.to_pyarrow_table(), remote_table.to_pyarrow_table()
    if rt.num_rows != lt.num_rows:
        problems.append(f"rows {rt.num_rows} != {lt.num_rows}")
    if set(rt.column("pid").to_pylist()) != {p for p, _ in PARTICIPANTS}:
        problems.append("participants differ")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", required=True)
    parser.add_argument("--execute", action="store_true", help="actually upload (needs approval)")
    args = parser.parse_args(argv)

    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    from deltalake import DeltaTable

    check_local(args.local)
    credential = DefaultAzureCredential()
    container = BlobServiceClient(f"https://{ACCOUNT}.blob.core.windows.net", credential).get_container_client(CONTAINER)
    if not container.exists():
        raise Refused(f"{ACCOUNT}/{CONTAINER} does not exist; create it first (separate step).")

    def options():
        return {"ACCOUNT_NAME": ACCOUNT, "BEARER_TOKEN": credential.get_token("https://storage.azure.com/.default").token}

    def remote_marker():
        try:
            return DeltaTable(f"abfs://{CONTAINER}/datums", storage_options=options()).metadata().configuration
        except Exception:
            return None

    existing = check_remote(container, remote_marker)
    uploaded, skipped = upload(container, args.local, existing, args.execute)
    print(f"{'Uploaded' if args.execute else 'Would upload'} {len(uploaded)} file(s); {len(skipped)} already present and identical.")
    if args.execute:
        problems = verify(args.local, DeltaTable(f"abfs://{CONTAINER}/datums", storage_options=options()))
        print("verified" if not problems else f"NOT complete: {problems}")
        return 0 if not problems else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
