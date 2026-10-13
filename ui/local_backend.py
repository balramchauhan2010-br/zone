"""In-process Python backend calls for the single-app Streamlit deployment.

The dashboard calls the existing Python/FastAPI route handlers through an
in-process ASGI test client. No second web service, public API URL, Render
service, or localhost network connection is required.
"""
from __future__ import annotations

import atexit
import threading
from typing import Any

from ui.runtime_config import apply_streamlit_secrets

_CLIENT = None
_CLIENT_LOCK = threading.RLock()
_REQUEST_LOCK = threading.RLock()


def _close_client() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        client, _CLIENT = _CLIENT, None
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except Exception:
                pass


def _client():
    """Create one long-lived, in-process client shared by Streamlit sessions."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            # Secrets must be read before importing terminal.config or api.main,
            # both of which snapshot provider configuration at import time.
            apply_streamlit_secrets()
            from fastapi.testclient import TestClient
            from api.main import app

            client = TestClient(app, raise_server_exceptions=False)
            client.__enter__()  # Runs the app lifespan once for this process.
            _CLIENT = client
            atexit.register(_close_client)
        return _CLIENT


def _unavailable(reason: str, http_status: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "UNAVAILABLE",
        "data": None,
        "source": None,
        "source_timestamp": None,
        "received_at": None,
        "data_age_seconds": None,
        "reason": reason,
    }
    if http_status is not None:
        result["http_status"] = http_status
    return result


def _decode(response) -> dict[str, Any]:
    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail") if isinstance(payload, dict) else None
        return _unavailable(str(detail or f"Local Python request failed with HTTP {response.status_code}"), response.status_code)
    try:
        payload = response.json()
    except ValueError:
        return _unavailable("Local Python backend returned an invalid response.")
    return payload if isinstance(payload, dict) else _unavailable("Local Python backend returned an unexpected response format.")


def get(path: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """Call a read-only Python endpoint in-process; timeout kept for UI compatibility."""
    del timeout  # In-process calls are governed by provider request timeouts.
    try:
        with _REQUEST_LOCK:
            response = _client().get(path, params=params)
        return _decode(response)
    except Exception as exc:
        return _unavailable(f"In-process Python backend could not complete this request ({type(exc).__name__}).")


def post(path: str, body: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    """Submit a Python operation in-process; no external HTTP request is made."""
    del timeout
    headers: dict[str, str] = {}
    if path.startswith("/api/ai/"):
        apply_streamlit_secrets()
        import os
        headers["X-Admin-Token"] = os.getenv("APP_ADMIN_TOKEN", "")
    try:
        with _REQUEST_LOCK:
            response = _client().post(path, json=body, headers=headers)
        return _decode(response)
    except Exception as exc:
        return _unavailable(f"In-process Python backend could not complete this request ({type(exc).__name__}).")


def upload_csv(
    path: str,
    file_name: str,
    content: bytes,
    params: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Pass a CSV upload to the local Python handler without a remote API."""
    del timeout
    try:
        with _REQUEST_LOCK:
            response = _client().post(
                path,
                params=params,
                files={"file": (file_name, content, "text/csv")},
            )
        return _decode(response)
    except Exception as exc:
        return _unavailable(f"Local CSV processing failed ({type(exc).__name__}).")
