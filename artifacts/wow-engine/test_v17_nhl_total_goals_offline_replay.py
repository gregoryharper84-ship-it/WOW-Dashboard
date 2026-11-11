"""Strict offline cohort admission for NHL total-goals fitted research replay."""
from copy import deepcopy
from pathlib import Path
import json
import pytest

from v17.nhl_total_goals_offline_replay import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE, NHLTotalsOfflineReplayError,
    replay_verified_nhl_totals_research,
)


def fixtures():
    root = Path(__file__).parent / "tests" / "fixtures"
    d1 = json.loads((root / "nhl_totals_d1_schedule_join_three_games.json").read_text())["rows"]
    source = json.loads((root / "nhl_official_schedule_regimes_NYR_20242025.json").read_text())
    d1 = [dict(row, source_retrieved_at="2026-10-26T00:00:00Z") for row in d1]
    schedules = [{"team": "NYR", "season": 20242025,
                  "payload": {"games": source["official_completed_samples"]}}]
    return d1, schedules


def test_small_real_nhl_source_set_never_emits_a_fitted_model():
    d1, schedules = fixtures()
    receipt = replay_verified_nhl_totals_research(
        d1, schedules, require_complete_corpus=False
    )
    assert receipt["status"] == "NHL_TOTALS_PARTIAL_RESEARCH_NO_FIT"
    assert receipt["coverage"]["d1_official_games"] == 3
    assert receipt["coverage"]["regime_official_games"] == 3
    assert receipt["coverage"]["regime_team_source_instances"] == 3
    assert receipt["calibrated_lower_bound"] is None
    assert receipt["model_probability"] is None
    assert receipt["can_execute"] is False and receipt["probability_publishable"] is False
    assert CAN_EXECUTE is False and PROBABILITY_PUBLISHABLE is False


def test_three_games_must_not_be_labeled_full_2021_to_2025_model():
    d1, schedules = fixtures()
    with pytest.raises(NHLTotalsOfflineReplayError) as e:
        replay_verified_nhl_totals_research(d1, schedules)
    assert e.value.code == "NHL_TOTALS_REPLAY_SEASON_COHORT_INCOMPLETE"


def test_empty_schedule_is_typed_absence_not_model_unavailable():
    d1, schedules = fixtures()
    with pytest.raises(NHLTotalsOfflineReplayError) as e:
        replay_verified_nhl_totals_research(
            d1, [], require_complete_corpus=False
        )
    assert e.value.code == "NHL_TOTALS_REPLAY_SCHEDULE_MISSING"


def test_contradictory_scores_cannot_be_overridden_by_partial_mode():
    d1, schedules = fixtures()
    damaged = deepcopy(d1)
    damaged[0]["outcome_json"]["home_score"] = -1
    with pytest.raises(Exception):
        replay_verified_nhl_totals_research(
            damaged, schedules, require_complete_corpus=False
        )


def test_missing_historical_source_retrieval_timestamp_rejected():
    d1, schedules = fixtures()
    del d1[0]["source_retrieved_at"]
    with pytest.raises(Exception):
        replay_verified_nhl_totals_research(
            d1, schedules, require_complete_corpus=False
        )


def test_duplicate_source_versions_require_explicit_research_review():
    d1, schedules = fixtures()
    copied = deepcopy(d1[0])
    copied["source_payload_sha256"] = "c"*64
    copied["source_retrieved_at"] = "2026-10-26T03:00:00Z"
    with pytest.raises(Exception):
        replay_verified_nhl_totals_research(
            d1+[copied], schedules, require_complete_corpus=False
        )
    receipt = replay_verified_nhl_totals_research(
        d1+[copied], schedules,
        allow_matching_source_versions=True,
        require_complete_corpus=False
    )
    assert receipt["status"] == "NHL_TOTALS_PARTIAL_RESEARCH_NO_FIT"


def test_nonmapping_schedule_never_accepted():
    d1, schedules = fixtures()
    with pytest.raises(NHLTotalsOfflineReplayError) as e:
        replay_verified_nhl_totals_research(
            d1, [None], require_complete_corpus=False
        )
    assert e.value.code == "NHL_TOTALS_REPLAY_SCHEDULE_INPUT_INVALID"
