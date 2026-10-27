"""Contract tests for the V17 advisory continuous-intelligence cycle."""
from unittest.mock import patch

import pytest

from v17.continuous_intelligence_cycle import run_continuous_intelligence_cycle

BASE = "v17.continuous_intelligence_cycle."
AUTHORITY = "ADVISORY_ONLY"


def payload(**fields):
    return {"status": "PASS", "authority": AUTHORITY, "can_execute": False, **fields}


def test_complete_cycle_captures_both_lanes_before_summary_and_compounding():
    order = []
    prop = lambda *a, **k: (order.append("prop") or payload(candidate_n=2, observation_n=2, skipped_n=0))
    event = lambda *a, **k: (order.append("event") or payload(candidate_n=3, observation_n=3, skipped_n=0))
    summary = lambda *a, **k: (order.append("summary") or payload(observation_n=5, cohort_n=2, hypothesis_n=1, hypotheses=[{"hypothesis_id": "research-only"}]))
    memory = lambda *a, **k: (order.append("memory") or payload(observation_n=5, challenger_proposal_n=1))

    with patch(BASE + "capture_settled_prop_observations", side_effect=prop), \
         patch(BASE + "capture_settled_event_observations", side_effect=event), \
         patch(BASE + "intelligence_summary", side_effect=summary) as summarizer, \
         patch(BASE + "run_compounding_memory_cycle", side_effect=memory) as compound:
        result = run_continuous_intelligence_cycle(object(), max_outcomes=100, min_samples=30)

    assert order == ["prop", "event", "summary", "memory"]
    assert result["status"] == "PASS"
    assert result["completed_stages"] == 4
    assert result["stages"][0]["candidate_n"] == 2
    assert result["stages"][1]["candidate_n"] == 3
    assert result["stages"][2]["hypothesis_n"] == 1
    assert result["can_execute"] is False
    assert result["automatic_promotion_allowed"] is False
    assert result["model_parameters_changed"] is False
    assert summarizer.call_args.kwargs["persist_hypotheses"] is True
    assert compound.call_args.kwargs["base_summary"]["hypothesis_n"] == 1


def test_independent_capture_failure_is_typed_and_downstream_stays_skipped():
    with patch(BASE + "capture_settled_prop_observations", side_effect=RuntimeError("hidden credential")), \
         patch(BASE + "capture_settled_event_observations", return_value=payload(candidate_n=1, observation_n=1)), \
         patch(BASE + "intelligence_summary") as summarizer, \
         patch(BASE + "run_compounding_memory_cycle") as compound:
        result = run_continuous_intelligence_cycle(object())

    assert result["status"] == "PARTIAL_FAILURE"
    assert result["stages"][0]["status"] == "FAILED"
    assert result["stages"][0]["error_type"] == "RuntimeError"
    assert result["stages"][1]["status"] == "PASS"
    assert result["stages"][2]["status"] == "SKIPPED_UPSTREAM_FAILURE"
    assert "hidden credential" not in str(result)
    summarizer.assert_not_called()
    compound.assert_not_called()


def test_unsafe_stage_fails_closed_without_claiming_learning():
    with patch(BASE + "capture_settled_prop_observations", return_value={**payload(), "can_execute": True}), \
         patch(BASE + "capture_settled_event_observations", return_value=payload()), \
         patch(BASE + "intelligence_summary") as summarizer:
        result = run_continuous_intelligence_cycle(object())
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["stages"][0]["error_type"] == "ValueError"
    assert result["can_execute"] is False
    summarizer.assert_not_called()


def test_summary_failure_preserves_capture_evidence_without_compounding():
    with patch(BASE + "capture_settled_prop_observations", return_value=payload(candidate_n=1, observation_n=1)), \
         patch(BASE + "capture_settled_event_observations", return_value=payload(candidate_n=0, observation_n=0)), \
         patch(BASE + "intelligence_summary", side_effect=ValueError("bad data")), \
         patch(BASE + "run_compounding_memory_cycle") as compound:
        result = run_continuous_intelligence_cycle(object())
    assert result["stages"][0]["status"] == "PASS"
    assert result["stages"][1]["status"] == "PASS"
    assert result["stages"][2]["status"] == "FAILED"
    assert result["stages"][3]["status"] == "SKIPPED_UPSTREAM_FAILURE"
    compound.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"max_outcomes": 0},
    {"max_outcomes": 5001},
    {"min_samples": 0},
    {"min_samples": True},
    {"max_observations": 20001},
    {"max_observations": "5000"},
])
def test_invalid_bounds_rejected_before_data_access(kwargs):
    with patch(BASE + "capture_settled_prop_observations") as source:
        with pytest.raises(ValueError, match="INVALID_"):
            run_continuous_intelligence_cycle(object(), **kwargs)
    source.assert_not_called()


def test_new_http_route_uses_existing_action_auth_and_does_not_replace_legacy():
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient
    from v17.core_intelligence_compounding_routes import install_compounding_intelligence_routes_read_only

    app = FastAPI()
    db = object()

    def require_key(authorization: str | None = Header(default=None)):
        if authorization != "Bearer contract-test":
            raise HTTPException(status_code=401)

    with patch(
        "v17.core_intelligence_compounding_routes.run_continuous_intelligence_cycle",
        return_value={"status": "PASS", "can_execute": False},
    ) as runner:
        assert install_compounding_intelligence_routes_read_only(
            app, get_client_fn=lambda: db, auth_dependency=require_key
        )
        client = TestClient(app)
        assert client.post("/v17/intelligence/continuous-cycle").status_code == 401
        assert client.post(
            "/v17/intelligence/continuous-cycle",
            headers={"Authorization": "Bearer contract-test"},
        ).status_code == 200
        assert runner.call_count == 1
        assert runner.call_args.args[0] is db
        assert runner.call_args.kwargs["min_samples"] == 30
        assert any(route.path == "/v17/intelligence/full-cycle" for route in app.routes)


def test_empty_prop_outcomes_keep_authority_and_do_not_block_learning():
    from types import SimpleNamespace
    from v17.core_intelligence_runtime import capture_settled_prop_observations

    class EmptyOutcomes:
        def table(self, name):
            assert name == "wow_outcomes"
            return self

        def select(self, *args):
            return self

        def order(self, *args, **kwargs):
            return self

        def limit(self, n):
            return self

        def execute(self):
            return SimpleNamespace(data=[])

    capture = capture_settled_prop_observations(EmptyOutcomes())
    assert capture == {
        "status": "PASS", "candidate_n": 0, "observation_n": 0,
        "authority": AUTHORITY, "can_execute": False,
    }

    with patch(BASE + "capture_settled_prop_observations", return_value=capture), \
         patch(BASE + "capture_settled_event_observations", return_value=payload(candidate_n=0, observation_n=0)), \
         patch(BASE + "intelligence_summary", return_value=payload(observation_n=0, cohort_n=0, hypothesis_n=0)), \
         patch(BASE + "run_compounding_memory_cycle", return_value=payload(observation_n=0, challenger_proposal_n=0)):
        result = run_continuous_intelligence_cycle(object())
    assert result["status"] == "PASS"
    assert result["completed_stages"] == 4
    assert result["stages"][0]["observation_n"] == 0
    assert result["stages"][1]["observation_n"] == 0
    assert result["can_execute"] is False


def test_compounding_failure_preserves_summary_but_marks_cycle_partial():
    with patch(BASE + "capture_settled_prop_observations", return_value=payload(candidate_n=2, observation_n=2)), \
         patch(BASE + "capture_settled_event_observations", return_value=payload(candidate_n=2, observation_n=2)), \
         patch(BASE + "intelligence_summary", return_value=payload(observation_n=4, cohort_n=2, hypothesis_n=1)), \
         patch(BASE + "run_compounding_memory_cycle", side_effect=ConnectionError("hidden database secret")):
        result = run_continuous_intelligence_cycle(object())
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["completed_stages"] == 3
    assert result["stages"][2]["status"] == "PASS"
    assert result["stages"][3]["error_type"] == "ConnectionError"
    assert "hidden database secret" not in str(result)


def test_void_family_excluded_even_if_upstream_hit_flag_is_inconsistent():
    from v17.core_intelligence import build_learning_observation
    prediction = {
        "prediction_id": "00000000-0000-0000-0000-000000000009",
        "calibrated_probability": 0.84,
        "calibrated_probability_lower_bound": 0.76,
        "sport": "MLB",
    }
    for result in (
        "VOID_PLAYER_DNP", "VOID_NOT_STARTING_PITCHER",
        "VOID_NOT_STARTING_BATTER", "VOID",
    ):
        observation = build_learning_observation(
            prediction,
            {"prediction_id": prediction["prediction_id"],
             "official_result": result,
             "hit": True,
             "settlement_timestamp": "2026-10-27T01:00:00Z"},
        )
        assert observation.outcome_target is None
        assert observation.brier_score is None
        assert observation.log_loss is None
        assert observation.can_execute is False


def test_raw_only_forecast_is_not_calibration_gradeable():
    from v17.core_intelligence import build_learning_observation, summarize_cohort
    prediction = {
        "prediction_id": "00000000-0000-0000-0000-000000000010",
        "sport": "MLB",
        "raw_model_probability": 0.92,
        "model_probability": 0.94,
    }
    observation = build_learning_observation(
        prediction, {"official_result": "LOSS", "settlement_timestamp": "2026-10-27T01:00:00Z"}
    )
    assert observation.probability is None
    assert observation.probability_source is None
    assert observation.calibration_bucket is None
    assert observation.brier_score is None
    assert observation.log_loss is None
    assert summarize_cohort([observation], min_samples=1).scored_n == 0


def test_exact_away_winner_without_scores_is_home_loss_not_unresolved():
    from v17.core_intelligence_event_runtime import _home_result
    source = {"home_team": "Houston Astros", "away_team": "Texas Rangers"}
    assert _home_result(
        source,
        {"official_winner": "texas rangers", "home_score": None, "away_score": None, "void": False}
    ) == "LOSS"
    assert _home_result(
        source,
        {"official_winner": "Unknown Alias", "home_score": None, "away_score": None, "void": False}
    ) is None
    assert _home_result(
        source,
        {"official_winner": "texas rangers", "home_score": 7, "away_score": 6, "void": False}
    ) == "WIN"  # Official score takes precedence over name-only fallback.


def test_prop_backfill_is_batched_and_idempotent_by_observation_id():
    from types import SimpleNamespace
    from v17.core_intelligence_runtime import capture_settled_prop_observations

    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(251)]
    outcomes = [
        {"prediction_id": uid, "official_result": "HIT", "actual_stat": 6,
         "hit": True, "push": False, "void": False, "settlement_source": "official",
         "settlement_timestamp": "2026-10-26T20:00:00Z"}
        for uid in ids
    ]
    predictions = [
        {"prediction_id": uid, "sport": "MLB", "market_type": "PLAYER_PROP",
         "stat_type": "PITCHER_STRIKEOUTS", "line": 4.5, "direction": "MORE",
         "calibrated_probability": 0.60, "calibrated_probability_lower_bound": 0.55}
        for uid in ids
    ]
    class _Query:
        def __init__(self, db, name):
            self.db, self.name, self.ids = db, name, None
        def select(self, *_a): return self
        def order(self, *_a, **_kw): return self
        def limit(self, _v): return self
        def in_(self, _field, values):
            self.ids = set(values)
            return self
        def upsert(self, rows, *, on_conflict, ignore_duplicates):
            assert self.name == "wow_intelligence_observations"
            assert on_conflict == "observation_id" and ignore_duplicates is True
            self.db.writes.append(list(rows))
            return self
        def execute(self):
            if self.name == "wow_outcomes":
                return SimpleNamespace(data=outcomes)
            if self.name == "wow_predictions":
                return SimpleNamespace(data=[x for x in predictions if x["prediction_id"] in self.ids])
            return SimpleNamespace(data=[])
    class _DB:
        def __init__(self): self.writes = []
        def table(self, name): return _Query(self, name)
    db = _DB()
    result = capture_settled_prop_observations(db, max_outcomes=251)
    assert result["status"] == "PASS"
    assert result["candidate_n"] == result["observation_n"] == 251
    assert [len(x) for x in db.writes] == [250, 1]
    assert len({x["observation_id"] for batch in db.writes for x in batch}) == 251
    assert all(x["can_execute"] is False for batch in db.writes for x in batch)


def test_independent_event_calibration_deduplicates_snapshots_and_rejects_bad_inputs():
    from v17.independent_event_accuracy import evaluate_independent_events

    def record(id, event, p, *, model_time="2026-09-20T18:00:00Z", score=(3, 2)):
        return {
            "event_prediction_id": id, "official_event_id": event,
            "sport": "MLB", "model_version": "MLB_V1",
            "event_start_time": "2026-09-20T21:00:00Z",
            "model_timestamp": model_time,
            "calibrated_home_probability": p,
            "calibrated_home_lower_bound": min(0.4, p),
            "home_score": score[0], "away_score": score[1],
            "void": False,
        }

    rows = [
        record("p1", "game-1", 0.55, model_time="2026-09-20T16:00:00Z"),
        record("p2", "game-1", 0.80, model_time="2026-09-20T20:00:00Z"),
        record("p3", "game-2", 0.60, score=(2, 4)),
        record("p4", "game-3", 0.75, score=(1, 0)),
        record("p5", "game-3", 0.75, score=(0, 1)),  # contradictory settlement
        record("p6", "game-4", 0.7, model_time="2026-09-20T22:00:00Z"),
        {**record("p7", "game-5", 0.6), "model_version": "OTHER_MODEL"},
    ]
    result = evaluate_independent_events(
        rows, model_version="MLB_V1",
        holdout_start="2026-09-01T00:00:00Z",
        holdout_end="2026-10-01T00:00:00Z",
        min_independent_events=3,
    )
    assert result["input_rows"] == 7
    assert result["independent_events"] == 2
    assert result["duplicate_snapshot_rows_excluded"] == 1
    assert result["conflicting_events_excluded"] == 1
    assert result["exclusion_counts"]["NOT_PREGAME_FROZEN"] == 1
    assert result["exclusion_counts"]["OTHER_MODEL_VERSION"] == 1
    # Latest game-1 forecast (0.80, WIN), plus game-2 (0.60, LOSS).
    assert result["brier"] == pytest.approx(((0.8-1)**2 + (0.6-0)**2) / 2)
    assert result["evidence_status"] == "WATCH_ONLY_THIN_COHORT"
    assert result["automatic_promotion_allowed"] is False
    assert result["can_execute"] is False


def test_independent_event_evaluator_requires_explicit_chronological_holdout():
    from v17.independent_event_accuracy import evaluate_independent_events
    with pytest.raises(ValueError, match="CHRONOLOGICAL_HOLDOUT_REQUIRED"):
        evaluate_independent_events([], model_version="MLB_V1", holdout_start="bad",
                                    holdout_end="2026-10-01T00:00:00Z")
    with pytest.raises(ValueError, match="MIN_INDEPENDENT_EVENTS_INVALID"):
        evaluate_independent_events([], model_version="MLB_V1",
                                    holdout_start="2026-09-01T00:00:00Z",
                                    holdout_end="2026-10-01T00:00:00Z",
                                    min_independent_events=0)
