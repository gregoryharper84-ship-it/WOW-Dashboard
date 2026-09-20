from __future__ import annotations

import io
from urllib.error import HTTPError

from v17 import market_evidence_sources as sources


class _Response:
    status = 200

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def test_canonical_rundown_defaults_are_product_v2_header_auth():
    provider = sources.PROVIDERS["RUNDOWN"]
    assert provider.key_envs == ("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY")
    assert provider.auth_style == "header"
    assert provider.auth_name == "X-TheRundown-Key"
    assert provider.endpoints["sports"] == "/api/v2/sports"
    assert provider.endpoints["events"] == "/api/v2/sports/{sport_id}/events/{date}"
    assert provider.endpoints["openers"] == "/api/v2/sports/{sport_id}/openers/{date}"
    assert sources.CAN_EXECUTE is False


def test_canonical_fetch_keeps_rundown_key_out_of_url(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("THERUNDOWN_API_KEY", "canonical-v2-secret")
    monkeypatch.delenv("RUNDOWN_API_KEY", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_API_KEY", raising=False)

    seen = {}

    def opener(request, timeout=None):
        headers = {key.lower(): value for key, value in request.header_items()}
        seen["url"] = request.full_url
        seen["header"] = headers.get("x-therundown-key")
        return _Response(b'{"sports": [{"sport_id": 3, "sport_name": "MLB"}]}')

    result = sources.fetch("RUNDOWN", "sports", opener=opener)
    assert result.ok is True
    assert result.status == 200
    assert seen["header"] == "canonical-v2-secret"
    assert "canonical-v2-secret" not in seen["url"]
    assert "?key=" not in seen["url"]
    assert seen["url"].endswith("/api/v2/sports")


def test_canonical_401_stays_typed_market_evidence_failure(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("THERUNDOWN_API_KEY", "canonical-v2-secret")

    def opener(_request, timeout=None):
        raise HTTPError("https://therundown.io/api/v2/sports", 401, "unauthorized", {}, io.BytesIO(b""))

    result = sources.fetch("RUNDOWN", "sports", opener=opener)
    assert result.ok is False
    assert result.status == 401
    assert result.code == "RUNDOWN_HTTP_401"
    assert result.can_execute is False
