from __future__ import annotations

from datetime import date

import pytest

from v17.mlb_extra_inning_margin_challenger import (
    ExtraInningMarginArtifact,
    ExtraInningMarginRow,
    ExtraInningMarginUnavailable,
    evaluate_extra_inning_margin_candidate,
    fit_extra_inning_margin_candidate,
    resolve_tied_nine_inning_samples,
)


def _rows(year, side_counts):
    out = []
    i = 0
    for winner, margins in side_counts.items():
        for margin, count in margins.items():
            for _ in range(count):
                i += 1
                out.append(
                    ExtraInningMarginRow(
                        f"{year}-{winner}-{i}", date(year, 7, 1), winner, margin
                    )
                )
    return out


def test_2024_fit_2025_holdout_beats_pooled_baseline():
    train = _rows(
        2024,
        {
            "HOME": {1: 98, 2: 6, 4: 1},
            "AWAY": {1: 56, 2: 23, 3: 7, 4: 7, 5: 5, 6: 3, 7: 1},
        },
    )
    holdout = _rows(
        2025,
        {
            "HOME": {1: 98, 2: 8, 3: 5},
            "AWAY": {1: 50, 2: 22, 3: 25},
        },
    )
    artifact = fit_extra_inning_margin_candidate(train)
    metrics = evaluate_extra_inning_margin_candidate(artifact, holdout)
    assert artifact.train_rows == 207
    assert metrics["holdout_rows"] == 208
    assert metrics["categorical_log_loss"] < metrics["pooled_baseline_log_loss"]
    assert metrics["categorical_brier"] < metrics["pooled_baseline_brier"]
    assert round(metrics["categorical_log_loss"], 3) == 0.722
    assert round(metrics["pooled_baseline_log_loss"], 3) == 0.804
    assert round(metrics["categorical_brier"], 3) == 0.134
    assert round(metrics["pooled_baseline_brier"], 3) == 0.151
    assert metrics["moneyline_probability_used"] is False
    assert metrics["probability_publishable"] is False
    assert metrics["can_execute"] is False


def test_tied_samples_use_winner_conditioned_margin_without_changing_non_ties():
    artifact = ExtraInningMarginArtifact(
        train_start="2024-04-01",
        train_end="2024-09-30",
        train_rows=207,
        home_histogram=((1, 98), (2, 6), (4, 1)),
        away_histogram=((1, 56), (2, 23), (3, 7), (4, 7), (5, 5), (6, 3), (7, 1)),
        training_dataset_hash="x" * 64,
    )
    home, away = resolve_tied_nine_inning_samples(
        home_runs9=[4, 2, 5, 1],
        away_runs9=[4, 1, 5, 3],
        extra_inning_home_win_probability=0.5,
        artifact=artifact,
        seed=17,
    )
    assert (home[1], away[1]) == (2, 1)
    assert (home[3], away[3]) == (1, 3)
    assert home[0] != away[0]
    assert home[2] != away[2]


def test_artifact_is_research_only_and_market_free():
    artifact = fit_extra_inning_margin_candidate(
        _rows(2024, {"HOME": {1: 50, 2: 5}, "AWAY": {1: 40, 2: 10}})
    )
    payload = artifact.payload()
    assert payload["market_features_used"] is False
    assert payload["moneyline_probability_used"] is False
    assert payload["probability_publishable"] is False
    assert payload["automatic_certification"] is False
    assert payload["automatic_promotion"] is False
    assert payload["can_execute"] is False


def test_holdout_must_be_strictly_after_training_cutoff():
    artifact = fit_extra_inning_margin_candidate(
        _rows(2024, {"HOME": {1: 50, 2: 5}, "AWAY": {1: 40, 2: 10}})
    )
    with pytest.raises(ExtraInningMarginUnavailable) as exc:
        evaluate_extra_inning_margin_candidate(
            artifact,
            [ExtraInningMarginRow("leak", date(2024, 7, 1), "HOME", 1)],
        )
    assert exc.value.code == "MLB_EXTRA_INNING_MARGIN_HOLDOUT_LEAKAGE"


def test_invalid_winner_label_fails_closed_in_fit_and_evaluation():
    invalid_train = _rows(
        2024, {"HOME": {1: 50, 2: 5}, "AWAY": {1: 40, 2: 10}}
    )
    invalid_train.append(
        ExtraInningMarginRow("invalid", date(2024, 7, 2), "NEUTRAL", 1)
    )
    with pytest.raises(ExtraInningMarginUnavailable) as fit_exc:
        fit_extra_inning_margin_candidate(invalid_train)
    assert fit_exc.value.code == "MLB_EXTRA_INNING_MARGIN_WINNER_INVALID"

    artifact = fit_extra_inning_margin_candidate(
        _rows(2024, {"HOME": {1: 50, 2: 5}, "AWAY": {1: 40, 2: 10}})
    )
    with pytest.raises(ExtraInningMarginUnavailable) as eval_exc:
        evaluate_extra_inning_margin_candidate(
            artifact,
            [ExtraInningMarginRow("bad-eval", date(2025, 7, 1), "NEUTRAL", 1)],
        )
    assert eval_exc.value.code == "MLB_EXTRA_INNING_MARGIN_WINNER_INVALID"


def test_duplicate_training_game_keys_fail_closed():
    rows = _rows(2024, {"HOME": {1: 50, 2: 5}, "AWAY": {1: 40, 2: 10}})
    rows.append(rows[0])
    with pytest.raises(ExtraInningMarginUnavailable) as exc:
        fit_extra_inning_margin_candidate(rows)
    assert exc.value.code == "MLB_EXTRA_INNING_MARGIN_DUPLICATE_GAME"
