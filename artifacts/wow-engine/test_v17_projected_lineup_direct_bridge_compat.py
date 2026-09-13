from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17.projected_lineup_direct_bridge_compat import (
    install_projected_lineup_direct_bridge_compat,
)


def _direct_lineup_only_failure():
    return HTTPException(
        status_code=422,
        detail={
            "code": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "sport_model_selected": True,
            "sport_model_invoked": False,
            "missing_fields": ["away_lineup_status", "home_lineup_status"],
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        },
    )


def _valid_projected_receipt():
    return {
        "ok": True,
        "status": "MODEL_SCORED_HELD",
        "code": "REAL_FITTED_MODEL_PATH_PROVEN",
        "shadow_event_id": "shadow-1",
        "score_snapshot_id": "score-1",
        "server_snapshot_id": "snapshot-1",
        "score_status": "SHADOW_SCORED_LINEUP_PENDING",
        "lineup_status": "NOT_YET_AVAILABLE",
        "feature_hydration_status": "PASS",
        "calibration_health_status": "PASS",
        "governed_probability_capability": "AVAILABLE",
        "ratification_status": "RATIFIED",
        "current_publication_blockers": [
            "LINEUP_NOT_CONFIRMED",
            "OFFICIAL_LINEUP_REFRESH_OFFICIAL_LINEUP_NOT_AVAILABLE",
            "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED",
        ],
        "scoring_evidence_produced": True,
        "probability_fields_withheld": True,
        "probability_publishable": False,
        "can_execute": False,
    }


def _market_api(direct, legacy):
    event_api = SimpleNamespace(
        score_event=direct,
        _v17_original_score_event=legacy,
    )
    return SimpleNamespace(prod=SimpleNamespace(event_api=event_api)), event_api


def test_lineup_only_direct_failure_uses_strict_held_receipt_without_numeric_leak():
    calls = {"direct": 0, "legacy": 0}

    def direct(req):
        calls["direct"] += 1
        raise _direct_lineup_only_failure()

    def legacy(req):
        calls["legacy"] += 1
        return _valid_projected_receipt()

    market_api, event_api = _market_api(direct, legacy)
    assert install_projected_lineup_direct_bridge_compat(market_api=market_api) is True

    result = event_api.score_event(SimpleNamespace())
    assert calls == {"direct": 1, "legacy": 1}
    assert result["code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert result["probability_fields_withheld"] is True
    assert result["probability_publishable"] is False
    assert result["projected_lineup_direct_bridge_compat"]["status"] == "PASS_HELD_RECEIPT_ONLY"
    assert result["projected_lineup_direct_bridge_compat"]["probabilities_recomputed"] is False
    assert result["projected_lineup_direct_bridge_compat"]["probabilities_exposed"] is False
    assert result["can_execute"] is False
    assert "calibrated_home_probability" not in result


def test_non_lineup_input_failure_does_not_fallback():
    calls = {"legacy": 0}

    def direct(req):
        exc = _direct_lineup_only_failure()
        exc.detail["missing_fields"] = ["home_lineup_status", "venue_name"]
        raise exc

    def legacy(req):
        calls["legacy"] += 1
        return _valid_projected_receipt()

    market_api, event_api = _market_api(direct, legacy)
    install_projected_lineup_direct_bridge_compat(market_api=market_api)

    with pytest.raises(HTTPException) as caught:
        event_api.score_event(SimpleNamespace())
    assert caught.value.status_code == 422
    assert calls["legacy"] == 0


def test_invalid_or_numeric_legacy_receipt_cannot_bypass_direct_failure():
    direct_exc = _direct_lineup_only_failure()

    def direct(req):
        raise direct_exc

    def legacy(req):
        receipt = _valid_projected_receipt()
        receipt["calibrated_home_probability"] = 0.62
        return receipt

    market_api, event_api = _market_api(direct, legacy)
    install_projected_lineup_direct_bridge_compat(market_api=market_api)

    with pytest.raises(HTTPException) as caught:
        event_api.score_event(SimpleNamespace())
    assert caught.value is direct_exc


def test_legacy_publication_after_refresh_retries_direct_specialist_once():
    calls = {"direct": 0, "legacy": 0}
    direct_result = {
        "code": "GOVERNED_MODEL_PROBABILITY_PROSPECTIVE",
        "probability_publishable": True,
        "can_execute": False,
    }

    def direct(req):
        calls["direct"] += 1
        if calls["direct"] == 1:
            raise _direct_lineup_only_failure()
        return direct_result

    def legacy(req):
        calls["legacy"] += 1
        return {
            "code": "GOVERNED_PROBABILITY_PUBLISHED",
            "probability_publishable": True,
            "can_execute": False,
        }

    market_api, event_api = _market_api(direct, legacy)
    install_projected_lineup_direct_bridge_compat(market_api=market_api)

    assert event_api.score_event(SimpleNamespace()) == direct_result
    assert calls == {"direct": 2, "legacy": 1}


def test_install_is_idempotent():
    def direct(req):
        return {"code": "DIRECT", "can_execute": False}

    def legacy(req):
        return _valid_projected_receipt()

    market_api, event_api = _market_api(direct, legacy)
    assert install_projected_lineup_direct_bridge_compat(market_api=market_api) is True
    wrapped = event_api.score_event
    assert install_projected_lineup_direct_bridge_compat(market_api=market_api) is True
    assert event_api.score_event is wrapped
