"""First-party NHL season-schedule outcome regime source regression."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from v17.nhl_total_goals_regime_source import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE, GOAL_COUNT_DEFINITION,
    NHLTotalsRegimeSourceError, normalize_official_regime_schedule,
    reconcile_official_regime_games, reconcile_regime_labels_with_d1_events,
)


def official():
    path = Path(__file__).parent / "tests" / "fixtures" / "nhl_official_schedule_regimes_NYR_20242025.json"
    return json.loads(path.read_text())


def schedule():
    return {"games": official()["official_completed_samples"]}


def test_official_2024_25_nyr_reg_ot_so_schedule_is_reconciled_without_market_inputs():
    data = official()
    assert data["research_only"] is True
    assert data["source_uri"] == "https://api-web.nhle.com/v1/club-schedule-season/NYR/20242025"
    rows = normalize_official_regime_schedule(schedule(), expected_season=20242025, requested_team="NYR")
    assert len(rows) == 3
    assert {(x.official_event_id, x.final_period_type, x.full_game_total) for x in rows} == {
        ("2024020007", "REG", 6),
        ("2024020030", "OT", 11),
        ("2024020728", "SO", 1),
    }
    assert all(x.goal_count_definition == GOAL_COUNT_DEFINITION for x in rows)
    assert all(len(x.source_game_sha256) == 64 for x in rows)
    assert all(x.historically_reconstructed and not x.archived_pregame_snapshot for x in rows)
    assert all(not x.probability_publishable and not x.can_execute for x in rows)
    assert not CAN_EXECUTE and not PROBABILITY_PUBLISHABLE


def test_mirror_dedup_protects_event_against_double_training_count():
    rows = normalize_official_regime_schedule(schedule(), expected_season=20242025, requested_team="NYR")
    second_mirror = replace(rows[1], source_game_sha256="f" * 64)
    unique, audit = reconcile_official_regime_games([rows, [second_mirror]])
    assert len(unique) == 3
    assert audit["source_instances"] == 4
    assert audit["unique_official_games"] == 3
    assert audit["mirrored_event_count"] == 1
    assert len(audit["source_version_ledger"][0]["source_hashes"]) == 2
    assert audit["can_execute"] is False


def test_identical_result_duplicate_does_not_imply_two_outcomes():
    rows = normalize_official_regime_schedule(schedule(), expected_season=20242025, requested_team="NYR")
    actual, audit = reconcile_official_regime_games([rows, rows])
    assert len(actual) == len(rows) == 3
    assert audit["source_instances"] == 6


def test_conflicting_regime_mirror_fails_closed():
    rows = normalize_official_regime_schedule(schedule(), expected_season=20242025, requested_team="NYR")
    wrong = replace(rows[1], final_period_type="REG")
    with pytest.raises(NHLTotalsRegimeSourceError) as error:
        reconcile_official_regime_games([rows, [wrong]])
    assert error.value.code == "NHL_TOTALS_REGIME_MIRROR_CONFLICT"


@pytest.mark.parametrize("mutate", [
    lambda x: x.__setitem__("season", 20232024),
    lambda x: x.__setitem__("startTimeUTC", "2024-10-09T23:30:00"),
    lambda x: x["homeTeam"].__setitem__("abbrev", x["awayTeam"]["abbrev"]),
    lambda x: x["gameOutcome"].__setitem__("lastPeriodType", "UNK"),
    lambda x: x.pop("gameOutcome"),
    lambda x: x.__setitem__("id", 2023020007),
])
def test_source_schema_identity_and_timezone_fail_closed(mutate):
    rows = schedule()
    mutate(rows["games"][0])
    with pytest.raises(NHLTotalsRegimeSourceError):
        normalize_official_regime_schedule(rows, expected_season=20242025, requested_team="NYR")


@pytest.mark.parametrize("typ", ["SO", "OT"])
def test_overtime_shootout_final_must_be_one_goal_margin(typ):
    rows = schedule()
    target = next(g for g in rows["games"] if g["gameOutcome"]["lastPeriodType"] == typ)
    target["homeTeam"]["score"] += 3
    with pytest.raises(NHLTotalsRegimeSourceError) as error:
        normalize_official_regime_schedule(rows, expected_season=20242025, requested_team="NYR")
    assert error.value.code == "NHL_TOTALS_SCHEDULE_ROW_INVALID"


def test_unfinished_games_do_not_become_training_targets():
    rows = schedule()
    rows["games"][0]["gameState"] = "FUT"
    r = normalize_official_regime_schedule(rows, expected_season=20242025, requested_team="NYR")
    assert len(r) == 2


def test_mismatched_team_schedule_is_not_accepted():
    with pytest.raises(NHLTotalsRegimeSourceError) as err:
        normalize_official_regime_schedule(schedule(), expected_season=20242025, requested_team="BOS")
    assert err.value.code == "NHL_TOTALS_SCHEDULE_IDENTITY_DRIFT"



def real_d1():
    file = Path(__file__).parent / "tests" / "fixtures" / "nhl_totals_d1_schedule_join_three_games.json"
    return json.loads(file.read_text())["rows"]


def test_real_source_join_matches_three_official_event_outcomes():
    schedule_results = normalize_official_regime_schedule(
        schedule(), expected_season=20242025, requested_team="NYR"
    )
    joined = reconcile_regime_labels_with_d1_events(real_d1(), schedule_results)
    assert joined["status"] == "NHL_TOTALS_RETROSPECTIVE_SOURCE_LABELS_RECONCILED"
    assert joined["count"] == 3
    labels = {r["official_event_id"]: r for r in joined["labels"]}
    assert labels["2024020007"]["final_period_type"] == "REG"
    assert labels["2024020030"]["final_period_type"] == "OT"
    assert labels["2024020728"]["final_period_type"] == "SO"
    assert labels["2024020728"]["final_total_goals"] == 1
    assert all(len(r["d1_source_payload_sha256"]) == 64 for r in joined["labels"])
    assert all(len(r["official_schedule_game_sha256"]) == 64 for r in joined["labels"])
    assert joined["calibrated_lower_bound"] is None
    assert joined["probability_publishable"] is False and joined["can_execute"] is False


@pytest.mark.parametrize("mutate,code", [
    (lambda rows: rows[0]["outcome_json"].__setitem__("home_score", 8),
     "NHL_TOTALS_REGIME_D1_IDENTITY_CONFLICT"),
    (lambda rows: rows[0].__setitem__("event_start_time", "2024-10-10T23:30:00Z"),
     "NHL_TOTALS_REGIME_D1_IDENTITY_CONFLICT"),
    (lambda rows: rows[0].__setitem__("home_participant", "NYR"),
     "NHL_TOTALS_REGIME_D1_IDENTITY_CONFLICT"),
    (lambda rows: rows[0].__setitem__("source_payload_sha256", ""),
     "NHL_TOTALS_REGIME_D1_ROW_INVALID"),
    (lambda rows: rows[0].__setitem__("can_execute", True),
     "NHL_TOTALS_REGIME_D1_ROW_INVALID"),
])
def test_real_join_conflicts_rejected_without_silent_fallback(mutate, code):
    rows = real_d1()
    mutate(rows)
    schedule_results = normalize_official_regime_schedule(
        schedule(), expected_season=20242025, requested_team="NYR"
    )
    with pytest.raises(NHLTotalsRegimeSourceError) as e:
        reconcile_regime_labels_with_d1_events(rows, schedule_results)
    assert e.value.code == code


def test_incomplete_or_duplicate_join_refused():
    games = normalize_official_regime_schedule(schedule(), expected_season=20242025, requested_team="NYR")
    rows = real_d1()
    with pytest.raises(NHLTotalsRegimeSourceError) as e:
        reconcile_regime_labels_with_d1_events(rows[:2], games)
    assert e.value.code == "NHL_TOTALS_REGIME_D1_COHORT_MISSING"
    with pytest.raises(NHLTotalsRegimeSourceError) as e:
        reconcile_regime_labels_with_d1_events(rows+[rows[0]], games)
    assert e.value.code == "NHL_TOTALS_REGIME_D1_DUPLICATE"
