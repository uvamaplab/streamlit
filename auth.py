import requests
import streamlit as st

import config

# Legacy destination, kept for reference; config.resolve() decides which one this deployment uses.
ROLES_API = config.LEGACY_ROLES_API


def roles_from_api(study: str, token: str, destinations: "config.Destinations | None" = None, http_get=None) -> list[str]:
    """The caller's roles in `study`, from the roles API. Raises on any failure, so callers deny.

    The destination is resolved and validated before the token leaves this process. On a MAPL
    deployment redirects are not followed (a 3xx is a failure) and the call times out.
    """
    destinations = destinations or config.current()
    http_get = http_get or requests.get
    url = destinations.roles_url(study)
    headers = {"Authorization": f"Bearer {token}", "x-portal": "True"}

    if destinations.mode == "mapl":
        resp = http_get(url, headers=headers, timeout=10, allow_redirects=False)
    else:
        resp = http_get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"Authentication failed: {resp.status_code}")

    data = resp.json()
    roles = data.get("roles", []) if isinstance(data, dict) else []
    # Only a list counts: "admin" in "badmin" would otherwise pass for a string.
    return [r for r in roles if isinstance(r, str)] if isinstance(roles, list) else []


@st.cache_data(ttl=300)
def fetch_roles(study: str, token: str) -> list[str]:
    return roles_from_api(study, token)

def set_session_state():
    qp_token = st.query_params.get("token")
    qp_study = st.query_params.get("study")
    
    if ("study" not in st.session_state) or (qp_study and st.session_state.get("study") != qp_study):
        st.session_state["study"] = qp_study
        
    if ("token" not in st.session_state) or (qp_token and st.session_state.get("token") != qp_token):
        st.session_state["token"] = qp_token

def check_access_admin_only() -> bool:
    
    set_session_state()

    token = st.session_state.get("token")
    study = st.session_state.get("study")

    if not token or not study:
        st.error("🚫 Unauthorized: missing token or study.")
        st.stop()

    try:
        roles = fetch_roles(study, token)
    except Exception as e:
        st.error(str(e))
        st.stop()

    if "admin" not in roles:
        st.error("Access Denied: Admin role required.")
        st.stop()

    return True