"""Attribution for internal NHL source routes and source-rights provenance.

The 2026-11-23 live G1 rebuild could not be attributed to any caller because
the internal routes wrote no invocation receipt and reported nothing about where
source-rights approval came from. These regressions pin both behaviours.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import github_actions_oidc
import v17.nhl_corpus_certification_readiness as readiness
import v17.nhl_source_reingest as reingest
from github_actions_oidc import scout_route_auth_dependency
from v17.action_invocation_telemetry import (
    RECONCILE_RECOVERY_RPC,
    RECORD_RECEIPT_RPC,
    RECORD_RECOVERY_RPC,
    ROUTE_OPERATION_IDS,
    _client_addr,
    _forwarded_for,
    install_action_invocation_middleware,
)

REINGEST_ROUTE = "/internal/v17/nhl-source-reingest"
REBUILD_ROUTE = "/internal/v17/nhl-g1-corpus-rebuild"


class _FakeDB:
    def __init__(self, sink):
        self.sink = sink

    def rpc(self, name, params):
        return _FakeRPC(self, name, params)


class _FakeRPC:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        if self.name == RECONCILE_RECOVERY_RPC:
            return SimpleNamespace(data={"rows_deleted": 0})
        if self.name == RECORD_RECEIPT_RPC:
            self.db.sink.append(dict(self.params["p_receipt"]))
            return SimpleNamespace(data={"status": "INSERTED"})
        if self.name == RECORD_RECOVERY_RPC:
            return SimpleNamespace(data={"status": "RECONCILED"})
        raise AssertionError(self.name)


def test_both_internal_source_routes_are_attributed():
    assert ROUTE_OPERATION_IDS[REINGEST_ROUTE] == "runWowV17NhlSourceReingest"
    assert ROUTE_OPERATION_IDS[REBUILD_ROUTE] == "runWowV17NhlG1CorpusRebuild"


def test_source_rights_provenance_labels_runtime_only_approval(monkeypatch):
    monkeypatch.delenv(reingest.SOURCE_RIGHTS_ENV_VAR, raising=False)
    unset = reingest.source_rights_provenance()
    assert unset["approved"] is False
    assert unset["source"] == "UNSET"
    assert unset["runtime_only"] is False

    monkeypatch.setenv(reingest.SOURCE_RIGHTS_ENV_VAR, "1")
    runtime = reingest.source_rights_provenance()
    assert runtime["approved"] is True
    assert runtime["source"] == "ENVIRONMENT_VARIABLE"
    assert runtime["runtime_only"] is True
    assert runtime["env_var"] == "WOW_NHL_SOURCE_RIGHTS_APPROVED"

    explicit = reingest.source_rights_provenance(True)
    assert explicit["approved"] is True
    assert explicit["source"] == "EXPLICIT_ARGUMENT"
    assert explicit["runtime_only"] is False

    denied = reingest.source_rights_provenance(False)
    assert denied["approved"] is False
    assert denied["source"] == "EXPLICIT_DENIAL"

    for block in (unset, runtime, explicit, denied):
        assert block["can_execute"] is False


def test_reingest_rights_hold_reports_unset_provenance(monkeypatch):
    monkeypatch.delenv(reingest.SOURCE_RIGHTS_ENV_VAR, raising=False)
    result = reingest.run_nhl_source_reingest(
        object(), season=20212022, ingest_code_sha="abcdef1"
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_RIGHTS_HOLD"
    assert result["source_rights"]["source"] == "UNSET"
    assert result["can_execute"] is False


def test_g1_rebuild_rights_hold_reports_unset_provenance(monkeypatch):
    monkeypatch.delenv(reingest.SOURCE_RIGHTS_ENV_VAR, raising=False)
    result = readiness.run_nhl_g1_corpus_rebuild(object(), dry_run=False)
    assert result["status"] == "BLOCKED"
    assert result["code"] == "NHL_SOURCE_RIGHTS_HOLD"
    assert result["source_rights"]["source"] == "UNSET"
    assert result["can_execute"] is False


def test_both_rights_hold_responses_are_identical(monkeypatch):
    """One source_rights shape, derived by each helper, never by a call site."""
    monkeypatch.delenv(reingest.SOURCE_RIGHTS_ENV_VAR, raising=False)
    reingest_hold = reingest.run_nhl_source_reingest(
        object(), season=20212022, ingest_code_sha="abcdef1"
    )
    rebuild_hold = readiness.run_nhl_g1_corpus_rebuild(object(), dry_run=False)
    assert reingest_hold["code"] == rebuild_hold["code"] == "NHL_SOURCE_RIGHTS_HOLD"
    assert reingest_hold["source_rights"] == rebuild_hold["source_rights"]
    assert reingest_hold["source_rights"] == reingest.source_rights_provenance()

    # Both blocked helpers attach provenance from the same argument shape.
    reingest_blocked = reingest._blocked("X", stage="SOURCE_RIGHTS", rights_approved=True)
    rebuild_blocked = readiness._blocked("X", rights_approved=True)
    assert reingest_blocked["source_rights"] == rebuild_blocked["source_rights"]
    assert rebuild_blocked["source_rights"]["source"] == "EXPLICIT_ARGUMENT"
    assert rebuild_blocked["can_execute"] is False


def test_source_address_helpers_reject_non_ip_peers():
    assert (
        _client_addr(SimpleNamespace(client=SimpleNamespace(host="203.0.113.9")))
        == "203.0.113.9"
    )
    assert _client_addr(SimpleNamespace(client=SimpleNamespace(host="testclient"))) is None
    assert _client_addr(SimpleNamespace(client=None)) is None
    assert _forwarded_for({"x-forwarded-for": " 198.51.100.4, 10.0.0.1 "}) == (
        "198.51.100.4, 10.0.0.1"
    )
    assert _forwarded_for({}) is None


def _post_attributed_route(route: str, headers: dict[str, str]):
    """Serve one internal NHL route under the real invocation middleware."""
    sink: list[dict] = []
    app = FastAPI()

    @app.post(route, dependencies=[scout_route_auth_dependency(None)])
    def _route():
        return {"status": "BLOCKED", "can_execute": False}

    install_action_invocation_middleware(app, db_client_fn=lambda: _FakeDB(sink))

    with TestClient(app) as client:
        response = client.post(route, headers=headers)
    return response, sink


def test_internal_route_receipt_captures_bearer_class_and_source(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-action-key")
    response, sink = _post_attributed_route(
        REINGEST_ROUTE,
        {
            "Authorization": "Bearer test-action-key",
            "User-Agent": "wow-attribution-test/1.0",
            "X-Request-ID": "req-attribution-1",
            "X-Forwarded-For": "198.51.100.4, 10.0.0.1",
        },
    )

    assert response.status_code == 200
    assert len(sink) == 1
    receipt = sink[0]
    assert receipt["route"] == REINGEST_ROUTE
    assert receipt["action_operation_id"] == "runWowV17NhlSourceReingest"
    assert receipt["http_method"] == "POST"
    assert receipt["http_status"] == 200
    assert receipt["auth_scheme"] == "BEARER"
    assert receipt["auth_bearer_class"] == "WOW_ACTION_API_KEY"
    assert receipt["caller_user_agent"] == "wow-attribution-test/1.0"
    assert receipt["request_id"] == "req-attribution-1"
    assert receipt["forwarded_for"] == "198.51.100.4, 10.0.0.1"
    assert receipt["can_execute"] is False


@pytest.mark.parametrize("route", [REINGEST_ROUTE, REBUILD_ROUTE])
@pytest.mark.parametrize(
    ("verified", "expected_status", "expected_bearer_class"),
    [(True, 200, "GITHUB_ACTIONS_OIDC"), (False, 401, "UNAUTHORIZED")],
)
def test_oidc_outcome_is_published_in_the_receipt(
    monkeypatch, route, verified, expected_status, expected_bearer_class
):
    monkeypatch.delenv("WOW_ACTION_API_KEY", raising=False)

    def _stub_verify(token: str) -> dict:
        # Stubbed so no token verification or JWKS request leaves the process.
        assert token == "test-oidc-token"
        if not verified:
            raise github_actions_oidc.GitHubOIDCValidationError(
                "GITHUB_OIDC_SIGNATURE_OR_STANDARD_CLAIMS_INVALID"
            )
        return {}

    monkeypatch.setattr(
        github_actions_oidc, "verify_github_actions_oidc", _stub_verify
    )
    response, sink = _post_attributed_route(
        route, {"Authorization": "Bearer test-oidc-token"}
    )

    assert response.status_code == expected_status
    assert len(sink) == 1
    assert sink[0]["route"] == route
    assert sink[0]["auth_bearer_class"] == expected_bearer_class
