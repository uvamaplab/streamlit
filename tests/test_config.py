"""config.py: which destinations a deployment may use. Pure logic; contacts nothing."""
import pytest

import config
from config import ConfigurationError, resolve

TEST = {
    "MAPL_ENVIRONMENT": "test",
    "OUTPUTS_ACCOUNT_NAME": "outputsmapltest",
    "ROLES_API_URL": "https://api.test.uvamaplab.com/creator/v1/roles?resource=study:{study}",
}


def test_legacy_deployment_keeps_todays_destinations():
    d = resolve({})
    assert d.mode == "legacy"
    assert d.outputs_account == "trailsoutputs"
    assert d.roles_url("mtm") == "https://digital-trails.org/api/v2.1/roles?resource=study:mtm"


def test_valid_test_configuration():
    d = resolve(TEST)
    assert (d.mode, d.environment, d.outputs_account) == ("mapl", "test", "outputsmapltest")
    assert d.roles_url("maplsynth") == "https://api.test.uvamaplab.com/creator/v1/roles?resource=study:maplsynth"


def test_the_study_is_encoded_into_the_url():
    assert resolve(TEST).roles_url("a&resource=other") == "https://api.test.uvamaplab.com/creator/v1/roles?resource=study:a%26resource%3Dother"


@pytest.mark.parametrize("missing", ["OUTPUTS_ACCOUNT_NAME", "ROLES_API_URL"])
def test_missing_configuration_fails(missing):
    env = dict(TEST)
    del env[missing]
    with pytest.raises(ConfigurationError):
        resolve(env)


@pytest.mark.parametrize("setting", ["OUTPUTS_ACCOUNT_NAME", "ROLES_API_URL"])
def test_settings_without_an_environment_fail_instead_of_running_as_legacy(setting):
    with pytest.raises(ConfigurationError):
        resolve({setting: TEST[setting]})


@pytest.mark.parametrize("env_name", ["dev", "TEST", "staging", " prod2"])
def test_unknown_environment_fails(env_name):
    with pytest.raises(ConfigurationError):
        resolve({**TEST, "MAPL_ENVIRONMENT": env_name})


@pytest.mark.parametrize("account", ["trailsoutputs", "outputsmaplprod", "outputsmapltest2", "OUTPUTSMAPLTEST"])
def test_legacy_or_cross_environment_storage_is_rejected(account):
    with pytest.raises(ConfigurationError):
        resolve({**TEST, "OUTPUTS_ACCOUNT_NAME": account})


@pytest.mark.parametrize("url", [
    "https://digital-trails.org/api/v2.1/roles?resource=study:{study}",            # legacy
    "https://api.uvamaplab.com/creator/v1/roles?resource=study:{study}",           # prod / legacy-fronted
    "http://api.test.uvamaplab.com/roles?resource=study:{study}",                  # not https
    "https://api.test.uvamaplab.com:8443/roles?resource=study:{study}",            # port
    "https://user@api.test.uvamaplab.com/roles?resource=study:{study}",            # user info
    "https://api.test.uvamaplab.com.evil.example/roles?resource=study:{study}",    # suffix trick
    "https://evil.example/api.test.uvamaplab.com/roles?resource=study:{study}",    # host in path
    "https://api.test.uvamaplab.com/roles#{study}",                                # fragment
    "https://api.test.uvamaplab.com\\@evil.example/roles?resource=study:{study}",  # backslash
    "https://api.test.uvamaplab.com/roles",                                        # no {study}
    "https://api.test.uvamaplab.com/{study}/{other}",                              # extra braces
    "https://аpi.test.uvamaplab.com/roles?resource=study:{study}",                 # Cyrillic а
])
def test_unintended_roles_destinations_are_rejected(url):
    with pytest.raises(ConfigurationError):
        resolve({**TEST, "ROLES_API_URL": url})


def test_prod_has_no_approved_roles_host_yet():
    with pytest.raises(ConfigurationError):
        resolve({"MAPL_ENVIRONMENT": "prod", "OUTPUTS_ACCOUNT_NAME": "outputsmaplprod",
                 "ROLES_API_URL": "https://api.test.uvamaplab.com/roles?resource=study:{study}"})


@pytest.mark.parametrize("study", ["mtm", "maplsynth-test", "a12"])
def test_valid_study_containers(study):
    assert config.check_study(study) == study


@pytest.mark.parametrize("study", ["", "x", "a1", "MTM", "a/b", "../mtm", "a--b", "-mtm", "mtm-", None, "a" * 64])
def test_invalid_study_containers(study):
    with pytest.raises(ConfigurationError):
        config.check_study(study)
