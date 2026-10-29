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

## Tests

```
pip install -r requirements.txt pytest
python -m pytest tests
```

The tests use fakes only: no request leaves the machine and no data is read.

## Building an image without deploying

`deploy.yml` builds and deploys to the legacy app on every push to `main`, so a migration build is
made by hand from a branch:

```
docker build -t streamlit:<commit> .
```

Import or push it to the target registry under a new tag, and pin the deployment by the digest the
registry reports (`az acr manifest show-metadata -r <registry> -n streamlit:<tag> --query digest`).
Never retag or overwrite an image a running app uses.
