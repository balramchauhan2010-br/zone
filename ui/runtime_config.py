"""Load Streamlit Cloud secrets into server-side env vars before config imports.

Only the Python process sees these values; this module never renders or logs them.
"""
from __future__ import annotations

import os

_SECRET_NAMES = (
    "DHAN_CLIENT_ID",
    "DHAN_ACCESS_TOKEN",
    "DHAN_BASE_URL",
    "DHAN_MASTER_URL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "APP_ADMIN_TOKEN",
    "YAHOO_RESEARCH_ENABLED",
)


def apply_streamlit_secrets() -> None:
    """Use Streamlit Secrets as process config, without overriding real env vars."""
    try:
        import streamlit as st
        secrets = st.secrets
    except Exception:
        # Local unit tests and command-line use may not have Streamlit installed
        # or a secrets.toml file configured.
        return

    for name in _SECRET_NAMES:
        if os.getenv(name, "").strip():
            continue
        try:
            value = secrets.get(name, "")
        except Exception:
            value = ""
        if value is not None and str(value).strip():
            os.environ[name] = str(value).strip()
