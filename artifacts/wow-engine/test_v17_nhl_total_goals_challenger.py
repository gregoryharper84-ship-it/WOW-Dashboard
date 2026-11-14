"""Leakage/certification negative regression for standalone NHL totals challenger."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isclose

import pytest

from v17.nhl_total_goals_challenger import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE, RANK_ELIGIBLE,
    NHLTotalsResearchError, TRAIN_SEASONS, build_total_rows,
    fit_total_goals_candidate, research_outcomes_from_mean,
)


def fixture_games():
    names = ["BOS", "NYR", "LAK", "DAL", "SEA", "CAR", "VAN", "TOR"]
    result = []
    for year in range(2021, 2026):
        for day in range(44):
            # Concurrent games with disjoint teams, four per day; deterministic rotations.
            opponents = [names[(i + day) % 8] for i in range(8)]
            for pair in range(4):
                home, away = opponents[pair], opponents[7 - pair]
                hg = 2 + (day * 3 + pair + year) % 4
                ag = 1 + (day + 2 * pair + year) % 4
                if hg == ag:
                    hg += 1
                identifier = f"{year}-{day}-{pair}"
                result.append({
                    "sport": "NHL", "source_provider": "NHL_PUBLIC_WEB_API",
                    "historical_reconstruction": True, "can_execute": False,
                    "official_event_id": identifier,
                    "event_start_time": (datetime(year, 10, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat(),
                    "season": f"{year}{year+1}", "home_team": home, "away_team": away,
                    "home_score": hg, "away_score": ag,
                    "source_payload_sha256": sha256(identifier.encode()).hexdigest(),
                })
    return result


def test_research_challenger_fit_is_not_governed_probability():
    rows, audit = build_total_rows(fixture_games())
    assert audit["source_rows"] == 5 * 44 * 4
    assert audit["eligible_rows"] > 700
    assert audit["historical_reconstruction"] is True
    assert audit["archive_pregame_snapshot_claimed"] is False
    assert audit["market_features_used"] is False
    artifact, receipt = fit_total_goals_candidate(rows)
    assert artifact.train_n >= 300
    assert artifact.calibration_n >= 120
    assert artifact.validation_n >= 120
    assert receipt["forward_reserve_n"] > 0
    assert artifact.calibration_mean_multiplier > 0
    assert receipt["validation"]["poisson_negative_log_likelihood"] > 0
    assert receipt["validation"]["under_5_5_brier"] >= 0
    assert 0 <= receipt["validation"]["under_6_0_observed_push_frequency"] <= 1
    assert receipt["validation"]["under_6_0_push_calibration_bias"] == pytest.approx(
        receipt["validation"]["under_6_0_mean_push_probability"] - receipt["validation"]["under_6_0_observed_push_frequency"]
    )
    assert receipt["validation"]["n"] == receipt["constant_baseline_validation"]["n"]
    assert artifact.train_dataset_hash != artifact.validation_dataset_hash
    assert receipt["parity_research_comparison"]["holdout_used_during_model_selection"] is True
    assert receipt["parity_research_comparison"]["status"] == "EXPLORATORY_DEVELOPMENT_COMPARISON_ONLY"
    assert receipt["parity_research_comparison"]["probability_publishable"] is False
    assert receipt["parity_research_comparison"]["calibrated_lower_bound"] is None
    assert 0 <= receipt["parity_research_comparison"]["observed_exact_six_frequency"] <= 1
    assert receipt["parity_research_comparison"]["paired_under55_brier_standard_error"] >= 0
    assert receipt["calibrated_lower_bound"] is None
    assert receipt["probability_publishable"] is False
    assert artifact.probability_publishable is False
    assert not (CAN_EXECUTE or PROBABILITY_PUBLISHABLE or RANK_ELIGIBLE)


def test_same_timestamp_other_game_is_not_a_feature_dependency():
    games = fixture_games()
    baseline, _ = build_total_rows(games)
    target = next(r for r in baseline if r.season_start_year == 2021)
    same_timestamp = next(g for g in games if g["event_start_time"] == target.start_utc and g["official_event_id"] != target.event_id)
    same_row_id = same_timestamp["official_event_id"]
    previous_features = {r.event_id: r.features for r in baseline}
    same_timestamp["home_score"] += 5
    same_timestamp["source_payload_sha256"] = sha256(b"changed historical result").hexdigest()
    changed, _ = build_total_rows(games)
    assert previous_features[target.event_id] == {r.event_id: r.features for r in changed}[target.event_id]
    assert same_row_id in {r.event_id for r in changed}


def test_features_only_prior_to_event_time():
    rows, _ = build_total_rows(fixture_games())
    assert all(r.reconstructed_as_of < r.start_utc for r in rows)
    assert all(r.historically_reconstructed is True for r in rows)
    assert all(len(r.features) == 6 for r in rows)
    assert all(r.season_start_year in TRAIN_SEASONS or r.season_start_year >= 2023 for r in rows)


@pytest.mark.parametrize("change,code", [
    ({"source_provider": "ODDS_API"}, "NHL_TOTALS_SOURCE_ROW_INVALID"),
    ({"source_payload_sha256": "not-a-sha"}, "NHL_TOTALS_SOURCE_ROW_INVALID"),
    ({"can_execute": True}, "NHL_TOTALS_SOURCE_ROW_INVALID"),
    ({"historical_reconstruction": False}, "NHL_TOTALS_SOURCE_ROW_INVALID"),
    ({"home_score": -1}, "NHL_TOTALS_SOURCE_ROW_INVALID"),
    ({"event_start_time": "2024-10-01T00:00:00"}, "NHL_TOTALS_EVENT_TIME_INVALID"),
    ({"season": "20242026"}, "NHL_TOTALS_SOURCE_ROW_INVALID"),
    ({"game_type": 3}, "NHL_TOTALS_SOURCE_ROW_INVALID"),
])
def test_bad_source_rejected(change, code):
    game = fixture_games()[0]
    game.update(change)
    with pytest.raises(NHLTotalsResearchError) as err:
        build_total_rows([game])
    assert err.value.code == code


def test_duplicate_event_is_rejected_even_when_same_hash():
    game = fixture_games()[0]
    with pytest.raises(NHLTotalsResearchError) as err:
        build_total_rows([game, dict(game)])
    assert err.value.code == "NHL_TOTALS_DUPLICATE_EVENT"


def test_source_id_collision_is_rejected():
    first = fixture_games()[0]
    second = dict(first)
    second["home_score"] += 3
    with pytest.raises(NHLTotalsResearchError) as err:
        build_total_rows([first, second])
    assert err.value.code == "NHL_TOTALS_SOURCE_IDENTITY_CONFLICT"


def test_overlapping_team_at_same_timestamp_rejected():
    games = fixture_games()[:2]
    games[1]["home_team"] = games[0]["home_team"]
    with pytest.raises(NHLTotalsResearchError) as err:
        build_total_rows(games)
    assert err.value.code == "NHL_TOTALS_OVERLAPPING_TEAM_EVENT"


def test_insufficient_holdout_is_explicit():
    games = [g for g in fixture_games() if g["season"].startswith("2021")]
    rows, _ = build_total_rows(games)
    with pytest.raises(NHLTotalsResearchError) as err:
        fit_total_goals_candidate(rows)
    assert err.value.code == "NHL_TOTALS_HOLDOUT_INSUFFICIENT"


def test_exact_line_research_push_and_symmetry():
    for line in (5.5, 6.0, 6.5):
        u = research_outcomes_from_mean(5.7, line=line, side="UNDER")
        o = research_outcomes_from_mean(5.7, line=line, side="OVER")
        assert isclose(u["research_win_probability"], o["research_loss_probability"])
        assert isclose(u["research_loss_probability"], o["research_win_probability"])
        assert isclose(sum(u[k] for k in ("research_win_probability", "research_push_probability", "research_loss_probability")), 1.0)
        assert u["research_push_probability"] == o["research_push_probability"]
        assert u["research_push_probability"] > 0 if float(line).is_integer() else u["research_push_probability"] == 0
        assert u["calibrated_lower_bound"] is None
        assert u["certified_model_probability"] is None
        assert u["probability_publishable"] is False


@pytest.mark.parametrize("line,side,mean", [(5.25, "UNDER", 5.7), (5.5, "MORE", 5.7), (5.5, "UNDER", -1), (True, "UNDER", 5.7)])
def test_invalid_research_offer_fails_closed(line, side, mean):
    with pytest.raises(NHLTotalsResearchError):
        research_outcomes_from_mean(mean, line=line, side=side)
