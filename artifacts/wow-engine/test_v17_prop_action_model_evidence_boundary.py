"""Negative evidence tests for the V17 PLAYER_PROP Action triad preflight."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17.model_evidence_bundle_contract import ModelBundleScope
from v17.prop_action_model_evidence_boundary import (
    INCOMPLETE,
    READER_UNAVAILABLE,
    UNRESOLVED,
    require_prop_action_triad,
)
from prop_terminal_reducer_v2 import reduce_prop_terminal


def _request(**changes):
    obj = {
        "sport": "NBA", "stat_type": "THREE_POINTERS_MADE",
        "event_id": "official-123", "event_start_time": "2026-11-15T02:00:00Z",
        "direction": "MORE", "line": 7.0,
    }
    obj.update(changes)
    return SimpleNamespace(**obj)


def _route(**changes):
    obj = {
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "model_family": "NBA_TEST_FITTED",
        "model_artifact_version": "nba-test-v1",
        "feature_schema_version": "NBA_FEATURES_V1",
        "artifact_checksum": "a" * 64,
    }
    obj.update(changes)
    return obj


def _result(scope: ModelBundleScope, **changes):
    obj = {
        "status": "READY_FOR_INDEPENDENT_REVIEW",
        "bundle_complete": True,
        "blockers": [],
        "model_kind": scope.model_kind,
        "sport": scope.sport,
        "model_family": scope.model_family,
        "model_artifact_version": scope.model_artifact_version,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "component_sources": {},
        "missing_component_fields": {},
    }
    obj.update(changes)
    return obj


def _assess(assessor, *, req=None, route=None, db=None):
    return require_prop_action_triad(
        db=object() if db is None else db,
        req=req or _request(),
        route_artifact=route or _route(),
        inference_request=SimpleNamespace(player_id="wow-name:certified-exact-player"),
        scored_at="2026-11-08T16:00:00Z",
        period="FULL_GAME",
        assessor=assessor,
    )


def _expected_hold(call, code, blocker=None):
    with pytest.raises(HTTPException) as exc:
        call()
    assert exc.value.status_code == 409
    body = exc.value.detail
    assert body["code"] == code
    if blocker:
        assert body["blocker_code"] == blocker
    assert body["model_evaluated"] is False
    assert body["specialist_invoked"] is False
    assert body["scoring_attempted"] is False
    assert body["probability_publishable"] is False
    assert body["rank_eligible"] is False
    assert body["can_execute"] is False
    assert body["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    return body


def test_reader_receives_exact_server_owned_identity_without_promotion():
    captured = []
    def assessor(db, scope):
        captured.append(scope)
        return _result(scope)

    report = _assess(assessor)
    assert report["status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert report["can_execute"] is False
    assert report["probability_publishable"] is False
    assert captured[0].model_kind == "PLAYER_PROP"
    assert captured[0].player_id == "wow-name:certified-exact-player"
    assert captured[0].line == "7.0"  # not "7+" or neighbouring 6.5/7.5
    assert captured[0].period == "FULL_GAME"
    assert captured[0].official_event_id == "official-123"


@pytest.mark.parametrize("missing", [
    "model_family", "model_artifact_version", "feature_schema_version",
])
def test_route_cannot_infer_missing_model_identity(missing):
    route = _route(**{missing: ""})
    _expected_hold(
        lambda: _assess(lambda db, scope: pytest.fail("reader called"), route=route),
        UNRESOLVED, "MODEL_BUNDLE_SCOPE_IDENTITY_UNAVAILABLE",
    )


def test_reader_raises_without_leaking_exception_text():
    def broken(db, scope):
        raise RuntimeError("do-not-print-database-secret")

    body = _expected_hold(lambda: _assess(broken), READER_UNAVAILABLE)
    assert "do-not-print-database-secret" not in str(body)


def test_unavailable_client_is_typed_and_does_not_read():
    _expected_hold(
        lambda: require_prop_action_triad(
            db=None, req=_request(), route_artifact=_route(),
            inference_request=SimpleNamespace(player_id="wow-name:exact"),
            scored_at="2026-11-08T16:00:00Z", period="FULL_GAME",
            assessor=lambda db, scope: pytest.fail("unexpected read"),
        ),
        READER_UNAVAILABLE, "MODEL_BUNDLE_DB_UNAVAILABLE",
    )


def test_reader_blockers_survive_without_model_unavailable():
    body = _expected_hold(
        lambda: _assess(
            lambda db, scope: _result(
                scope, status="BLOCKED", bundle_complete=False,
                blockers=["MODEL_BUNDLE_PREGAME_CORPUS_MISSING",
                          "MODEL_BUNDLE_CALIBRATOR_INVALID_OR_UNVERIFIED"],
                component_sources={"pregame_corpus": {"status": "NOT_CONFIGURED"}},
            )
        ),
        INCOMPLETE, "MODEL_BUNDLE_PREGAME_CORPUS_MISSING",
    )
    assert body["blockers"] == [
        "MODEL_BUNDLE_PREGAME_CORPUS_MISSING",
        "MODEL_BUNDLE_CALIBRATOR_INVALID_OR_UNVERIFIED",
    ]


@pytest.mark.parametrize("mutant", [
    {"status": "BLOCKED", "bundle_complete": True},
    {"probability_publishable": True},
    {"rank_eligible": True},
    {"can_execute": True},
    {"bundle_complete": False},
])
def test_registry_columns_cannot_self_certify(mutant):
    _expected_hold(
        lambda: _assess(lambda db, scope: _result(scope, **mutant)),
        INCOMPLETE,
    )


def test_neighbouring_version_response_not_admitted():
    _expected_hold(
        lambda: _assess(lambda db, scope: _result(scope, model_artifact_version="other")),
        READER_UNAVAILABLE, "MODEL_BUNDLE_READER_CONTRACT_INVALID",
    )


def test_terminal_reducer_classifies_typed_evidence_gap_separately():
    decision = reduce_prop_terminal(
        proposed_label="MODEL_EVIDENCE_TRIAD_BLOCKED",
        blockers=("MODEL_EVIDENCE_TRIAD_INCOMPLETE",
                  "MODEL_BUNDLE_PREGAME_CORPUS_MISSING"),
        model_evaluated=False,
    )
    assert decision.terminal_label == "MODEL_EVIDENCE_TRIAD_BLOCKED"
    assert decision.verdict_class == "MODEL_EVIDENCE_TRIAD_BLOCKED"
    assert decision.infrastructure_blocked is True
    assert decision.model_evaluated is False
    assert decision.pick_rejected is False
    assert "MODEL_BUNDLE_PREGAME_CORPUS_MISSING" in decision.blockers


def test_request_bound_wrapper_does_not_rewrite_preinference_hold_as_invoked():
    from pick_request_runtime import _ScoringReceiptMarketApi

    def preflight_held():
        raise HTTPException(
            status_code=409,
            detail={"code": INCOMPLETE, "specialist_invoked": False,
                    "scoring_attempted": False, "model_evaluated": False},
        )

    proxy = _ScoringReceiptMarketApi.__new__(_ScoringReceiptMarketApi)
    with pytest.raises(HTTPException) as exc:
        proxy._invoke_score(preflight_held)
    assert exc.value.detail["specialist_invoked"] is False
    assert exc.value.detail["scoring_attempted"] is False
    assert exc.value.detail["specialist_scoring_attempted"] is True  # until next fix


def test_direct_action_scoring_stops_before_fitted_model(monkeypatch):
    import api_prod_market as market
    from v17 import prop_action_model_evidence_boundary as gate

    req = _request()
    monkeypatch.setattr(
        market.prod, "_reject_llp_prop_identity", lambda identity: "WOW_BETTING_ENGINE"
    )
    monkeypatch.setattr(
        market.prod, "_runtime_capability", lambda key: {"capability_status": "AVAILABLE"}
    )
    monkeypatch.setattr(
        market, "_preflight_prop_route", lambda *a, **kw: ({"controlling_specialist": "TEST"}, _route())
    )
    monkeypatch.setattr(
        market, "repair_prop_evidence", lambda *a, **kw: {
            "ok": True, "code": "PROP_EVIDENCE_READY",
            "player": "Stephen Curry", "source_snapshot_id": "snapshot-1",
        },
    )
    monkeypatch.setattr(
        market, "_server_owned_inference_request",
        lambda *a, **kw: SimpleNamespace(player_id="wow-name:exact-player"),
    )
    monkeypatch.setattr(market.prod, "get_client", lambda: object())
    monkeypatch.setattr(
        gate, "require_prop_action_triad",
        lambda **kw: (_ for _ in ()).throw(
            HTTPException(status_code=409, detail={
                "code": INCOMPLETE, "scoring_attempted": False, "can_execute": False,
            })
        ),
    )
    monkeypatch.setattr(
        market, "score_discrete_prop_end_to_end",
        lambda **kw: pytest.fail("fitted scorer must never run on held triad"),
    )
    with pytest.raises(HTTPException) as exc:
        market.score_prop(req, x_wow_model_identity=None)
    assert exc.value.detail["code"] == INCOMPLETE
