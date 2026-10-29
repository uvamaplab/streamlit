"""Where the dashboard reads data and checks roles.

LEGACY (the existing trailscontainerapp, which sets none of the settings below): exactly today's
destinations, the trailsoutputs account and https://digital-trails.org/api/v2.1/roles. Unchanged.

MAPL (MAPL_ENVIRONMENT=test or prod): every destination comes from settings and must belong to that
environment, or the call fails before any token is minted or sent and before any storage read. A MAPL
deployment never falls back to a legacy destination.

  OUTPUTS_ACCOUNT_NAME   must be exactly outputsmapl{env}
  ROLES_API_URL          https URL template containing {study}; its host must be the environment's
                         approved API host (test: api.test.uvamaplab.com). Prod has no approved host
                         yet (api.uvamaplab.com still fronts legacy), so prod refuses every value.

Setting OUTPUTS_ACCOUNT_NAME or ROLES_API_URL without MAPL_ENVIRONMENT is an error too, so a
half-configured deployment cannot quietly run with legacy destinations.
"""
import os
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Optional
from urllib.parse import quote, urlsplit

LEGACY_ACCOUNT = "trailsoutputs"
LEGACY_ROLES_API = "https://digital-trails.org/api/v2.1/roles?resource=study:{study}"

# Roles API hosts approved per MAPL environment. None: nothing approved yet, every value refused.
APPROVED_ROLES_HOSTS: Mapping[str, Optional[str]] = {
    "test": "api.test.uvamaplab.com",
    "prod": None,
}

# Azure blob container names; a study is used as the container, so it must be one.
_CONTAINER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")


class ConfigurationError(RuntimeError):
    """A destination is missing or not allowed for this deployment. Nothing was contacted."""


@dataclass(frozen=True)
class Destinations:
    mode: str                 # "legacy" or "mapl"
    environment: Optional[str]
    outputs_account: str
    roles_api_template: str

    def roles_url(self, study: str) -> str:
        """The roles URL for a study, with the study percent-encoded into its place."""
        return self.roles_api_template.format(study=quote(study, safe=""))


def resolve(environ: Mapping[str, str] = os.environ) -> Destinations:
    """This deployment's destinations. Raises ConfigurationError rather than guess."""
    env = (environ.get("MAPL_ENVIRONMENT") or "").strip()
    account = (environ.get("OUTPUTS_ACCOUNT_NAME") or "").strip()
    roles = (environ.get("ROLES_API_URL") or "").strip()

    if not env:
        if account or roles:
            raise ConfigurationError("OUTPUTS_ACCOUNT_NAME or ROLES_API_URL is set without MAPL_ENVIRONMENT.")
        return Destinations("legacy", None, LEGACY_ACCOUNT, LEGACY_ROLES_API)

    if env not in APPROVED_ROLES_HOSTS:
        raise ConfigurationError(f"MAPL_ENVIRONMENT must be one of {sorted(APPROVED_ROLES_HOSTS)}.")

    expected_account = f"outputsmapl{env}"
    if account != expected_account:
        raise ConfigurationError(f"OUTPUTS_ACCOUNT_NAME must be {expected_account} for MAPL_ENVIRONMENT={env}.")

    _check_roles_template(roles, env)
    return Destinations("mapl", env, account, roles)


def _check_roles_template(template: str, env: str) -> None:
    approved = APPROVED_ROLES_HOSTS[env]
    if approved is None:
        raise ConfigurationError(f"No roles API is approved for MAPL_ENVIRONMENT={env} yet.")
    if not template:
        raise ConfigurationError("ROLES_API_URL is not set.")
    if "{study}" not in template or template.count("{") != 1 or template.count("}") != 1:
        raise ConfigurationError("ROLES_API_URL must contain {study} exactly once and no other braces.")
    if not template.isascii() or any(c.isspace() or c in "\\@#" for c in template):
        raise ConfigurationError("ROLES_API_URL contains characters that are not allowed.")
    try:
        parts = urlsplit(template.replace("{study}", "study"))
        port = parts.port
    except ValueError:
        raise ConfigurationError("ROLES_API_URL is not a valid URL.") from None
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        raise ConfigurationError("ROLES_API_URL must be https on port 443 with no user info.")
    if (parts.hostname or "") != approved:
        raise ConfigurationError(f"ROLES_API_URL must use the approved host for {env}.")


def check_study(study: str) -> str:
    """The study as a container name, or ConfigurationError. Checked before any storage call."""
    if not isinstance(study, str) or not _CONTAINER.match(study) or "--" in study:
        raise ConfigurationError("The study is not a valid container name.")
    return study


def current(environ_getter: Callable[[], Mapping[str, str]] = lambda: os.environ) -> Destinations:
    """resolve() for the running process; read on each call so tests and restarts see changes."""
    return resolve(environ_getter())
