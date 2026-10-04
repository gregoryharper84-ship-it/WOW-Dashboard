from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from v17 import github_actions_oidc_client as client
from v17 import nightly_multiscout as scout
from v17 import nightly_multiscout_oidc as scout_oidc
from v17 import multiscout_auto_advance as auto_advance
from v17 import multiscout_auto_advance_oidc as advance_oidc
from v17 import scout_research_promotion as promotion


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


def test_provider_429_is_not_global_scout_terminal(monkeypatch):
    monkeypatch.setattr(scout, "TERMINAL_SOURCE_HTTP_STATUSES", {401, 403, 429})
    scout_oidc.configure_source_failure_scope()
    assert scout.TERMINAL_SOURCE_HTTP_STATUSES == set()


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

    def fake_execute(payload, *, token, origin, post_fn=None, max_in_flight=None, progress_fn=None):
        seen.append((token, origin, payload["run_id"], post_fn, progress_fn, max_in_flight))
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
    assert callable(seen[0][4])
    assert seen[0][5] == advance_oidc.OIDC_MAX_IN_FLIGHT == 2
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

    def fake_execute(payload, *, token, origin, progress_fn=None):
        seen.append((token, origin, payload["run_id"], progress_fn))
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
    assert seen[0][:3] == ("static-action-key", advance_oidc.ACTION_ORIGIN, "run-static")
    assert callable(seen[0][3])


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


def test_auto_advance_progress_writer_is_atomic_and_fail_closed(tmp_path):
    output = tmp_path / "auto-advance-receipt.json"
    handoff = {"run_id": "run-progress", "research_run_id": "research-progress"}
    write = advance_oidc._progress_writer(output, handoff)
    write(
        lane="props",
        batch_key="research-progress:props:1",
        batch_index=1,
        completed_batches=1,
        total_batches=3,
        receipt={"ok": True, "http_status": 200, "can_execute": False},
    )
    data = json.loads(output.read_text())
    assert data["status"] == "AUTO_ADVANCE_IN_PROGRESS"
    assert data["source_run_id"] == "run-progress"
    assert data["research_run_id"] == "research-progress"
    assert data["completed_batches"][0]["batch_key"] == "research-progress:props:1"
    assert data["completed_batches"][0]["ok"] is True
    assert data["can_execute"] is False
    assert not output.with_suffix(output.suffix + ".tmp").exists()


def test_multiscout_workflow_triggers_on_p0_evidence_transport_changes():
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "wow-v17-nightly-multiscout.yml"
    text = workflow.read_text(encoding="utf-8")
    for path in (
        "artifacts/wow-engine/v17/market_evidence_snapshot_bridge.py",
        "artifacts/wow-engine/v17/scout_brain_transport_sanitizer.py",
    ):
        assert text.count(f'- "{path}"') == 2


def test_async_handoff_feature_flag_uses_durable_queue_instead_of_sync_batches(monkeypatch, tmp_path):
    handoff = {
        "run_id": "run-async",
        "research_run_id": "run-async",
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "model_handoff_ready": True,
        "source_blockers": [{"code": "SECONDARY_SOURCE_DEGRADED"}],
        "governance": {"can_execute": False},
        "model_handoff": {"prop_candidates": [], "team_event_candidates": []},
    }
    source = tmp_path / "handoff.json"
    output = tmp_path / "receipt.json"
    source.write_text(json.dumps(handoff))
    monkeypatch.setenv("WOW_SCOUT_ASYNC_HANDOFF_ENABLED", "true")
    monkeypatch.delenv("WOW_ACTION_API_KEY", raising=False)
    monkeypatch.setattr(advance_oidc, "mint_github_actions_oidc", lambda force=True: "fresh-oidc")
    seen = []

    def fake_async(payload, token, *, origin):
        seen.append((payload, token, origin))
        return {
            "schema_version": "wow.v17.scout-handoff-run.v1",
            "status": "IN_PROGRESS",
            "source_run_id": "run-async",
            "research_run_id": "run-async",
            "rows_in": 3,
            "rows_completed": 0,
            "rows_held": 3,
            "rows_rejected": 0,
            "row_accounting_pass": True,
            "reconciliation_pass": False,
            "can_execute": False,
        }

    monkeypatch.setattr(advance_oidc, "_execute_async_handoff", fake_async)
    monkeypatch.setattr(
        advance_oidc,
        "execute_auto_advance",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sync path must not run")),
    )
    monkeypatch.setattr("sys.argv", ["prog", "--input", str(source), "--output", str(output)])
    assert advance_oidc.main() == 0
    assert len(seen) == 1
    async_payload, async_token, async_origin = seen[0]
    assert async_token == "fresh-oidc"
    assert async_origin == advance_oidc.ACTION_ORIGIN
    assert async_payload["run_id"] == "run-async"
    assert async_payload["status"] == "DISCOVERY_COMPLETE"
    assert async_payload["source_acquisition_status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    receipt = json.loads(output.read_text())
    assert receipt["status"] == "IN_PROGRESS"
    assert receipt["row_accounting_pass"] is True
    assert receipt["source_acquisition_status"] == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
    assert receipt["source_blocker_count"] == 1
    assert receipt["can_execute"] is False



def _async_compaction_fixture():
    evidence = {
        "source_class": "SPORTSBOOK_FEED",
        "market_last_update": "2026-10-04T01:55:00+00:00",
        "bookmaker": "ExampleBook",
        "source_provider": "example",
        "description": "Player One",
        "outcome_name": "Over",
        "point": 4.5,
        "market_key": "player_assists",
        "unused_quote_blob": "x" * 20000,
    }
    common = {
        "official_event_id": "evt-1",
        "sport_key": "basketball_nba",
        "commence_time": "2026-10-04T03:00:00+00:00",
        "home_team": "Home",
        "away_team": "Away",
        "market_evidence_source_blockers": [],
        "contradictory_evidence": [],
        "red_team_flags": [],
        "research_priority": "HIGH",
        "market_evidence_stale": [{"blob": "s" * 50000}],
        "market_evidence_historical": [{"blob": "h" * 50000}],
        "research_worker_briefs": [{"blob": "b" * 50000}],
        "can_execute": False,
    }
    prop = {**common, "market_evidence": dict(evidence)}
    team = {
        **common,
        "official_event_id": "evt-2",
        "sport_key": "americanfootball_nfl",
        "market_evidence": [
            dict(evidence, bookmaker="BookA"),
            dict(evidence, bookmaker="BookB"),
            dict(evidence, bookmaker="BookC"),
        ],
    }
    return {
        "run_id": "run-compact",
        "research_run_id": "research-compact",
        "generated_at": "2026-10-04T02:00:00+00:00",
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "model_handoff_ready": True,
        "source_blockers": [{"code": "SECONDARY_SOURCE_DEGRADED"}],
        "governance": {
            "can_execute": False,
            "upset_alert_requires_governed_llp_probability": True,
        },
        "model_handoff": {
            "prop_candidates": [prop],
            "team_event_candidates": [team],
        },
        "can_execute": False,
    }


def test_async_handoff_compaction_preserves_dispatch_and_research_gate_semantics():
    raw = _async_compaction_fixture()
    normalized = advance_oidc._dispatchable_handoff(raw)
    compact = advance_oidc._compact_async_handoff(normalized)

    assert normalized["status"] == "DISCOVERY_COMPLETE"
    assert compact["status"] == "DISCOVERY_COMPLETE"
    assert compact["governance"] == normalized["governance"]
    assert compact["can_execute"] is False

    raw_dispatch = auto_advance.build_dispatch(normalized)
    compact_dispatch = auto_advance.build_dispatch(compact)
    assert compact_dispatch == raw_dispatch

    now = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)
    for lane in ("prop_candidates", "team_event_candidates"):
        raw_candidate = normalized["model_handoff"][lane][0]
        compact_candidate = compact["model_handoff"][lane][0]
        assert promotion.evaluate_candidate(compact_candidate, now=now) == promotion.evaluate_candidate(
            raw_candidate, now=now
        )
        assert "market_evidence_stale" not in compact_candidate
        assert "market_evidence_historical" not in compact_candidate
        assert "research_worker_briefs" not in compact_candidate

    compact_bytes = len(json.dumps(compact, separators=(",", ":")).encode("utf-8"))
    raw_bytes = len(json.dumps(normalized, separators=(",", ":")).encode("utf-8"))
    assert compact_bytes * 10 < raw_bytes


def test_async_handoff_compaction_preserves_invalid_candidate_shape_for_typed_rejects():
    handoff = {
        "run_id": "run-invalid",
        "research_run_id": "research-invalid",
        "generated_at": "2026-10-04T02:00:00+00:00",
        "status": "DISCOVERY_COMPLETE",
        "model_handoff_ready": True,
        "governance": {"can_execute": False},
        "model_handoff": {
            "prop_candidates": ["not-a-candidate"],
            "team_event_candidates": [None],
        },
    }

    compact = advance_oidc._compact_async_handoff(handoff)
    assert compact["model_handoff"]["prop_candidates"] == ["not-a-candidate"]
    assert compact["model_handoff"]["team_event_candidates"] == [None]
    dispatch = auto_advance.build_dispatch(compact)
    assert dispatch["mapping"]["rejected_prop_rows"] == [
        {"source_index": 1, "code": "PROP_CANDIDATE_INVALID"}
    ]
    assert dispatch["mapping"]["rejected_team_event_rows"] == [
        {"source_index": 1, "code": "TEAM_EVENT_CANDIDATE_INVALID"}
    ]
