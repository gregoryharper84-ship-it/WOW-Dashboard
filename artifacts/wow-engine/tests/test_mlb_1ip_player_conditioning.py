import pytest

from mlb_1ip_artifact_pipeline import TrainingRow
from mlb_1ip_empirical_pmf import fit_empirical_pmf
from mlb_1ip_player_conditioning import score_player_conditioned_1ip


def _artifact_payload():
    rows = []
    rows.extend(TrainingRow(bf=3, pitches=12 + i % 4) for i in range(450))
    rows.extend(TrainingRow(bf=4, pitches=16 + i % 5) for i in range(400))
    rows.extend(TrainingRow(bf=5, pitches=21 + i % 6) for i in range(300))
    return fit_empirical_pmf(rows)


def test_same_line_materially_different_pitcher_histories_do_not_collapse():
    artifact = _artifact_payload()
    efficient = score_player_conditioned_1ip(
        aggregate_artifact_payload=artifact,
        recent_1ip_pitch_totals=[11, 12, 13, 14, 15, 12, 13, 14, 15, 12],
        line_value=15.5,
        side="MORE",
        shrinkage_alpha=12.0,
    )
    inefficient = score_player_conditioned_1ip(
        aggregate_artifact_payload=artifact,
        recent_1ip_pitch_totals=[16, 17, 18, 19, 20, 17, 18, 19, 20, 21],
        line_value=15.5,
        side="MORE",
        shrinkage_alpha=12.0,
    )
    assert efficient["league_prior_probability"] == inefficient["league_prior_probability"]
    assert efficient["calibrated_probability"] != inefficient["calibrated_probability"]
    assert efficient["calibrated_probability"] < inefficient["calibrated_probability"]
    assert inefficient["player_discrimination_status"] == "PLAYER_CONDITIONED"


def test_more_and_less_are_scored_from_exact_line_history():
    artifact = _artifact_payload()
    more = score_player_conditioned_1ip(
        aggregate_artifact_payload=artifact,
        recent_1ip_pitch_totals=[12, 14, 16, 18, 20, 13, 15, 17, 19, 21],
        line_value=15.5,
        side="MORE",
        shrinkage_alpha=8.0,
    )
    less = score_player_conditioned_1ip(
        aggregate_artifact_payload=artifact,
        recent_1ip_pitch_totals=[12, 14, 16, 18, 20, 13, 15, 17, 19, 21],
        line_value=15.5,
        side="LESS",
        shrinkage_alpha=8.0,
    )
    assert more["recent_hits"] == 6
    assert less["recent_hits"] == 4
    assert more["calibrated_probability"] > less["calibrated_probability"]


def test_player_conditioning_fails_closed_on_thin_history():
    with pytest.raises(ValueError, match="MLB_1IP_PLAYER_HISTORY_INSUFFICIENT"):
        score_player_conditioned_1ip(
            aggregate_artifact_payload=_artifact_payload(),
            recent_1ip_pitch_totals=[14, 16, 18, 20],
            line_value=15.5,
            side="MORE",
            shrinkage_alpha=12.0,
        )


def test_player_conditioning_requires_explicit_positive_alpha():
    with pytest.raises(ValueError, match="MLB_1IP_SHRINKAGE_ALPHA_INVALID"):
        score_player_conditioned_1ip(
            aggregate_artifact_payload=_artifact_payload(),
            recent_1ip_pitch_totals=[14, 16, 18, 20, 22],
            line_value=15.5,
            side="MORE",
            shrinkage_alpha=0.0,
        )
