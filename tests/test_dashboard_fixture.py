"""The Events page against the synthetic fixture, through its real authorization and data paths.

Simulated authorization: the roles API is replaced by a fake `requests.get` inside these tests only.
That is not a live integration check, and nothing in the application has a bypass or "always admin"
mode. Storage is the local synthetic Delta table, opened in place of the abfs URI the page asks for.
"""
import os
import socket

import pytest
import streamlit as st
from deltalake import DeltaTable
from streamlit.testing.v1 import AppTest

import auth
import utils
from tools import synthetic_fixture

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(REPO, "pages", "events.py")
TEST_ENV = {
    "MAPL_ENVIRONMENT": "test",
    "OUTPUTS_ACCOUNT_NAME": "outputsmapltest",
    "ROLES_API_URL": "https://api.test.uvamaplab.com/creator/v1/roles?resource=study:{study}",
}


class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("A test tried to open a network connection.")
    monkeypatch.setattr(socket, "create_connection", refuse)
    for k in ("MAPL_ENVIRONMENT", "OUTPUTS_ACCOUNT_NAME", "ROLES_API_URL"):
        monkeypatch.delenv(k, raising=False)
    st.cache_data.clear()
    yield
    st.cache_data.clear()


@pytest.fixture(scope="session")
def fixture_table(tmp_path_factory):
    return synthetic_fixture.write(str(tmp_path_factory.mktemp("synthetic")), repo_root=REPO)


@pytest.fixture(scope="session")
def empty_table(tmp_path_factory):
    uri = os.path.join(str(tmp_path_factory.mktemp("empty")), "datums")
    os.makedirs(uri)
    DeltaTable.create(uri, synthetic_fixture.SCHEMA)
    return uri


class Storage:
    """Stands in for DeltaTable: records the URI and account asked for, serves a local table."""

    def __init__(self, local_uri):
        self.local_uri, self.opened = local_uri, []

    def __call__(self, uri, storage_options):
        self.opened.append((uri, storage_options["ACCOUNT_NAME"]))
        return DeltaTable(self.local_uri)


class Credential:
    def __init__(self, *a, **k):
        pass

    def get_token(self, scope):
        return type("T", (), {"token": "synthetic-storage-token"})()


def run_events(monkeypatch, env, roles_response, table_uri, token="synthetic-portal-token", study="maplsynth"):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(roles_response, Exception):
            raise roles_response
        return roles_response
    monkeypatch.setattr(auth.requests, "get", fake_get)
    storage = Storage(table_uri)
    monkeypatch.setattr(utils, "DeltaTable", storage)
    monkeypatch.setattr(utils, "DefaultAzureCredential", Credential)

    at = AppTest.from_file(EVENTS, default_timeout=60)
    if token is not None:
        at.query_params["token"] = token
    if study is not None:
        at.query_params["study"] = study
    at.run()
    return at, calls, storage


def errors(at):
    return [e.value for e in at.error]


def test_fixture_matches_the_writer_schema_and_is_marked(fixture_table):
    dt = DeltaTable(fixture_table)
    assert dt.schema().to_arrow() == synthetic_fixture.SCHEMA
    assert dt.metadata().configuration[synthetic_fixture.FIXTURE_PROPERTY] == synthetic_fixture.FIXTURE_VALUE
    assert dt.to_pyarrow_table().num_rows == len(synthetic_fixture.build_rows())


def test_the_dashboard_transformations_consume_the_fixture(monkeypatch, fixture_table):
    for k, v in TEST_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(utils, "DeltaTable", Storage(fixture_table))
    monkeypatch.setattr(utils, "DefaultAzureCredential", Credential)

    df = utils.load_datums("maplsynth")
    assert set(df["linking_code"]) == {"901", "902", "903"}

    flows = utils.completed_flow_values(df, only_completed=False)
    assert set(flows["flow_name"]) == {"daily-checkin", "weekly-survey"}
    completed = utils.completed_flow_values(df)
    assert set(completed["flow_name"]) == {"daily-checkin"} and set(completed["name"]) == {"mood", "sleep_hours"}

    assert len(utils.consents_as_events(df)) == 3      # "Consent" rows; the 3 "Consented" rows are not read
    assert len(utils.invites_as_events(df)) == 3
    assert len(utils.enrolled_as_events(df)) == 3


def test_authorized_synthetic_admin_sees_events(monkeypatch, fixture_table):
    at, calls, storage = run_events(monkeypatch, TEST_ENV, Response(200, {"roles": ["admin"]}), fixture_table)
    assert not at.exception and not errors(at)
    assert at.title[0].value == "Events"
    assert not [t for t in at.text if t.value == "No data is available"]
    assert calls[0][0] == "https://api.test.uvamaplab.com/creator/v1/roles?resource=study:maplsynth"
    assert storage.opened == [("abfs://maplsynth/datums", "outputsmapltest")]


def test_mapl_removes_the_token_from_the_url(monkeypatch, fixture_table):
    at, _, _ = run_events(monkeypatch, TEST_ENV, Response(200, {"roles": ["admin"]}), fixture_table)
    assert "token" not in at.query_params
    assert at.query_params["study"] == ["maplsynth"] or at.query_params["study"] == "maplsynth"


def test_legacy_keeps_the_url_unchanged(monkeypatch, fixture_table):
    at, _, _ = run_events(monkeypatch, {}, Response(200, {"roles": ["admin"]}), fixture_table, study="mtm")
    assert "token" in at.query_params


def test_empty_data_says_so(monkeypatch, empty_table):
    at, _, storage = run_events(monkeypatch, TEST_ENV, Response(200, {"roles": ["admin"]}), empty_table)
    assert storage.opened
    assert not at.exception and not errors(at)
    assert [t.value for t in at.text] == ["No data is available"]


@pytest.mark.parametrize("name, kwargs", [
    ("missing token", dict(token=None)),
    ("missing study", dict(study=None)),
    ("non-admin", dict(roles_response=Response(200, {"roles": ["viewer"]}))),
    ("roles service failure", dict(roles_response=Response(503, {}))),
    ("roles redirect", dict(roles_response=Response(302, {}))),
    ("malformed roles", dict(roles_response=Response(200, {"roles": "admin"}))),
    ("mixed-type roles", dict(roles_response=Response(200, {"roles": ["admin", 123]}))),
    ("roles not an object", dict(roles_response=Response(200, ["admin"]))),
    ("roles unreachable", dict(roles_response=ConnectionError("unreachable"))),
])
def test_denied_requests_never_reach_storage(monkeypatch, fixture_table, name, kwargs):
    args = dict(env=TEST_ENV, roles_response=Response(200, {"roles": ["admin"]}), table_uri=fixture_table)
    args.update(kwargs)
    at, _, storage = run_events(monkeypatch, **args)
    assert errors(at), name
    assert storage.opened == [], name
    assert not at.title, name   # stopped before the page rendered


@pytest.mark.parametrize("env", [
    {"MAPL_ENVIRONMENT": "test"},
    {**TEST_ENV, "OUTPUTS_ACCOUNT_NAME": "trailsoutputs"},
    {**TEST_ENV, "ROLES_API_URL": "https://digital-trails.org/api/v2.1/roles?resource=study:{study}"},
])
def test_bad_mapl_configuration_denies_before_any_call(monkeypatch, fixture_table, env):
    at, calls, storage = run_events(monkeypatch, env, Response(200, {"roles": ["admin"]}), fixture_table)
    assert errors(at)
    assert storage.opened == []
    if "ROLES_API_URL" not in env or "digital-trails" in env.get("ROLES_API_URL", ""):
        assert calls == []


def test_legacy_configuration_reaches_only_legacy_destinations(monkeypatch, fixture_table):
    at, calls, storage = run_events(monkeypatch, {}, Response(200, {"roles": ["admin"]}), fixture_table, study="mtm")
    assert calls[0][0] == "https://digital-trails.org/api/v2.1/roles?resource=study:mtm"
    assert storage.opened == [("abfs://mtm/datums", "trailsoutputs")]


def test_the_generator_refuses_the_repository_and_foreign_tables(tmp_path):
    with pytest.raises(SystemExit):
        synthetic_fixture.write(os.path.join(REPO, "synthetic-out"), repo_root=REPO)
    foreign = tmp_path / "datums"
    foreign.mkdir()
    DeltaTable.create(str(foreign), synthetic_fixture.SCHEMA)
    with pytest.raises(SystemExit):
        synthetic_fixture.write(str(tmp_path), repo_root=REPO)


def test_writing_twice_is_idempotent(tmp_path):
    first = synthetic_fixture.write(str(tmp_path), repo_root=REPO)
    synthetic_fixture.write(str(tmp_path), repo_root=REPO)
    assert DeltaTable(first).to_pyarrow_table().num_rows == len(synthetic_fixture.build_rows())
