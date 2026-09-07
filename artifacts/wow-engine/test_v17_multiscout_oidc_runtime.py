from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

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


def test_auto_advance_refreshes_oidc_on_401_and_retries_only_failed_request(monkeypatch):
    posts = []
    minted = []

    def fake_post(origin, path, token, payload, timeout=120):
        posts.append((origin, path, token, payload, timeout))
        if token == "expired-oidc":
            return {"ok": False, "http_status": 401, "body": {"detail": "Unauthorized"}, "can_execute": False}
        return {"ok": True, "http_status": 200, "body": {"rows": []}, "can_execute": False}

    def fake_mint(force=True):
        minted.append(force)
        return "renewed-oidc"

    monkeypatch.setattr(advance_oidc, "_post_json", fake_post)
    monkeypatch.setattr(advance_oidc, "mint_github_actions_oidc", fake_mint)
    post = advance_oidc._refreshing_oidc_post("expired-oidc")
    receipt = post("https://engine.example", "/score-pick-request", "ignored", {"rows": []})

    assert receipt["ok"] is True
    assert [entry[2] for entry in posts] == ["expired-oidc", "renewed-oidc"]
    assert minted == [True]

    posts.clear()
    receipt = post("https://engine.example", "/score-pick-request", "ignored", {"rows": []})
    assert receipt["ok"] is True
    assert [entry[2] for entry in posts] == ["renewed-oidc"]
    assert minted == [True]


def test_auto_advance_oidc_refresh_failure_stays_fail_closed(monkeypatch):
    monkeypatch.setattr(
        advance_oidc,
        "_post_json",
        lambda *args, **kwargs: {
            "ok": False,
            "http_status": 401,
            "body": {"detail": "Unauthorized"},
            "can_execute": False,
        },
    )

    def fail_mint(force=True):
        raise client.GitHubOIDCMintError("GITHUB_OIDC_MINT_FAILED")

    monkeypatch.setattr(advance_oidc, "mint_github_actions_oidc", fail_mint)
    receipt = advance_oidc._refreshing_oidc_post("expired")(
        "https://engine.example", "/score-pick-request", "ignored", {"rows": []}
    )
    assert receipt == {
        "ok": False,
        "http_status": 401,
        "body": {"code": "GITHUB_OIDC_MINT_FAILED"},
        "can_execute": False,
    }


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

    def fake_execute(payload, *, token, origin, post_fn=None):
        seen.append((token, origin, payload["run_id"], post_fn))
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
    assert callable(seen[0][3])
    assert json.loads(output.read_text())["can_execute"] is False


def test_auto_advance_static_action_key_keeps_existing_nonrefreshing_path(monkeypatch, tmp_path):
    handoff = {
        "run_id": "run-static",
        "research_run_id": "run-static",
        "status": "DISCOVERY_COMPLETE",
        "model_handoff_ready": True,
        "governance": {"can_execute": False},
        "model_handoff": {"prop_candidates": [], "team_event_candidates": []},
    }
    source = tmp_path / "handoff.json"
    output = tmp_path / "receipt.json"
    source.write_text(json.dumps(handoff))
    monkeypatch.setenv("WOW_ACTION_API_KEY", "static-action-key")
    seen = []

    def fake_execute(payload, *, token, origin):
        seen.append((token, origin, payload["run_id"]))
        return {
            "status": "AUTO_ADVANCE_COMPLETE",
            "code": "AUTO_ADVANCE_RECONCILED",
            "source_run_id": "run-static",
            "research_run_id": "run-static",
            "can_execute": False,
        }

    monkeypatch.setattr(advance_oidc, "execute_auto_advance", fake_execute)
    monkeypatch.setattr("sys.argv", ["prog", "--input", str(source), "--output", str(output)])
    assert advance_oidc.main() == 0
    assert seen == [("static-action-key", advance_oidc.ACTION_ORIGIN, "run-static")]


def test_postmerge_workflow_direct_script_invocations_import_v17_package():
    """Regress the exact script form used by the GitHub post-merge workflow.

    ``--help`` exits before any network/OIDC acquisition while still proving
    each direct-file wrapper can import its package dependencies from the
    artifacts/wow-engine working directory.
    """
    engine_dir = Path(__file__).resolve().parent
    scripts = (
        engine_dir / "v17" / "nightly_multiscout_oidc.py",
        engine_dir / "v17" / "multiscout_auto_advance_oidc.py",
    )
    for script in scripts:
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=engine_dir,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "ModuleNotFoundError" not in result.stderr


def test_postmerge_workflow_installs_runtime_dependencies_before_live_scout():
    """The live job uses a fresh runner and must install PyJWT/FastAPI/etc itself."""
    engine_dir = Path(__file__).resolve().parent
    workflow = engine_dir.parents[1] / ".github" / "workflows" / "wow-v17-nightly-multiscout.yml"
    text = workflow.read_text(encoding="utf-8")
    live_job = text.split("  nightly-discovery:\n", 1)[1]
    install_at = live_job.index("- name: Install live Scout runtime dependencies")
    run_at = live_job.index("- name: Run V17 Nightly Multi-Scout")
    assert "pip install -r requirements.txt" in live_job[install_at:run_at]
    assert install_at < run_at
