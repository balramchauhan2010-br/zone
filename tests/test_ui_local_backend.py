from __future__ import annotations

from ui import local_backend


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": "AVAILABLE", "data": []}

    def json(self):
        return self._payload


class _InProcessClient:
    def __init__(self, response=None):
        self.response = response or _Response()
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self.response

    def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return self.response


def test_get_uses_in_process_python_client(monkeypatch):
    client = _InProcessClient()
    monkeypatch.setattr(local_backend, "_client", lambda: client)

    result = local_backend.get("/api/futures", params={"contract": "front"}, timeout=60)

    assert result["status"] == "AVAILABLE"
    assert client.calls == [("GET", "/api/futures", {"contract": "front"})]


def test_http_error_is_reported_without_fabricating_data(monkeypatch):
    client = _InProcessClient(_Response(503, {"detail": "provider unavailable"}))
    monkeypatch.setattr(local_backend, "_client", lambda: client)

    result = local_backend.get("/api/futures")

    assert result["status"] == "UNAVAILABLE"
    assert result["data"] is None
    assert result["reason"] == "provider unavailable"
    assert result["http_status"] == 503


def test_csv_upload_is_sent_to_local_python_client(monkeypatch):
    client = _InProcessClient()
    monkeypatch.setattr(local_backend, "_client", lambda: client)

    result = local_backend.upload_csv(
        "/api/global/zones/upload", "bars.csv", b"timestamp,open,high,low,close,volume\n",
        params={"symbol": "BTCUSD", "timeframe": "5m"},
    )

    assert result["status"] == "AVAILABLE"
    method, path, kwargs = client.calls[0]
    assert (method, path) == ("POST", "/api/global/zones/upload")
    assert kwargs["params"] == {"symbol": "BTCUSD", "timeframe": "5m"}
    assert kwargs["files"]["file"][0] == "bars.csv"
