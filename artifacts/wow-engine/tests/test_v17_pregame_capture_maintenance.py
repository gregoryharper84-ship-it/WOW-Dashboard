from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import github_actions_oidc as oidc
from v17 import pregame_capture_maintenance as maintenance

NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "wow-v17-pregame-capture.yml"


def _cycle(sport: str) -> dict:
    return {
        "status": "CYCLE_COMPLETE",
        "sport": sport,
        "captures_attempted": 1,
        "captures_evidence_insufficient": 1 if sport == "NBA" else 0,
        "captures_candidates_only": 1 if sport == "NHL" else 0,
        "blockers": ["NBA_CONTEXT_ROTATION_MISSING"] if sport == "NBA" else [
            "NHL_CAPTURE_STARTERS_NOT_PUBLISHED"
        ],
        "retention": {
            "retained_count": 1,
            "superseded_count": 0,
            "deduplicated_sha256": [],
            "deletion_performed": False,
        },
        "receipts": [
            {
                "official_event_id": "2026020251" if sport == "NHL" else "espn-401909936",
                "capture_sha256": "a" * 64,
                "confirmed_starter_ids": {"home": None, "away": None},
            }
        ],
    }


def test_run_maintenance_cycles_each_sport_without_granting_authority(monkeypatch):
    seen: list[str] = []

    def fake_cycle(sport, **kwargs):
        seen.append(sport)
        assert kwargs["max_captures"] == 4
        return _cycle(sport)

    monkeypatch.setattr(maintenance, "run_pregame_capture_cycle", fake_cycle)
    result = maintenance.run_pregame_capture_maintenance(now=NOW, max_captures=4)

    assert seen == ["NBA", "NHL"]
    assert result["status"] == "COMPLETE"
    assert result["rows_completed"] == 2
    assert result["rows_blocked"] == 0
    assert [row["sport"] for row in result["rows"]] == ["NBA", "NHL"]
    assert result["rows"][0]["captures_evidence_insufficient"] == 1
    assert result["rows"][1]["captures_candidates_only"] == 1
    assert result["starter_inference_allowed"] is False
    assert result["fitted_model"] is False
    assert result["calibration_status"] == "NOT_CALIBRATED_RESEARCH_ONLY"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False


def test_run_maintenance_isolates_a_failing_sport_and_preserves_the_code(monkeypatch):
    def flaky(sport, **kwargs):
        if sport == "NBA":
            raise RuntimeError("provider exploded")
        return _cycle(sport)

    monkeypatch.setattr(maintenance, "run_pregame_capture_cycle", flaky)
    result = maintenance.run_pregame_capture_maintenance(now=NOW)

    assert result["status"] == "PARTIAL"
    nba, nhl = result["rows"]
    assert nba["status"] == "BLOCKED"
    assert nba["code"] == "PREGAME_CAPTURE_MAINTENANCE_FAILED"
    assert nba["error_type"] == "RuntimeError"
    assert nba["can_execute"] is False
    assert nhl["status"] == "CYCLE_COMPLETE"
    assert result["rows_completed"] == 1


def test_run_maintenance_rejects_unsupported_sport_without_dispatching(monkeypatch):
    def never(_sport, **_kwargs):
        raise AssertionError("unsupported sport must not reach the scheduler")

    monkeypatch.setattr(maintenance, "run_pregame_capture_cycle", never)
    result = maintenance.run_pregame_capture_maintenance(now=NOW, sports=("MLB",))
    assert result["status"] == "BLOCKED"
    assert result["rows"][0]["code"] == "PREGAME_CAPTURE_SPORT_UNSUPPORTED"


def test_route_is_internal_auth_wrapped_and_idempotent(monkeypatch):
    app = FastAPI()
    monkeypatch.setattr(maintenance, "scout_route_auth_dependency", lambda dep: dep)

    install = maintenance.install_pregame_capture_route
    install(
        app,
        auth_dependency=Depends(lambda: None),
        capture_runner=lambda: {
            "status": "COMPLETE",
            "rows": [],
            "rows_completed": 0,
            "rows_blocked": 0,
            "starter_inference_allowed": False,
            "probability_publishable": False,
            "fitted_model": False,
            "can_execute": False,
        },
    )
    install(app, auth_dependency=Depends(lambda: None), capture_runner=lambda: {})

    assert maintenance.ROUTE_PATH.startswith("/internal/")
    assert sum(route.path == maintenance.ROUTE_PATH for route in app.routes) == 1
    response = TestClient(app).post(maintenance.ROUTE_PATH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["can_execute"] is False
    assert payload["probability_publishable"] is False
    assert payload["starter_inference_allowed"] is False


def test_route_uses_the_default_runner_with_env_artifact_root(monkeypatch, tmp_path):
    app = FastAPI()
    monkeypatch.setattr(maintenance, "scout_route_auth_dependency", lambda dep: dep)
    monkeypatch.setenv("WOW_PREGAME_CAPTURE_ROOT", str(tmp_path))
    seen: list[dict] = []

    def fake_run(**kwargs):
        seen.append(kwargs)
        return {"status": "BLOCKED", "rows": [], "can_execute": False}

    monkeypatch.setattr(maintenance, "run_pregame_capture_maintenance", fake_run)
    maintenance.install_pregame_capture_route(app, auth_dependency=Depends(lambda: None))

    assert TestClient(app).post(maintenance.ROUTE_PATH).status_code == 200
    assert seen == [{"artifact_root": str(tmp_path)}]


def test_capture_workflow_is_bounded_single_flight_and_never_live_on_pr():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'cron: "*/15 * * * *"' in workflow
    assert "concurrency:" in workflow
    assert "group: wow-v17-pregame-capture-" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow
    assert "permissions:\n  contents: read\n\n" in workflow
    assert "  capture:\n" in workflow
    assert "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in workflow
    assert "id-token: write" in workflow
    assert 'WOW_CAN_EXECUTE: "false"' in workflow
    assert 'WOW_DRY_RUN_ONLY: "true"' in workflow
    assert "/internal/v17/pregame-capture-maintenance" in workflow
    assert 'payload.get("can_execute") is False' in workflow
    assert 'payload.get("probability_publishable") is False' in workflow
    assert 'payload.get("fitted_model") is False' in workflow
    assert 'payload.get("starter_inference_allowed") is False' in workflow
    assert "upload-artifact" in workflow
    assert "pull_request_target" not in workflow
    assert "WOW_ACTION_API_KEY" not in workflow


def test_capture_workflow_has_its_own_exact_oidc_trust_set():
    ref = oidc.PREGAME_CAPTURE_WORKFLOW_REF
    assert ref.endswith(
        "/.github/workflows/wow-v17-pregame-capture.yml@refs/heads/main"
    )
    assert ref in oidc.PREGAME_CAPTURE_WORKFLOW_REFS
    # Kept out of the legacy allowlist so the pregame capture surface is
    # independently reviewable, matching the forward-evidence/canary pattern.
    assert ref not in oidc.ALLOWED_WORKFLOW_REFS

    claims = {
        "repository": oidc.REPOSITORY,
        "repository_id": oidc.REPOSITORY_ID,
        "repository_owner_id": oidc.REPOSITORY_OWNER_ID,
        "ref": oidc.REF,
        "workflow_ref": ref,
        "runner_environment": "github-hosted",
        "event_name": "schedule",
    }
    assert oidc.validate_github_actions_claims(claims)["workflow_ref"] == ref

    claims["event_name"] = "pull_request"
    with pytest.raises(oidc.GitHubOIDCValidationError, match="EVENT_NOT_ALLOWED"):
        oidc.validate_github_actions_claims(claims)
