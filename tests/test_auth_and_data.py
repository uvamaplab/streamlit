"""auth.py and utils.py with fakes: no request leaves the process and no storage is read."""
import socket

import pytest

import auth
import config
import utils

TEST_ENV = {
    "MAPL_ENVIRONMENT": "test",
    "OUTPUTS_ACCOUNT_NAME": "outputsmapltest",
    "ROLES_API_URL": "https://api.test.uvamaplab.com/creator/v1/roles?resource=study:{study}",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("A test tried to open a network connection.")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    for name in ("MAPL_ENVIRONMENT", "OUTPUTS_ACCOUNT_NAME", "ROLES_API_URL"):
        monkeypatch.delenv(name, raising=False)


def use(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)


class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Recorder:
    def __init__(self, *responses):
        self.calls, self._responses = [], list(responses)

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


@pytest.mark.parametrize("roles", [["admin"], ["viewer", "admin"], []])
def test_a_list_of_strings_is_returned_as_is(monkeypatch, roles):
    use(monkeypatch, TEST_ENV)
    assert auth.roles_from_api("maplsynth", "synthetic-token", http_get=Recorder(Response(200, {"roles": roles}))) == roles


@pytest.mark.parametrize("body", [{"roles": ["admin", 123]}, {"roles": ["admin", None]}, {"roles": ["admin", ["admin"]]}])
def test_a_mixed_type_list_grants_nothing(monkeypatch, body):
    use(monkeypatch, TEST_ENV)
    assert auth.roles_from_api("maplsynth", "synthetic-token", http_get=Recorder(Response(200, body))) == []


def test_test_deployment_calls_only_its_approved_roles_api(monkeypatch):
    use(monkeypatch, TEST_ENV)
    http = Recorder(Response(200, {"roles": ["admin"]}))
    assert auth.roles_from_api("maplsynth", "synthetic-token", http_get=http) == ["admin"]
    url, kwargs = http.calls[0]
    assert url == "https://api.test.uvamaplab.com/creator/v1/roles?resource=study:maplsynth"
    assert kwargs["allow_redirects"] is False and kwargs["timeout"] == 10
    assert kwargs["headers"] == {"Authorization": "Bearer synthetic-token", "x-portal": "True"}


def test_legacy_behaviour_is_unchanged(monkeypatch):
    http = Recorder(Response(200, {"roles": ["admin", "viewer"]}))
    assert auth.roles_from_api("mtm", "synthetic-token", http_get=http) == ["admin", "viewer"]
    url, kwargs = http.calls[0]
    assert url == "https://digital-trails.org/api/v2.1/roles?resource=study:mtm"
    assert kwargs == {"headers": {"Authorization": "Bearer synthetic-token", "x-portal": "True"}}


@pytest.mark.parametrize("env", [
    {**TEST_ENV, "ROLES_API_URL": "https://digital-trails.org/api/v2.1/roles?resource=study:{study}"},
    {**TEST_ENV, "ROLES_API_URL": "https://api.uvamaplab.com/roles?resource=study:{study}"},
    {k: v for k, v in TEST_ENV.items() if k != "ROLES_API_URL"},
    {"ROLES_API_URL": TEST_ENV["ROLES_API_URL"]},
])
def test_a_rejected_destination_never_receives_the_token(monkeypatch, env):
    use(monkeypatch, env)
    http = Recorder()
    with pytest.raises(config.ConfigurationError):
        auth.roles_from_api("maplsynth", "synthetic-token", http_get=http)
    assert http.calls == []


@pytest.mark.parametrize("response", [
    Response(500, {}), Response(401, {}), Response(302, {}), Response(200, {"roles": "admin-ish"}),
    Response(200, {"roles": None}), Response(200, ["admin"]),
    Response(200, {"roles": ["admin", 123]}), Response(200, {"roles": ["admin", None]}),
    Response(200, {"roles": ["admin", ["admin"]]}), Response(200, {"roles": [{"admin": True}]}),
    Response(200, {}),
])
def test_unavailable_or_malformed_roles_deny(monkeypatch, response):
    use(monkeypatch, TEST_ENV)
    try:
        roles = auth.roles_from_api("maplsynth", "synthetic-token", http_get=Recorder(response))
    except Exception:
        return  # raised: check_access_admin_only shows an error and stops
    assert "admin" not in roles


class Stop(Exception):
    pass


class FakeStreamlit:
    def __init__(self, params):
        self.query_params, self.session_state, self.errors = params, {}, []

    def error(self, message):
        self.errors.append(message)

    def stop(self):
        raise Stop()


@pytest.mark.parametrize("roles", [Exception("Authentication failed: 503"), config.ConfigurationError("no destination"), ["viewer"], []])
def test_access_is_denied_unless_the_lookup_returns_admin(monkeypatch, roles):
    fake = FakeStreamlit({"token": "synthetic-token", "study": "maplsynth"})
    monkeypatch.setattr(auth, "st", fake)

    def lookup(study, token):
        if isinstance(roles, Exception):
            raise roles
        return roles
    monkeypatch.setattr(auth, "fetch_roles", lookup)

    with pytest.raises(Stop):
        auth.check_access_admin_only()
    assert all("synthetic-token" not in e for e in fake.errors)


def test_access_is_granted_for_admin(monkeypatch):
    monkeypatch.setattr(auth, "st", FakeStreamlit({"token": "synthetic-token", "study": "maplsynth"}))
    monkeypatch.setattr(auth, "fetch_roles", lambda study, token: ["admin"])
    assert auth.check_access_admin_only() is True


class FakeCredential:
    calls = 0

    def __init__(self, *a, **k):
        pass

    def get_token(self, scope):
        FakeCredential.calls += 1
        return type("T", (), {"token": "synthetic-storage-token"})()


def test_data_reads_from_the_test_account(monkeypatch):
    use(monkeypatch, TEST_ENV)
    seen = {}

    class FakeTable:
        def __init__(self, uri, storage_options):
            seen["uri"], seen["account"] = uri, storage_options["ACCOUNT_NAME"]
            raise RuntimeError("stop here: no data is read in tests")
    monkeypatch.setattr(utils, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(utils, "DeltaTable", FakeTable)

    with pytest.raises(RuntimeError, match="no data"):
        utils.load_datums("maplsynth-test")
    assert seen == {"uri": "abfs://maplsynth-test/datums", "account": "outputsmapltest"}


@pytest.mark.parametrize("env, study", [
    ({**TEST_ENV, "OUTPUTS_ACCOUNT_NAME": "trailsoutputs"}, "mtm"),
    ({**TEST_ENV, "OUTPUTS_ACCOUNT_NAME": "outputsmaplprod"}, "mtm"),
    (TEST_ENV, "../mtm"),
])
def test_rejected_storage_never_mints_a_credential(monkeypatch, env, study):
    use(monkeypatch, env)
    FakeCredential.calls = 0
    monkeypatch.setattr(utils, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(utils, "DeltaTable", lambda *a, **k: pytest.fail("storage must not be opened"))
    with pytest.raises(config.ConfigurationError):
        utils.load_datums(study)
    assert FakeCredential.calls == 0


def test_legacy_data_account_is_unchanged(monkeypatch):
    seen = {}

    class FakeTable:
        def __init__(self, uri, storage_options):
            seen["account"] = storage_options["ACCOUNT_NAME"]
            raise RuntimeError("stop")
    monkeypatch.setattr(utils, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(utils, "DeltaTable", FakeTable)
    with pytest.raises(RuntimeError):
        utils.load_datums("mtm")
    assert seen["account"] == "trailsoutputs"
