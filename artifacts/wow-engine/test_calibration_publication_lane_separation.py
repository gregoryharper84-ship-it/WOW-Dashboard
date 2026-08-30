from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

import api_calibration_lane_acceptance as api
from calibration_publication_scope import classify_probability_capability


def _request():
    return api.market_api.ScorePropRequest(
        event_id="WNBA:TEST:CALPUB:1",
        event_start_time=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        sport="WNBA",
        player="Test Player",
        stat_type="REB",
        line=10.5,
        direction="MORE",
        source_snapshot_id=str(uuid.uuid4()),
        money_lane_status="PAYOUT_UNRESOLVED",
    )


def _specialist():
    return {
        "sport": "WNBA",
        "canonical_prop_type": "REB",
        "controlling_specialist": "wow.wnba-player-prop-generative-expert",
    }


def _route():
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "sport": "WNBA",
        "stat_type": "REB",
        "feature_schema_version": "PROP_FEATURES_V1",
        "model_family": "TEST_DISCRETE_V1",
        "model_artifact_version": "WNBA_REB_MODEL_V1",
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "probability_publishable": False,
        "can_execute": False,
    }


def _evidence(req):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ok": True,
        "code": "PROP_EVIDENCE_READY",
        "hydration_status": "PASS",
        "source_snapshot_id": req.source_snapshot_id,
        "captured_at": now,
        "player": req.player,
        "game_log": [10, 12, 11, 9, 14, 8, 13, 12, 10, 15],
        "box_score_log": [{"minutes": 34}] * 10,
        "role_status": {"status": "ACTIVE", "role": "STARTER"},
        "role_timestamp": now,
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"box_score_log": now},
        "evidence_version": "PROP_EVIDENCE_V1",
        "rate_provenance": "TEST_ROLE_VALID_L10_V1",
        "can_execute": False,
    }


def _publication_locked_lane():
    return {
        "capability_key": "PROP_PROBABILITY",
        "capability_status": "UNAVAILABLE",
        "evidence": {
            "blocker": "FORWARD_SHADOW_NOT_COMPLETED",
            "calibration_health_status": "BLOCKED",
        },
        "can_execute": False,
    }


def test_forward_shadow_block_is_calibration_publication_scoped():
    result = classify_probability_capability(_publication_locked_lane())
    assert result.publication_only_lock is True
    assert result.routing_capability_status == "AVAILABLE_FOR_RESEARCH"
    assert result.calibration_capability == "BLOCKED_OR_UNKNOWN"
    assert result.governed_publication_capability == "UNAVAILABLE"
    assert result.governed_publishable is False
    assert set(result.failed_contract_scope) == {"CALIBRATION", "PUBLICATION"}
    assert "FORWARD_SHADOW_NOT_COMPLETED" in result.blocker_codes


def test_true_model_failure_overrides_forward_shadow_scope():
    lane = _publication_locked_lane()
    lane["evidence"] = {
        "blockers": ["FORWARD_SHADOW_NOT_COMPLETED", "MODEL_UNAVAILABLE"],
        "failed_contract_scope": ["CALIBRATION", "PUBLICATION"],
    }
    result = classify_probability_capability(lane)
    assert result.publication_only_lock is False
    assert result.routing_capability_status == "UNAVAILABLE"
    assert result.governed_publishable is False


def test_unknown_unavailable_capability_fails_closed_global():
    result = classify_probability_capability(
        {
            "capability_status": "UNAVAILABLE",
            "evidence": {"reason": "UNCLASSIFIED_RUNTIME_FAILURE"},
        }
    )
    assert result.publication_only_lock is False
    assert result.routing_capability_status == "UNAVAILABLE"
    assert result.failed_contract_scope == ("GLOBAL",)


def test_scoped_runtime_alias_preserves_raw_source_state(monkeypatch):
    monkeypatch.setattr(api, "_original_runtime_capability", lambda _key: _publication_locked_lane())
    lane = api._scoped_runtime_capability("PROP_PROBABILITY")
    assert lane["source_capability_status"] == "UNAVAILABLE"
    assert lane["capability_status"] == "AVAILABLE"
    assert lane["capability_status_semantics"] == "RESEARCH_ROUTING_COMPATIBILITY_ALIAS"
    assert lane["routing_capability_status"] == "AVAILABLE_FOR_RESEARCH"
    assert lane["governed_publication_capability"] == "UNAVAILABLE"
    assert lane["governed_publishable"] is False


def test_publication_lock_returns_raw_specialist_research_without_calibration_or_ledger_write(monkeypatch):
    req = _request()
    monkeypatch.setattr(api, "_original_runtime_capability", lambda _key: _publication_locked_lane())
    monkeypatch.setattr(api.prod, "_reject_llp_prop_identity", lambda _identity: "WOW_BETTING_ENGINE")
    monkeypatch.setattr(api.prod.base_api, "_controlling_specialist_provider", lambda _sport, _stat: _specialist())
    monkeypatch.setattr(api.market_api, "_prop_route_artifact", lambda _sport, _stat: _route())
    monkeypatch.setattr(api.market_api, "repair_prop_evidence", lambda *_args, **_kwargs: _evidence(req))
    monkeypatch.setattr(api.prod, "get_client", lambda: object())
    monkeypatch.setattr(
        api,
        "_raw_specialist_evidence",
        lambda **_kwargs: {
            "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
            "model_family": "TEST_DISCRETE_V1",
            "raw_model_probability": 0.64,
            "probability_more": 0.64,
            "probability_less": 0.34,
            "push_probability": 0.02,
            "calibrated_probability": None,
            "calibrated_probability_lower_bound": None,
            "calibrated_probability_upper_bound": None,
            "calibration_method": None,
            "bounds_method_version": None,
            "probability_claim_status": "SPECIALIST_RAW_RESEARCH_ONLY",
            "probability_publishable": False,
            "governed_publishable": False,
            "can_execute": False,
        },
    )

    def _must_not_use_normal_published_path(*_args, **_kwargs):
        raise AssertionError("published/calibrated path must not run during publication lock")

    monkeypatch.setattr(api, "_original_score_prop", _must_not_use_normal_published_path)
    monkeypatch.setattr(
        api,
        "resolve_market_prior",
        lambda *_args, **_kwargs: SimpleNamespace(
            market_prior_available=False,
            market_prior_quality="NO_QUALIFYING_MARKET",
            market_prior_probability=None,
            reference_market_probability_raw=None,
            reference_market_side=None,
            reference_market_price=None,
        ),
    )

    result = api.score_prop(req, x_wow_model_identity="WOW_BETTING_ENGINE")
    assert result["ok"] is True
    assert result["research_only"] is True
    assert result["prediction"] is None
    assert result["specialist_model_capability"] == "AVAILABLE"
    assert result["specialist_model_status"] == "COMPLETED_RESEARCH_ONLY"
    assert result["calibration_status"] == "UNKNOWN_OR_BLOCKED"
    assert result["governed_publishable"] is False
    assert result["probability_claim_status"] == "SPECIALIST_RAW_RESEARCH_ONLY"
    assert result["terminal_ceiling"] == "MODEL_QUALIFIED_HOLD"
    assert set(result["failed_contract_scope"]) == {"CALIBRATION", "PUBLICATION"}
    assert result["model_evidence"]["raw_model_probability"] == 0.64
    assert result["model_evidence"]["calibrated_probability"] is None
    assert result["model_evidence"]["calibrated_probability_lower_bound"] is None
    assert result["model_evidence"]["calibrated_probability_upper_bound"] is None
    assert result["backend_traversal"]["dynamic_calibration"] == "NOT_INVOKED_PUBLICATION_LOCK"
    assert result["backend_traversal"]["prediction_ledger_write"] == "NOT_ATTEMPTED_PUBLICATION_BLOCKED"
    assert result["objective_lanes"]["MARKET"]["market_prior_weight"] == 0.0
    assert result["can_execute"] is False


def test_non_publication_failure_delegates_to_existing_fail_closed_path(monkeypatch):
    req = _request()
    lane = {
        "capability_status": "UNAVAILABLE",
        "evidence": {"reason": "DATA_PROVIDER_OUTAGE"},
        "can_execute": False,
    }
    monkeypatch.setattr(api, "_original_runtime_capability", lambda _key: lane)
    monkeypatch.setattr(api.prod, "_reject_llp_prop_identity", lambda _identity: "WOW_BETTING_ENGINE")
    sentinel = {"delegated": True, "probability_publishable": False, "can_execute": False}
    monkeypatch.setattr(api, "_original_score_prop", lambda *_args, **_kwargs: sentinel)
    assert api.score_prop(req, x_wow_model_identity="WOW_BETTING_ENGINE") is sentinel
