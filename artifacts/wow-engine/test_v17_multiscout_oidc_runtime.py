from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from v17 import github_actions_oidc_client as client
from v17 import nightly_multiscout as scout
from v17 import nightly_multiscout_oidc as scout_oidc
from v17 import multiscout_auto_advance_oidc as advance_oidc


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_oidc_client_mints_fixed_audience_and_briefly_caches(monkeypatch):
    client.reset_oidc_cache()
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://github.example/token?x=1")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-secret")
    seen = []

    def fake_urlopen(request, timeout=15):
        seen.append((request.full_url, request.headers.get("Authorization")))
        return _Response({"value": "oidc-jwt"})

    monkeypatch.setattr(client, "urlopen", fake_urlopen)
    assert client.mint_github_actions_oidc() == "oidc-jwt"
    assert client.mint_github_actions_oidc() == "oidc-jwt"
    assert len(seen) == 1
    assert "audience=wow-v17-multiscout" in seen[0][0]
    assert seen[0][1] == "Bearer runner-secret"


def test_oidc_client_missing_runner_context_is_typed(monkeypatch):
    client.reset_oidc_cache()
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", raising=False)
    with pytest.raises(client.GitHubOIDCMintError, match="REQUEST_CONTEXT_UNAVAILABLE"):
        client.mint_github_actions_oidc(force=True)


def test_scout_wrapper_refreshes_oidc_when_legacy_proxy_key_absent(monkeypatch):
    monkeypatch.delenv("WOW_ODDS_PROXY_ACTION_KEY", raising=False)
    monkeypatch.delenv("WOW_GITHUB_OIDC_TOKEN", raising=False)
    calls = []

    def original(path, params=None):
        calls.append((path, os.environ.get("WOW_GITHUB_OIDC_TOKEN")))
        return scout.FetchResult(True, data=[])

    monkeypatch.setattr(scout_oidc.scout, "proxy_get", original)
    monkeypatch.setattr(scout_oidc, "mint_github_actions_oidc", lambda: "fresh-oidc")
    scout_oidc.install_refreshable_oidc_proxy_auth()
    result = scout_oidc.scout.proxy_get("/odds-api/v4/sports")
    assert result.ok is True
    assert calls == [("/odds-api/v4/sports", "fresh-oidc")]


def test_scout_wrapper_keeps_legacy_proxy_key_path(monkeypatch):
    monkeypatch.setenv("WOW_ODDS_PROXY_ACTION_KEY", "legacy")
    original = scout_oidc.scout.proxy_get
    scout_oidc.install_refreshable_oidc_proxy_auth()
    assert scout_oidc.scout.proxy_get is original


def test_auto_advance_oidc_wrapper_uses_minted_token(monkeypatch, tmp_path):
    handoff = {
        "run_id": "run-1",
        "research_run_id": "run-1",
        "status": "DISCOVERY_COMPLETE",
        "model_handoff_ready": True,
        "governance": {"can_execute": False},
        "model_handoff": {"prop_candidates": [], "team_event_candidates": []},
    }
    source = tmp_path / "handoff.json"
    output = tmp_path / "receipt.json"
    source.write_text(json.dumps(handoff))
    monkeypatch.delenv("WOW_ACTION_API_KEY", raising=False)
    monkeypatch.setattr(advance_oidc, "mint_github_actions_oidc", lambda force=True: "fresh-oidc")
    seen = []

    def fake_execute(payload, *, token, origin):
        seen.append((token, origin, payload["run_id"]))
        return {
            "status": "AUTO_ADVANCE_COMPLETE",
            "code": "AUTO_ADVANCE_RECONCILED",
            "source_run_id": "run-1",
            "research_run_id": "run-1",
            "can_execute": False,
        }

    monkeypatch.setattr(advance_oidc, "execute_auto_advance", fake_execute)
    monkeypatch.setattr("sys.argv", ["prog", "--input", str(source), "--output", str(output)])
    assert advance_oidc.main() == 0
    assert seen[0][0] == "fresh-oidc"
    assert json.loads(output.read_text())["can_execute"] is False
