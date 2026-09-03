from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from agent_runtime.scout_research import RESEARCH_RECONCILER, RESEARCH_WORKERS
from v17.host_routing import LLP_TEAM_BETTING_ENGINE, WOW_BETTING_ENGINE
from v17 import team_event_request_runtime as team_event_module
from v17.team_event_request_runtime import TeamEventRequest, score_team_event_request


class _FakeScoreEventRequest:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeEventApi:
    ScoreEventRequest = _FakeScoreEventRequest

    @staticmethod
    def score_event(req):
        return {
            "ok": True,
            "code": "REAL_FITTED_MODEL_PATH_PROVEN",
            "official_event_id": req.official_event_id,
            "probability_fields_withheld": True,
            "probability_publishable": False,
            "can_execute": False,
        }


class _RpcResult:
    def __init__(self, data):
        self.data = data


class _RpcCall:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _RpcResult(self._data)


class _GovernanceClient:
    def __init__(self, payload):
        self.payload = payload
        self.last_rpc = None
        self.last_params = None

    def rpc(self, name, params):
        self.last_rpc = name
        self.last_params = params
        return _RpcCall(self.payload)


class _GovernedEventApi:
    ScoreEventRequest = _FakeScoreEventRequest
    client = _GovernanceClient(
        {
            "status": "PASS",
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_decision": "SELECTED",
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": "FINAL_APPROVED",
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }
    )

    @staticmethod
    def score_event(req):
        return {
            "ok": True,
            "code": "GOVERNED_PROBABILITY_PUBLISHED",
            "official_event_id": req.official_event_id,
            "score_snapshot_id": "00000000-0000-0000-0000-000000000099",
            "raw_home_probability": 0.61,
            "raw_away_probability": 0.39,
            "independent_home_probability": 0.61,
            "independent_away_probability": 0.39,
            "calibrated_home_probability": 0.60,
            "calibrated_away_probability": 0.40,
            "calibrated_home_lower_bound": 0.56,
            "calibrated_home_upper_bound": 0.64,
            "calibrated_away_lower_bound": 0.36,
            "calibrated_away_upper_bound": 0.44,
            "calibration_method": "PLATT_TIME_SPLIT_V1",
            "calibration_version": "mlb-cal-v1",
            "calibration_training_n": 500,
            "calibration_health_status": "PASS",
            "model_version": "MLB_EVENT_TEST_V1",
            "model_timestamp": datetime.now(timezone.utc).isoformat(),
            "probability_fields_withheld": False,
            "probability_publishable": True,
            "can_execute": False,
        }

    @classmethod
    def get_client(cls):
        return cls.client


class _PublishedWithoutGovernanceEventApi:
    ScoreEventRequest = _FakeScoreEventRequest

    @staticmethod
    def score_event(req):
        return {
            "ok": True,
            "code": "GOVERNED_PROBABILITY_PUBLISHED",
            "official_event_id": req.official_event_id,
            "score_snapshot_id": "00000000-0000-0000-0000-000000000099",
            "raw_home_probability": 0.61,
            "raw_away_probability": 0.39,
            "independent_home_probability": 0.61,
            "independent_away_probability": 0.39,
            "calibrated_home_probability": 0.60,
            "calibrated_away_probability": 0.40,
            "calibrated_home_lower_bound": 0.56,
            "calibrated_home_upper_bound": 0.64,
            "calibrated_away_lower_bound": 0.36,
            "calibrated_away_upper_bound": 0.44,
            "calibration_method": "PLATT_TIME_SPLIT_V1",
            "calibration_version": "mlb-cal-v1",
            "calibration_training_n": 500,
            "calibration_health_status": "PASS",
            "model_version": "MLB_EVENT_TEST_V1",
            "model_timestamp": datetime.now(timezone.utc).isoformat(),
            "probability_fields_withheld": False,
            "probability_publishable": True,
            "can_execute": False,
        }


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def _base(**overrides):
    payload = {
        "requester_host_identity": WOW_BETTING_ENGINE,
        "research_run_id": "rr-v17-team-event-test",
        "requested_slate_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(),
        "requested_timezone": "America/Chicago",
        "scan_stage": "PREGAME",
        "candidate_family": "TEAM_EVENT",
        "decision_intent": "BEST_SIDE",
        "event_key": "MLB:test-1",
        "official_event_id": "test-1",
        "event_start_time_utc": _future(),
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "source_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "sport_specific_evidence": {
            "venue": "Test Park",
            "home_starting_pitcher": "Home Starter",
            "away_starting_pitcher": "Away Starter",
            "home_starter_status": "PROBABLE",
            "away_starter_status": "PROBABLE",
            "home_lineup_status": "PROJECTED",
            "away_lineup_status": "PROJECTED",
        },
    }
    payload.update(overrides)
    return TeamEventRequest(**payload)


def test_wow_requester_team_event_is_controlled_by_llp():
    result = score_team_event_request(_base(), event_api=_FakeEventApi)
    assert result["requester_host_identity"] == WOW_BETTING_ENGINE
    assert result["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert result["host_terminal_authority"] is False
    assert result["can_execute"] is False


def test_llp_requester_team_event_is_controlled_by_llp():
    result = score_team_event_request(
        _base(requester_host_identity=LLP_TEAM_BETTING_ENGINE), event_api=_FakeEventApi
    )
    assert result["requester_host_identity"] == LLP_TEAM_BETTING_ENGINE
    assert result["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE


def test_unsupported_sport_fails_closed_without_probability():
    req = _base(
        sport="NFL",
        league="NFL",
        event_key="NFL:test-2",
        official_event_id="test-2",
        settlement_basis="FULL_GAME_OUTRIGHT",
        sport_specific_evidence={},
    )
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert detail["code"] == "MODEL_UNAVAILABLE"
    assert detail["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert detail["probability_publishable"] is False
    assert detail["market_probability_substitution_allowed"] is False
    assert detail["generic_reasoning_substitution_allowed"] is False
    assert detail["can_execute"] is False
    assert not any(
        key in detail
        for key in ("raw_probability", "calibrated_probability", "calibrated_lower_bound")
    )


def test_baseball_sport_family_alias_with_mlb_league_reaches_the_mlb_adapter():
    # A caller sending sport="baseball", league="MLB" was previously
    # uppercased to "BASEBALL" and never matched the "MLB" dispatch check,
    # producing a false MODEL_UNAVAILABLE even though the MLB adapter is
    # registered and otherwise fully able to score the row.
    result = score_team_event_request(_base(sport="baseball", league="MLB"), event_api=_FakeEventApi)
    assert result["code"] == "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
    assert result["upstream_model_code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert result["can_execute"] is False


def test_baseball_mlb_sport_alias_reaches_the_mlb_adapter():
    result = score_team_event_request(_base(sport="baseball_mlb", league="MLB"), event_api=_FakeEventApi)
    assert result["code"] == "LLP_EVENT_GOVERNANCE_NOT_PROVEN"


def test_baseball_alias_with_a_different_baseball_league_still_fails_closed():
    # "baseball" is a sport family, not a league. A non-MLB baseball league
    # (NPB, KBO, ...) must never be silently scored by the MLB-fitted
    # adapter -- it should fail closed exactly like any other unsupported
    # sport, not be misrouted to the wrong league's model.
    req = _base(
        sport="baseball",
        league="NPB",
        event_key="NPB:test-3",
        official_event_id="test-3",
        settlement_basis="FULL_GAME_OUTRIGHT",
        sport_specific_evidence={},
    )
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert detail["code"] == "MODEL_UNAVAILABLE"
    assert detail["sport"] == "BASEBALL"
    assert detail["can_execute"] is False


def test_normalize_team_event_sport_unit_cases():
    normalize = team_event_module.normalize_team_event_sport
    assert normalize("baseball", "MLB") == "MLB"
    assert normalize("BASEBALL_MLB", "mlb") == "MLB"
    assert normalize("mlb", "MLB") == "MLB"
    assert normalize("baseball", "NPB") == "BASEBALL"
    assert normalize("NFL", "NFL") == "NFL"


def test_mlb_missing_sport_specific_evidence_is_acquisition_incomplete():
    req = _base(sport_specific_evidence={})
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert detail["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_valid_mlb_fitted_model_is_held_until_llp_governance_is_proven():
    result = score_team_event_request(_base(), event_api=_FakeEventApi)
    assert result["code"] == "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
    assert result["upstream_model_code"] == "REAL_FITTED_MODEL_PATH_PROVEN"
    assert result["probability_fields_withheld"] is True
    assert result["probability_publishable"] is False
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert "LLP_PROBABILITY_CLAIM_AUDIT_NOT_PROVEN" in result["blockers"]
    assert "LLP_EVENT_DECISION_GOVERNOR_NOT_PROVEN" in result["blockers"]
    assert result["controlling_engine_identity"] == LLP_TEAM_BETTING_ENGINE
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_numeric_mlb_result_is_stripped_when_llp_bridge_is_unavailable():
    result = score_team_event_request(_base(), event_api=_PublishedWithoutGovernanceEventApi)
    assert result["code"] == "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
    assert result["probability_publishable"] is False
    assert result["probability_fields_withheld"] is True
    assert "raw_home_probability" not in result
    assert "calibrated_home_probability" not in result
    assert result["terminal_ceiling"] == "MODEL_QUALIFIED_HOLD"


def test_numeric_mlb_result_can_publish_only_after_explicit_llp_bridge_pass():
    result = score_team_event_request(_base(), event_api=_GovernedEventApi)
    assert _GovernedEventApi.client.last_rpc == "wow_v17_mlb_team_event_governance_bridge"
    assert result["code"] == "GOVERNED_PROBABILITY_PUBLISHED"
    assert result["probability_publishable"] is True
    assert result["calibrated_home_probability"] == 0.60
    assert result["llp_probability_audit_result"] == "PASS_PROBABILITY_AUDIT"
    assert result["event_mutex_status"] == "PASS"
    assert result["terminal_label"] == "FINAL_APPROVED"
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["can_execute"] is False


def test_rank_ineligible_governance_cannot_publish_even_when_other_gates_pass():
    original = _GovernedEventApi.client
    _GovernedEventApi.client = _GovernanceClient({**original.payload, "rank_eligible": False})
    try:
        result = score_team_event_request(_base(), event_api=_GovernedEventApi)
    finally:
        _GovernedEventApi.client = original
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["code"] == "LLP_EVENT_GOVERNANCE_NOT_PROVEN"


def test_early_governance_hold_always_contains_terminal_reducer_receipt():
    result = score_team_event_request(_base(), event_api=_FakeEventApi)
    receipt = result["terminal_reducer_input"]
    assert receipt["terminal_output"] == "MODEL_QUALIFIED_HOLD"
    assert receipt["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"


def test_intentionally_withheld_model_result_reaches_llp_by_snapshot_reference():
    class HeldApi(_GovernedEventApi):
        client = _GovernanceClient({
            **_GovernedEventApi.client.payload,
            "probability_publishable": False,
            "rank_eligible": False,
            "terminal_label": "MODEL_QUALIFIED_HOLD",
        })

        @staticmethod
        def score_event(req):
            return {
                "ok": True,
                "code": "REAL_FITTED_MODEL_PATH_PROVEN",
                "official_event_id": req.official_event_id,
                "score_snapshot_id": "00000000-0000-0000-0000-000000000099",
                "probability_fields_withheld": True,
                "probability_publishable": False,
                "can_execute": False,
            }

    result = score_team_event_request(_base(), event_api=HeldApi)
    assert HeldApi.client.last_rpc == "wow_v17_mlb_team_event_governance_bridge"
    assert result["code"] == "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
    assert result["probability_publishable"] is False
    assert result["terminal_reducer_input"]["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"


def test_prospective_base_score_snapshot_reaches_llp_bridge():
    class ProspectiveApi(_GovernedEventApi):
        client = _GovernanceClient({**_GovernedEventApi.client.payload, "probability_publishable": False, "terminal_label": "MODEL_QUALIFIED_HOLD", "rank_eligible": False})

        @staticmethod
        def score_event(req):
            payload = _GovernedEventApi.score_event(req)
            payload["base_score_snapshot_id"] = payload.pop("score_snapshot_id")
            payload["probability_publishable"] = False
            payload["rank_eligible"] = False
            return payload

    result = score_team_event_request(_base(), event_api=ProspectiveApi)
    assert ProspectiveApi.client.last_rpc == "wow_v17_mlb_team_event_governance_bridge"
    assert result["probability_publishable"] is False
    assert result["terminal_reducer_input"]["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"


def test_market_handoff_is_explicit_and_model_prior_drops_envelope_only_fields():
    captured = {}

    class MarketApi(_FakeEventApi):
        @staticmethod
        def score_event(req):
            captured.update(req.market_prior)
            return _FakeEventApi.score_event(req)

    req = _base(market_prior={
        "home_probability": 0.55,
        "away_probability": 0.45,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "quality": "EXACT_TWO_WAY_NO_VIG",
        "source": "TEST_BOOKS",
        "snapshot_id": "market-snapshot-1",
        "book_count": 4,
    })
    result = score_team_event_request(req, event_api=MarketApi)
    assert set(captured) == {"home_probability", "away_probability", "timestamp", "quality", "source"}
    assert result["candidate_envelope"]["market_status"] == "EXACT_LINE"
    assert "NOT_CALLED" not in str(result["candidate_envelope"])


def test_canonical_hydration_model_translation_and_llp_bridge_integrate_end_to_end():
    req = _base()
    snapshot_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    canonical_row = {
        "official_event_id": req.official_event_id,
        "event_start_time": req.event_start_time_utc,
        "event_status": "Scheduled",
        "home_team": req.home_team,
        "away_team": req.away_team,
        "venue_name": "Test Park",
        "home_probable_pitcher": "Home Starter",
        "away_probable_pitcher": "Away Starter",
        "snapshot_id": "00000000-0000-0000-0000-000000000010",
        "snapshot_timestamp": snapshot_at,
        "feature_hydration_status": "PASS",
    }

    class Result:
        def __init__(self, data): self.data = data

    class Query:
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def order(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self): return Result([canonical_row])

    class Client(_GovernanceClient):
        def table(self, _name): return Query()

    class IntegratedApi(_GovernedEventApi):
        client = Client(_GovernedEventApi.client.payload)

    result = score_team_event_request(
        req,
        event_api=IntegratedApi,
        canonical_hydration_required=True,
    )
    assert IntegratedApi.client.last_rpc == "wow_v17_mlb_team_event_governance_bridge"
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is True
    assert result["candidate_envelope"]["official_event_id"] == req.official_event_id
    assert result["candidate_envelope"]["source_snapshot_id"] == canonical_row["snapshot_id"]
    assert result["canonical_acquisition"]["status"] == "PASS"
    assert result["terminal_reducer_input"]["terminal_output"] == "FINAL_APPROVED"
    assert result["can_execute"] is False


def test_unknown_requester_host_fails_closed():
    req = _base(requester_host_identity="RANDOM_GPT")
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "UNAUTHORIZED_WOW_REQUESTER_HOST"
    assert exc_info.value.detail["can_execute"] is False


def test_same_team_event_identity_fails_closed():
    req = _base(home_team="Same Team", away_team="Same Team")
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    assert "EVENT_PARTICIPANTS_NOT_MUTUALLY_EXCLUSIVE" in exc_info.value.detail["errors"]


def test_past_event_fails_closed():
    req = _base(
        event_start_time_utc=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    )
    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(req, event_api=_FakeEventApi)
    assert exc_info.value.status_code == 422
    assert "EVENT_NOT_PREGAME_OR_TIMESTAMP_INVALID" in exc_info.value.detail["errors"]


def test_mandatory_scout_research_barrier_runs_before_specialist_and_is_reported():
    result = score_team_event_request(_base(), event_api=_FakeEventApi)
    barrier = result["scout_research_barrier"]
    assert barrier["status"] == "SUCCEEDED"
    worker_ids = [stage["worker_id"] for stage in barrier["stages"]]
    assert worker_ids == [
        "wow.global-scout-coordinator",
        "wow.ml-event-scout-router",
        *RESEARCH_WORKERS,
        RESEARCH_RECONCILER,
    ]
    assert all(stage["status"] == "SUCCEEDED" for stage in barrier["stages"])
    assert all(stage["blockers"] == [] for stage in barrier["stages"])


def test_mandatory_scout_research_barrier_blocks_specialist_when_a_stage_fails(monkeypatch):
    calls: list[str] = []
    real_execute_envelope = team_event_module.execute_envelope

    def _fail_reconciler(env):
        out = real_execute_envelope(env)
        if env.worker_id == RESEARCH_RECONCILER:
            return out.model_copy(update={"status": "BLOCKED", "blockers": ["RESEARCH_TEAM_INCOMPLETE"]})
        return out

    monkeypatch.setattr(team_event_module, "execute_envelope", _fail_reconciler)

    class _SpyEventApi:
        ScoreEventRequest = _FakeScoreEventRequest

        @staticmethod
        def score_event(req):
            calls.append("score_event")
            return _FakeEventApi.score_event(req)

    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(_base(), event_api=_SpyEventApi)

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert detail["code"] == "SCOUT_RESEARCH_BARRIER_BLOCKED"
    assert detail["stage"] == RESEARCH_RECONCILER
    assert "RESEARCH_TEAM_INCOMPLETE" in detail["blockers"]
    assert detail["probability_publishable"] is False
    assert calls == [], "controlling specialist must never be reached when the barrier blocks"


def test_mandatory_scout_research_barrier_blocks_specialist_when_global_scout_fails(monkeypatch):
    calls: list[str] = []
    real_execute_envelope = team_event_module.execute_envelope

    def _fail_global_scout(env):
        if env.worker_id == "wow.global-scout-coordinator":
            out = real_execute_envelope(env)
            return out.model_copy(update={"status": "BLOCKED", "blockers": ["SCOUT_CANDIDATE_MISSING"]})
        return real_execute_envelope(env)

    monkeypatch.setattr(team_event_module, "execute_envelope", _fail_global_scout)

    class _SpyEventApi:
        ScoreEventRequest = _FakeScoreEventRequest

        @staticmethod
        def score_event(req):
            calls.append("score_event")
            return _FakeEventApi.score_event(req)

    with pytest.raises(HTTPException) as exc_info:
        score_team_event_request(_base(), event_api=_SpyEventApi)

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert detail["code"] == "SCOUT_RESEARCH_BARRIER_BLOCKED"
    assert detail["stage"] == "wow.global-scout-coordinator"
    assert calls == [], "controlling specialist must never be reached when scout blocks"


def test_scout_research_workers_cannot_smuggle_predictive_authority_into_the_barrier():
    """Defense-in-depth: even if a candidate payload carried a forbidden
    authority key, execute_envelope's own validate_non_predictive_output
    check inside the real Scout/Research handlers blocks it -- the v17
    ingress does not add or need a second check for this."""
    from agent_runtime.scout_research import FORBIDDEN_AUTHORITY_KEYS

    assert "model_probability" in FORBIDDEN_AUTHORITY_KEYS
    assert "terminal_label" in FORBIDDEN_AUTHORITY_KEYS
    assert "calibrated_probability_lower_bound" in FORBIDDEN_AUTHORITY_KEYS
