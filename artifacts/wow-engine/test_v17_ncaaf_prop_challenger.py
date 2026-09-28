import math

import pytest

from v17.ncaaf_prop_challenger import (
    FeatureRow,
    NCAAFPropChallengerError,
    fit_candidate,
)


def _rows():
    rows = []
    for season in (2023, 2024, 2025):
        for idx in range(120):
            base = 80.0 + (idx % 25) * 3.0
            aux = 18.0 + (idx % 12)
            target = 0.75 * base + 4.0 * aux + 3.0 * math.sin(idx * 0.7)
            rows.append(
                FeatureRow(
                    stat_type="PASSING_YARDS",
                    participant_id=f"p{idx % 12}",
                    team_id=f"t{idx % 8}",
                    opponent_id=f"o{idx % 8}",
                    event_id=f"{season}-{idx:03d}",
                    season=season,
                    event_start_time=f"{season}-09-{(idx % 28) + 1:02d}T{(idx % 23):02d}:00:00+00:00",
                    features=(base, base * 1.03, base * 0.97, aux, aux * 1.05, aux * 0.95),
                    target=target,
                    prior_game_n=5,
                )
            )
    return rows


def test_candidate_is_inert_and_keeps_2025_untouched_holdout():
    candidate = fit_candidate(
        _rows(),
        stat_type="PASSING_YARDS",
        training_code_sha="abc1234567890",
    )

    assert candidate["lifecycle_state"] == "CANDIDATE"
    assert candidate["active"] is False
    assert candidate["promoted"] is False
    assert candidate["probability_publishable"] is False
    assert candidate["can_execute"] is False
    assert candidate["candidate_research_active"] is True
    metrics = candidate["validation_metrics"]
    assert metrics["train_n"] == 120
    assert metrics["calibration_n"] == 120
    assert metrics["holdout_n"] == 120
    assert metrics["forward_season_reserved"] == 2026
    assert metrics["market_features_used"] is False
    assert candidate["artifact_payload"]["dkw_epsilon_95"] > 0
    assert len(candidate["artifact_payload"]["residual_quantiles"]) == 101


def test_candidate_requires_chronological_season_splits():
    rows = _rows()
    broken = [row for row in rows if row.season != 2024]
    with pytest.raises(NCAAFPropChallengerError) as exc:
        fit_candidate(broken, stat_type="PASSING_YARDS", training_code_sha="abc1234567890")
    assert exc.value.code == "NCAAF_PROP_CAL_ROWS_BELOW_MINIMUM"
