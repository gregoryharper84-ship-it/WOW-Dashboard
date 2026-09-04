from __future__ import annotations

import importlib


def test_v17_detailed_evidence_entrypoint_imports_and_mounts_contract(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "ci-only-wow-action-key")
    monkeypatch.setenv("WOW_CAN_EXECUTE", "false")
    monkeypatch.setenv("WOW_DRY_RUN_ONLY", "true")

    module = importlib.import_module("api_v17_evidence")
    app = module.app

    pick_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/score-pick-request"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    contract_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/v17/detailed-evidence-contract"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]

    assert len(pick_routes) == 1
    assert len(contract_routes) == 1
    assert pick_routes[0].operation_id == "scoreWowPickRequest"
    assert contract_routes[0].operation_id == "getWowV17DetailedEvidenceContract"
    assert "detailed_evidence" in module.DetailedRawPropEvidence.model_fields


def test_v17_detailed_evidence_entrypoint_preserves_execution_lock(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "ci-only-wow-action-key")
    module = importlib.import_module("api_v17_evidence")
    payload = module.get_v17_detailed_evidence_contract()

    assert payload["status"] == "ACTIVE"
    assert payload["market_evidence_separate"] is True
    assert payload["probability_substitution_allowed"] is False
    assert payload["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert payload["can_execute"] is False
