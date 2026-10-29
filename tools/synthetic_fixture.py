"""Synthetic dashboard-test input: a local Delta table shaped like the datums tables the writer makes.

DASHBOARD-TEST INPUT, NOT INGESTION EVIDENCE. Every identifier and value is invented; nothing is
read from or copied out of participant data.

Schema and table properties match trailsfunctionspy's writer (blueprints/datums.py
_DATUMS_ARROW_SCHEMA and blueprints/delta.py _create_table): ts float64, tz, pid, did, type, data (a
JSON string), and the lineage fields ehp and ehseq. The table carries
`maplab.fixture = dashboard-test-input`, which a future upload checks before touching anything.

Records are chosen so the Events page shows something real: for three synthetic participants an
Invited, a Link (whose data.Code becomes the linking code), a Consent, an Enrolled, one completed and
one unfinished survey flow, plus a server-style Consented and an unrelated Ping that the page ignores.

    python -m tools.synthetic_fixture --out <directory outside this repository>
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pyarrow as pa

FIXTURE_PROPERTY = "maplab.fixture"
FIXTURE_VALUE = "dashboard-test-input"

SCHEMA = pa.schema([
    pa.field("ts", pa.float64(), nullable=False),
    pa.field("tz", pa.string()),
    pa.field("pid", pa.string(), nullable=False),
    pa.field("did", pa.string()),
    pa.field("type", pa.string(), nullable=False),
    pa.field("data", pa.string()),
    pa.field("ehp", pa.string()),
    pa.field("ehseq", pa.int64()),
])

# The writer's table configuration (blueprints/delta.py), plus the fixture marker.
TABLE_CONFIGURATION = {
    "delta.checkpointPolicy": "classic",
    "delta.appendOnly": "true",
    "delta.logRetentionDuration": "interval 7 days",
    "delta.deletedFileRetentionDuration": "interval 7 days",
    "delta.targetFileSize": str(32 * 1024 * 1024),
    FIXTURE_PROPERTY: FIXTURE_VALUE,
}

PARTICIPANTS = [
    ("user_synthetic_0001", "901"),
    ("user_synthetic_0002", "902"),
    ("user_synthetic_0003", "903"),
]
TZ = "-04:00"
START = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc).timestamp()
DAY = 86400.0


def build_rows():
    """Deterministic rows: the same table every time."""
    rows = []
    seq = 0

    def add(pid, offset_days, kind, data=None):
        nonlocal seq
        seq += 1
        rows.append({
            "ts": START + offset_days * DAY + seq,
            "tz": TZ,
            "pid": pid,
            "did": f"synthetic-device-{pid[-2:]}",
            "type": kind,
            "data": json.dumps(data) if data is not None else None,
            "ehp": "0",
            "ehseq": seq,
        })

    for n, (pid, code) in enumerate(PARTICIPANTS):
        add(pid, n, "Invited")
        add(pid, n, "Link", {"Code": code})
        add(pid, n + 0.1, "Consent", {})
        add(pid, n + 0.1, "Consented", {})        # what the server writes; the page does not read it
        add(pid, n + 0.2, "Enrolled", {})

        done = f"synthetic-flow-{pid[-4:]}-daily"
        add(pid, n + 1, "Flow", {"flow_id": done, "name": "$RootPath", "value": "flows/daily-checkin.json"})
        add(pid, n + 1, "Flow", {"flow_id": done, "name": "mood", "value": str(3 + n)})
        add(pid, n + 1, "Flow", {"flow_id": done, "name": "sleep_hours", "value": str(6 + n)})
        add(pid, n + 1, "Flow", {"flow_id": done, "name": "$Ending", "value": "Completion"})

        partial = f"synthetic-flow-{pid[-4:]}-weekly"
        add(pid, n + 2, "Flow", {"flow_id": partial, "name": "$RootPath", "value": "flows/weekly-survey.json"})
        add(pid, n + 2, "Flow", {"flow_id": partial, "name": "stress", "value": "2"})

        add(pid, n + 2, "Ping", {"synthetic": True})  # a type the Events page ignores
    return rows


def table():
    return pa.Table.from_pylist(build_rows(), schema=SCHEMA)


def _inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def write(out_dir, repo_root=None):
    """Writes the fixture to `out_dir`/datums. Refuses a path inside the repository, and refuses to
    write over anything that is not already this fixture."""
    from deltalake import DeltaTable, write_deltalake

    repo_root = repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _inside(out_dir, repo_root):
        raise SystemExit("Refusing to write inside the repository (or its container build context).")

    uri = os.path.join(out_dir, "datums")
    if os.path.exists(uri) and os.listdir(uri):
        existing = DeltaTable(uri).metadata().configuration
        if existing.get(FIXTURE_PROPERTY) != FIXTURE_VALUE:
            raise SystemExit(f"{uri} exists and is not a synthetic fixture; not touching it.")
        return uri  # already the fixture; writing it again would duplicate rows

    os.makedirs(uri, exist_ok=True)
    # raise_if_key_not_exists=False lets the custom maplab.fixture marker through.
    DeltaTable.create(uri, SCHEMA, configuration=TABLE_CONFIGURATION, raise_if_key_not_exists=False)
    write_deltalake(uri, table(), mode="append")
    return uri


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="directory outside this repository")
    args = parser.parse_args(argv)
    uri = write(args.out)
    print(f"Synthetic dashboard-test fixture at {uri} ({len(build_rows())} rows).")


if __name__ == "__main__":
    sys.exit(main())
