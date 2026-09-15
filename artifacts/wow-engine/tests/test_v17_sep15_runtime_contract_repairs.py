from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17 import market_evidence_sources as sources
from v17.sep15_runtime_contract_repairs import (
    install_market_prior_ingress_repair,
    install_post_mlb_bridge_repairs,
    install_rundown_v2_auth_repair,
    sanitize_model_market_prior,
)


def test_rundown_product_v2_uses_documented_header_auth():
    original = sources.PROVIDERS["RUNDOWN"]
    try:
        assert install_rundown_v2_auth_repair() is True
        provider = sources.PROVIDERS["RUNDOWN"]
        assert provider.auth_style == "header"
        assert provider.auth_name == "X-TheRundown-Key"
        assert provider.key_envs[0] == "THERUNDOWN_API_KEY"
        assert provider.endpoints["sports"] == "/api/v2/sports"
        assert provider.endpoints["events"] == "/api/v2/sports/{sport_id}/events/{date}"
    finally:
        sources.PROVIDERS["RUNDOWN"] = original


def test_market_prior_sanitizer_drops_envelope_only_fields_but_keeps_valid_prior():
    prior = {
        "home_probability": 0.55,
        "away_probability": 0.45,
        "timestamp": "2026-09-15T14:00:00Z",
        "quality": "CROSS_BOOK_NO_VIG",
        "source": "RUNDOWN_MARKET_EVIDENCE",
        "snapshot_id": "rundown:824466:2026-09-15T14:00:00Z",
        "book_count": 3,
    }
    assert sanitize_model_market_prior(prior) == {
        "home_probability": 0.55,
        "away_probability": 0.45,
        "timestamp": "2026-09-15T14:00:00Z",
        "quality": "CROSS_BOOK_NO_VIG",
        "source": "RUNDOWN_MARKET_EVIDENCE",
    }


@pytest.mark.parametrize(
    "prior",
    [
        {"snapshot_id": "only-envelope-field"},
        {"home_probability": 0.55, "away_probability": 0.45},
        {"home_probability": 0.7, "away_probability": 0.4, "timestamp": "2026-09-15T14:00:00Z"},
        {"home_probability": "bad", "away_probability": 0.45, "timestamp": "2026-09-15T14:00:00Z"},
        {"home_probability": 0.55, "away_probability": 0.45, "timestamp": "2026-09-15T14:00:00"},
    ],
)
def test_invalid_optional_market_prior_becomes_no_model_prior(prior):
    assert sanitize_model_market_prior(prior) is None


def test_market_prior_ingress_repair_is_fail_safe_for_optional_context():
    runtime = SimpleNamespace(_model_market_prior=lambda prior: prior)
    assert install_market_prior_ingress_repair(runtime) is True
    assert runtime._model_market_prior(None) is None
    assert runtime._model_market_prior({"snapshot_id": "incomplete"}) is None
    assert runtime._v17_sep15_market_prior_ingress_repair_installed is True


class _RpcResult:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _RpcResult(self.rows)


class _FakeClient:
    def __init__(self, receipt, score_rows=None):
        self.receipt = receipt
        self.score_rows = score_rows or []
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return _Query(self.receipt)

    def table(self, name):
        assert name == "wow_mlb_forward_score_snapshots"
        return _Query(self.score_rows)


def _pending_exc():
    return HTTPException(
        status_code=422,
        detail={
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_LINEUP_NOT_YET_AVAILABLE",
            "can_execute": False,
        },
    )


def _projected_receipt():
    return {
        "code": "REAL_FITTED_MODEL_PATH_PROVEN",
        "status": "MODEL_SCORED_HELD",
        "can_execute": False,
        "score_status": "SHADOW_SCORED_LINEUP_PENDING",
        "lineup_status": "NOT_YET_AVAILABLE",
        "model_version": "MLB_V2C_SHARED_NB_2024_R1",
        "model_timestamp": "2026-09-15T05:05:00+00:00",
        "shadow_event_id": "shadow-1",
        "score_snapshot_id": "score-1",
        "server_snapshot_id": "snap-1",
        "server_snapshot_timestamp": "2026-09-15T05:02:00+00:00",
        "ratification_status": "RATIFIED",
        "controlling_specialist": "wow.mlb-game-win-probability-expert",
        "probability_publishable": False,
        "feature_hydration_status": "PASS",
        "calibration_health_status": "PASS",
        "scoring_evidence_produced": True,
        "probability_fields_withheld": True,
        "current_publication_blockers": [
            "LINEUP_NOT_CONFIRMED",
            "OFFICIAL_LINEUP_REFRESH_OFFICIAL_LINEUP_NOT_AVAILABLE",
            "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED",
        ],
        "governed_probability_capability": "AVAILABLE",
    }


def _score_row():
    return {
        "score_snapshot_id": "score-1",
        "shadow_event_id": "shadow-1",
        "model_timestamp": "2026-09-15T05:05:00+00:00",
        "model_version": "MLB_V2C_SHARED_NB_2024_R1",
        "calibration_id": "cal-1",
        "calibration_method": "LOGIT_INTERCEPT_POOLED_2022_2024",
        "raw_home_probability": 0.56,
        "raw_away_probability": 0.44,
        "calibrated_home_probability": 0.54,
        "calibrated_away_probability": 0.46,
        "home_lower_bound": 0.50,
        "home_upper_bound": 0.58,
        "away_lower_bound": 0.42,
        "away_upper_bound": 0.50,
        "home_bound_status": "PASS",
        "away_bound_status": "PASS",
        "tie_after_9_probability": 0.10,
        "lineup_status_at_score": "NOT_YET_AVAILABLE",
        "score_status": "SHADOW_SCORED_LINEUP_PENDING",
        "blockers": ["LINEUP_NOT_CONFIRMED"],
        "probability_publishable": False,
        "can_execute": False,
    }


def _score_request():
    return SimpleNamespace(
        official_event_id="824466",
        event_start_time_utc="2026-09-15T22:40:00+00:00",
        requested_slate_date="2026-09-15",
        home_team="Cincinnati Reds",
        away_team="Los Angeles Dodgers",
        venue="Great American Ball Park",
        home_starting_pitcher="Rhett Lowder",
        away_starting_pitcher="Yoshinobu Yamamoto",
        source_snapshot_id="snap-1",
        latest_material_update_timestamp="2026-09-15T05:02:00+00:00",
    )


def test_prelineup_bridge_recovers_only_ratified_immutable_projected_score():
    client = _FakeClient(_projected_receipt(), [_score_row()])

    class EventApi:
        def get_client(self):
            return client

        def score_event(self, _req):
            raise _pending_exc()

    event_api = EventApi()
    captured = {}

    def governance(_req, _route, model_result, envelope=None, *, event_api=None):
        captured.update(model_result)
        return dict(model_result)

    runtime = SimpleNamespace(_run_mlb_llp_governance=governance)
    market_api = SimpleNamespace(prod=SimpleNamespace(event_api=event_api))
    assert install_post_mlb_bridge_repairs(market_api=market_api, team_runtime=runtime) is True

    req = _score_request()
    held = event_api.score_event(req)
    assert held["code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert held["sport_model_invoked"] is True
    assert held["sport_model_invocation_source"] == "IMMUTABLE_PROJECTED_SCORE_SNAPSHOT"
    assert held["probability_fields_withheld"] is True
    assert held["rank_eligible"] is False
    assert held["can_execute"] is False
    assert client.rpc_calls[0][0] == "wow_mlb_score_event_bridge"

    out = runtime._run_mlb_llp_governance(
        req,
        SimpleNamespace(),
        held,
        envelope=None,
        event_api=event_api,
    )
    assert out["projected_lineup_score_rehydration"]["status"] == "PASS"
    assert out["sporting_probability_completed"] is True
    assert out["probability_fields_withheld"] is False
    assert out["probability_package_valid"] is True
    assert out["dynamic_calibration_complete"] is True
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False
    assert captured["calibration_version"] == "cal-1"


def test_non_lineup_canonical_failure_is_not_rewritten_or_recovered():
    client = _FakeClient(_projected_receipt())

    original = HTTPException(
        status_code=422,
        detail={
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE",
            "missing_fields": ["home_probable_pitcher"],
            "can_execute": False,
        },
    )

    class EventApi:
        def get_client(self):
            return client

        def score_event(self, _req):
            raise original

    event_api = EventApi()
    runtime = SimpleNamespace(
        _run_mlb_llp_governance=lambda _req, _route, model_result, envelope=None, *, event_api=None: model_result
    )
    market_api = SimpleNamespace(prod=SimpleNamespace(event_api=event_api))
    assert install_post_mlb_bridge_repairs(market_api=market_api, team_runtime=runtime) is True

    with pytest.raises(HTTPException) as exc_info:
        event_api.score_event(_score_request())
    assert exc_info.value.detail["blocker_code"] == "MLB_TEAM_EVENT_CANONICAL_SNAPSHOT_UNAVAILABLE"
    assert client.rpc_calls == []
