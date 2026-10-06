from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from v17 import generate_acceptance_routes as subject
from v17.wnba_prop_evidence_control_plane import WNBAForwardEvidenceRequest


HERE = Path(__file__).resolve().parent / "v17"


def _fake_app() -> FastAPI:
    app = FastAPI()

    @app.post(
        "/internal/v17/wnba-spread-forward-auto-canary",
        operation_id="runWowV17WNBASpreadForwardAutoCanary",
    )
    def spread() -> dict[str, object]:
        return {
            "can_execute": False,
            "probability_publishable": False,
        }

    @app.post(
        "/internal/v17/wnba-prop-forward-evidence/acquire",
        operation_id="acquireWowV17WnbaPropForwardEvidence",
    )
    def prop(req: WNBAForwardEvidenceRequest) -> dict[str, object]:
        return {"can_execute": False, "requested_date": req.requested_date}

    return app


def test_registry_policy_contains_only_real_1127_acceptance_surfaces() -> None:
    assert set(subject.ACCEPTANCE_POLICY) == {
        ("/internal/v17/wnba-spread-forward-auto-canary", "POST"),
        ("/internal/v17/wnba-prop-forward-evidence/acquire", "POST"),
    }
    assert ("/v1/probability/evaluate", "POST") not in subject.ACCEPTANCE_POLICY
    assert ("/internal/v17/wnba-prop-forward-auto-canary", "POST") not in subject.ACCEPTANCE_POLICY


def test_registry_generation_hashes_request_and_response_contracts(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_canonical_contract_routes", lambda: {})
    registry = subject.build_registry(_fake_app())
    routes = {(row["path"], row["method"]): row for row in registry["routes"]}

    prop = routes[("/internal/v17/wnba-prop-forward-evidence/acquire", "POST")]
    spread = routes[("/internal/v17/wnba-spread-forward-auto-canary", "POST")]

    assert prop["request_schema_hash"] == "b27c0e4cbcc7a682"
    assert prop["response_schema_hash"] == "82ef96cebaf5fbe1"
    assert spread["request_schema_hash"] == "44136fa355b3678a"
    assert spread["response_schema_hash"] == "82ef96cebaf5fbe1"
    assert all(row["can_execute_required"] is False for row in registry["routes"])
    assert registry["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_missing_allowlisted_route_fails_closed(monkeypatch) -> None:
    app = FastAPI()
    monkeypatch.setattr(subject, "_canonical_contract_routes", lambda: {})
    with pytest.raises(RuntimeError, match="ACCEPTANCE_ROUTE_NOT_MOUNTED"):
        subject.build_registry(app)


def test_checked_in_registry_is_non_executable_and_deterministic() -> None:
    payload = json.loads((HERE / "production_acceptance_routes.json").read_text())
    assert payload["runtime_generation"] == "V17_ACTIVE"
    assert payload["can_execute"] is False
    assert payload["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert payload["total_acceptance_routes"] == len(payload["routes"]) == 2
    assert "generated_at_utc" not in payload
    for row in payload["routes"]:
        assert row["can_execute_required"] is False
        assert row["required_headers"]["X-WOW-Acceptance-Mode"] == "true"
        assert row["required_headers"]["X-WOW-Runtime-Generation"] == "V17_ACTIVE"
