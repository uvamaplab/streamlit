# streamlit

## Where it reads data and checks roles (`config.py`)

- **Legacy (the existing `trailscontainerapp`):** set nothing. Data comes from the `trailsoutputs`
  account and roles from `https://digital-trails.org/api/v2.1/roles?resource=study:{study}`, exactly
  as before.
- **MAPL (`mapl-test`, later `mapl-prod`):** set all three, or every page stops with a configuration
  error before any token or storage call:
  - `MAPL_ENVIRONMENT`: `test` or `prod`
  - `OUTPUTS_ACCOUNT_NAME`: must be `outputsmapl{env}`
  - `ROLES_API_URL`: an https URL template containing `{study}`, on the environment's approved host
    (test: `api.test.uvamaplab.com`; prod has none yet)

  A legacy or other-environment destination is refused, and a MAPL deployment never falls back to
  legacy. Redirects from the roles API are not followed. Storage access stays on the app's managed
  identity.

The roles API must answer `200` with `{"roles": [...]}` for the caller's bearer token and
`x-portal: True`; pages require `admin`. Any other answer denies access.

## Intentional differences from the legacy dashboard

- A study id that is not a valid container name is refused before storage is touched, and the
  study is percent-encoded into the roles URL (identical for valid study ids).
- **Authorization correction:** a roles answer grants something only when `roles` is a list made
  entirely of strings. A string (`"admin-x"` would satisfy `"admin" in roles`), a missing field, or a
  list with any non-string item (`["admin", 123]`) is malformed and grants nothing. Legacy accepted
  all of these.
- An empty datums table now returns an empty frame, so a page shows "No data is available"; legacy
  raised "Columns must be same length as key".
- MAPL role lookups time out after 10 s and do not follow redirects; legacy lookups are unchanged.

## Synthetic dashboard-test input (`tools/synthetic_fixture.py`)

**Dashboard-test input, not ingestion evidence.** All identifiers and values are invented.

    python -m tools.synthetic_fixture --out <directory outside this repository>

Writes `<out>/datums`, a Delta table with the writer's schema and table properties (ts float64,
tz, pid, did, type, data as a JSON string, ehp, ehseq; append-only, 7-day retention) plus the marker
property `maplab.fixture = dashboard-test-input`. Three participants (`user_synthetic_0001`-`0003`,
linking codes 901-903), each with Invited, Link, Consent, Consented, Enrolled, a completed
`daily-checkin` flow, an unfinished `weekly-survey` flow and a Ping. It refuses to write inside this
repository and never writes over a table that lacks the marker; running it again is a no-op.
`tools/` and `synthetic-out/` are excluded from the image.

Note: the Events page counts `Consent` rows. The server writes `Consented`, which the page does not
read; the fixture has both, to show that.

**Study id versus container.** The dashboard uses the `study` query parameter twice: as the study in
the roles lookup (`resource=study:{study}`) and as the storage container (`abfs://{study}/datums`).
How the writer picks a container: a datum sent with a `code` goes to the container that code's
document resolves to, `{study}` when its env is `prod` and `{study}-{env}` otherwise; a datum sent
without a code goes to the `study` value it carries, lowercased, as is. Which resource group or
stack the writer runs in adds no suffix. So `maplsynth` and `maplsynth-test` are different
containers, and the dashboard reads whichever container equals the `study` it is given.

**This dashboard test is independently seeded.** The fixture goes to `outputsmapltest`, container
`maplsynth`, path `datums/`, authorized as `study:maplsynth`. It is not the parked ingestion trial
(whose datum used `study=maplsynth-test` and would land in container `maplsynth-test`), and it is not
an ingestion-to-dashboard test.

**Upload (prepared, not run): `tools/upload_fixture.py`.** Fixed target `outputsmapltest/maplsynth/datums/`,
operator Entra identity, never account keys. It refuses a non-fixture local table, any blob in the
container outside `datums/`, and an existing `datums/` table without the fixture marker. Uploads are
create-only, data files before `_delta_log`, so an interrupted run leaves no readable partial table and
a rerun skips identical files and stops on different ones. After uploading it reads the remote table
back and compares version, schema, marker, row count and participants; the marker alone never counts
as complete. The container is created separately.

**Role document (prepared, not run): `tools/create_role_document.py`.** One create-only document,
`{"id": "study:maplsynth", "pid": "<user id>", "roles": ["admin"], "synthetic": true}`, in
`cosmosmapltest/cosmicworks/roles_old` (partition key `/pid`), for the Clerk user id of the real
development-portal test user, supplied explicitly. An existing document is refused (409), never
replaced.

## Tests

```
pip install -r requirements.txt pytest
python -m pytest tests
```

The tests use fakes only: no request leaves the machine and no data is read. `test_dashboard_fixture`
runs the Events page itself (Streamlit's AppTest) against the synthetic table, with the roles API
replaced inside the test: simulated authorization, not a live integration check.

## Building an image without deploying

`deploy.yml` builds and deploys to the legacy app on every push to `main`, so a migration build is
made by hand from a branch:

```
docker build -t streamlit:<commit> .
```

Import or push it to the target registry under a new tag, and pin the deployment by the digest the
registry reports (`az acr manifest show-metadata -r <registry> -n streamlit:<tag> --query digest`).
Never retag or overwrite an image a running app uses.
