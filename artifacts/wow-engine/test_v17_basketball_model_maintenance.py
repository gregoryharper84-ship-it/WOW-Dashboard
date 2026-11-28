from datetime import datetime, timezone
import json
from pathlib import Path
import textwrap
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

import v17.basketball_model_maintenance as maintenance
from basketball_certification_write import BasketballCertificationWriteError


def test_default_seasons_are_four_year_window():
    assert maintenance.default_seasons(datetime(2026, 9, 15, tzinfo=timezone.utc)) == (2023, 2024, 2025, 2026)


def test_maintenance_updates_shadow_evidence_without_promotion(monkeypatch):
    seen = []

    def fake_hydrate(sport, seasons, client=None):
        seen.append(("hydrate", sport, tuple(seasons), client))
        return {"sport": sport, "settled_rows": 100, "can_execute": False}

    def fake_replay(sport, client=None):
        seen.append(("replay", sport, client))
        return {
            "sport": sport,
            "persisted_status": "SHADOW",
            "promotion_attempted": False,
            "can_execute": False,
        }

    monkeypatch.setattr(maintenance, "hydrate", fake_hydrate)
    monkeypatch.setattr(maintenance, "run_training_replay", fake_replay)
    db = object()
    result = maintenance.run_basketball_model_maintenance(db, seasons=(2025, 2026))

    assert result["status"] == "COMPLETE"
    assert result["rows_updated"] == 2
    assert result["rows_fresh_acquisition_degraded"] == 0
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert all(row["status"] == "SHADOW_EVIDENCE_UPDATED" for row in result["rows"])
    assert all(row["hydration_status"] == "FRESH_ACQUISITION_UPDATED" for row in result["rows"])
    assert all(row["promotion_attempted"] is False for row in result["rows"])
    assert seen[0][:3] == ("hydrate", "NBA", (2025, 2026))


def test_missing_provider_credential_replays_persisted_corpus(monkeypatch):
    def fail_hydration(*_args, **_kwargs):
        raise maintenance.BasketballHydrationError("BALLDONTLIE_API_KEY unavailable")

    replay_calls = []

    def replay(sport, client=None):
        replay_calls.append((sport, client))
        return {
            "sport": sport,
            "persisted_status": "SHADOW",
            "provenance_preflight": {"provenance_complete": True},
            "freshness_preflight": {"freshness_status": "PASS"},
            "promotion_attempted": False,
            "can_execute": False,
        }

    monkeypatch.setattr(maintenance, "hydrate", fail_hydration)
    monkeypatch.setattr(maintenance, "run_training_replay", replay)
    db = object()
    result = maintenance.run_basketball_model_maintenance(db, sports=("NBA",), seasons=(2026,))
    row = result["rows"][0]

    assert result["status"] == "COMPLETE"
    assert result["rows_updated"] == 1
    assert result["rows_fresh_acquisition_degraded"] == 1
    assert row["status"] == "SHADOW_EVIDENCE_UPDATED"
    assert row["hydration_status"] == "FRESH_ACQUISITION_BLOCKED_USING_PERSISTED_CORPUS"
    assert row["hydration_blocker"]["code"] == "BALLDONTLIE_API_KEY unavailable"
    assert row["hydration_blocker"]["stage"] == "FRESH_SOURCE_HYDRATION"
    assert replay_calls == [("NBA", db)]
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_hydration_and_persisted_replay_failure_remain_blocked(monkeypatch):
    def fail_hydration(*_args, **_kwargs):
        raise maintenance.BasketballHydrationError("BALLDONTLIE_API_KEY unavailable")

    def fail_replay(*_args, **_kwargs):
        raise RuntimeError("WNBA_TRAINING_CORPUS_EMPTY")

    monkeypatch.setattr(maintenance, "hydrate", fail_hydration)
    monkeypatch.setattr(maintenance, "run_training_replay", fail_replay)
    result = maintenance.run_basketball_model_maintenance(object(), sports=("WNBA",), seasons=(2026,))
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "WNBA_TRAINING_CORPUS_EMPTY"
    assert row["hydration_blocker"]["code"] == "BALLDONTLIE_API_KEY unavailable"
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def test_maintenance_route_is_internal_and_auth_wrapped(monkeypatch):
    app = FastAPI()
    monkeypatch.setattr(maintenance, "scout_route_auth_dependency", lambda dep: dep)
    monkeypatch.setattr(
        maintenance,
        "run_basketball_model_maintenance",
        lambda db: {
            "status": "BLOCKED",
            "rows": [],
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    maintenance.install_basketball_model_maintenance_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: SimpleNamespace(),
    )
    client = TestClient(app)
    response = client.post("/internal/v17/basketball-model-maintenance")
    assert response.status_code == 200
    assert response.json()["can_execute"] is False
    assert sum(route.path == "/internal/v17/basketball-model-maintenance" for route in app.routes) == 1

    maintenance.install_basketball_model_maintenance_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: SimpleNamespace(),
    )
    assert sum(route.path == "/internal/v17/basketball-model-maintenance" for route in app.routes) == 1



def test_live_basketball_maintenance_never_runs_on_push():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (
        repo_root / ".github" / "workflows" / "wow-v17-basketball-model-maintenance.yml"
    ).read_text(encoding="utf-8")

    assert "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in workflow
    assert "(github.event_name == 'push' && github.ref == 'refs/heads/main')" not in workflow
    assert 'WOW_CAN_EXECUTE: "false"' in workflow
    assert 'WOW_DRY_RUN_ONLY: "true"' in workflow


class _ProviderError(Exception):
    """Minimal stand-in for postgrest.exceptions.APIError."""

    def __init__(self, payload):
        super().__init__("provider rejected the write")
        self.message = payload


def test_blocked_row_carries_provider_error_fields(monkeypatch):
    def fail_replay(*_args, **_kwargs):
        raise _ProviderError(
            {
                "code": "PGRST204",
                "message": "Could not find the 'sport' column in the schema cache",
                "details": "Searched the cached schema for the column.",
                "hint": "Reload the PostgREST schema cache.",
            }
        )

    monkeypatch.setattr(maintenance, "hydrate", lambda *args, **kwargs: {"sport": "NBA"})
    monkeypatch.setattr(maintenance, "run_training_replay", fail_replay)

    result = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "BASKETBALL_MODEL_MAINTENANCE_FAILED"
    assert row["error_type"] == "_ProviderError"
    assert row["error_details"] == {
        "code": "PGRST204",
        "message": "Could not find the 'sport' column in the schema cache",
        "details": "Searched the cached schema for the column.",
        "hint": "Reload the PostgREST schema cache.",
    }
    assert row["probability_publishable"] is False
    assert row["can_execute"] is False


def _ci_blocked_projection(payload):
    """Run the workflow's own blocked-row projection against a fake payload."""
    repo_root = Path(__file__).resolve().parents[2]
    workflow = (
        repo_root / ".github" / "workflows" / "wow-v17-basketball-model-maintenance.yml"
    ).read_text(encoding="utf-8")
    marker = workflow.index("blocked_rows = [")
    start = workflow.rindex("\n", 0, marker) + 1
    block = workflow[start:workflow.index("basketball = payload", start)]
    scope = {"json": json, "payload": payload}
    with pytest.raises(RuntimeError) as raised:
        exec(compile(textwrap.dedent(block), "<workflow>", "exec"), scope)
    prefix, _, body = str(raised.value).partition(":")
    assert prefix == "BASKETBALL_MAINTENANCE_BLOCKED"
    return json.loads(body)


def test_ci_blocked_projection_carries_provider_error_fields():
    payload = {
        "status": "BLOCKED",
        "rows": [
            {
                "sport": "NBA",
                "status": "BLOCKED",
                "code": "BASKETBALL_MODEL_MAINTENANCE_FAILED",
                "error_type": "APIError",
                "error_details": {
                    "code": "PGRST204",
                    "message": "schema cache miss",
                    "details": "column not found",
                    "hint": "reload the cache",
                },
            }
        ],
    }

    projected = _ci_blocked_projection(payload)
    row = projected["blocked_rows"][0]

    assert projected["status"] == "BLOCKED"
    assert row["sport"] == "NBA"
    assert row["code"] == "BASKETBALL_MODEL_MAINTENANCE_FAILED"
    assert row["error_type"] == "APIError"
    assert row["error_code"] == "PGRST204"
    assert row["error_message"] == "schema cache miss"
    assert row["error_details"] == "column not found"
    assert row["error_hint"] == "reload the cache"


def test_ci_blocked_projection_tolerates_rows_without_provider_details():
    payload = {
        "status": "BLOCKED",
        "rows": [{"sport": "WNBA", "status": "BLOCKED", "code": "WNBA_CORPUS_EMPTY"}],
    }

    row = _ci_blocked_projection(payload)["blocked_rows"][0]

    assert row["code"] == "WNBA_CORPUS_EMPTY"
    assert row["error_code"] is None
    assert row["error_message"] is None
    assert row["error_details"] is None
    assert row["error_hint"] is None


def test_certification_write_failure_uses_its_own_governed_code(monkeypatch):
    def fail_replay(*_args, **_kwargs):
        raise BasketballCertificationWriteError(
            "BASKETBALL_CERTIFICATION_WRITE_NOT_OBSERVED sport=NBA "
            "expected=v2 observed=v1 capability_status=SHADOW "
            "reason_code=CERTIFICATION_REPLAY_REQUIRED"
        )

    monkeypatch.setattr(maintenance, "hydrate", lambda *args, **kwargs: {"sport": "NBA"})
    monkeypatch.setattr(maintenance, "run_training_replay", fail_replay)

    result = maintenance.run_basketball_model_maintenance(
        object(), sports=("NBA",), seasons=(2026,)
    )
    row = result["rows"][0]

    assert result["status"] == "BLOCKED"
    assert row["code"] == "BASKETBALL_CERTIFICATION_WRITE_NOT_OBSERVED"
    assert row["error_type"] == "BasketballCertificationWriteError"
    assert "observed=v1" in row["error_details"]["message"]
    assert row["promotion_attempted"] is False
    assert row["can_execute"] is False
