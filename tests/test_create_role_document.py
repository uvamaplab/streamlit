"""tools/create_role_document.py with a fake HTTP post. Nothing reaches Cosmos."""
import json

import pytest

from tools import create_role_document as crd

PID = "user_2SyntheticPortalUser01"


def test_the_document_matches_the_roles_route_contract():
    assert crd.document(PID) == {"id": "study:maplsynth", "pid": PID, "roles": ["admin"], "synthetic": True}


@pytest.mark.parametrize("pid", ["", None, "someone", "user_", "user_bad id", "user_x/../y", "user_" + "a" * 80])
def test_a_user_id_must_be_supplied_and_well_formed(pid):
    with pytest.raises(SystemExit):
        crd.document(pid)


def test_create_is_create_only_and_partitioned_by_the_user():
    seen = {}

    def post(url, headers, body):
        seen.update(url=url, headers=headers, body=json.loads(body))
        return 201, None
    assert crd.create(PID, "synthetic-token", post) == 201
    assert seen["url"] == "https://cosmosmapltest.documents.azure.com/dbs/cosmicworks/colls/roles_old/docs"
    assert seen["headers"]["x-ms-documentdb-partitionkey"] == json.dumps([PID])
    assert "x-ms-documentdb-is-upsert" not in seen["headers"]
    assert seen["headers"]["Authorization"].startswith("type=aad&")


def test_an_existing_document_is_not_overwritten():
    with pytest.raises(SystemExit, match="not overwriting"):
        crd.create(PID, "synthetic-token", lambda url, headers, body: (409, None))


def test_other_failures_stop():
    with pytest.raises(SystemExit, match="HTTP 403"):
        crd.create(PID, "synthetic-token", lambda url, headers, body: (403, None))
