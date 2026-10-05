from __future__ import annotations

import math

from nfl_event_features_p2 import FEATURE_ORDER
from v17.nfl_ml_prior_games_ablation import (
    ABLATION_FEATURES,
    CHALLENGER_ID,
    challenger_feature_order,
    fit_prior_games_ablation,
)


def _rows():
    rows = []
    sizes = {2021: 263, 2022: 262, 2023: 262, 2024: 285, 2025: 284}
    serial = 0
    for season, n in sizes.items():
        for i in range(n):
            serial += 1
            y = 1 if (i + season) % 2 == 0 else 0
            vector = []
            for j, name in enumerate(FEATURE_ORDER):
                if name == "home_prior_games":
                    value = float(serial)
                elif name == "away_prior_games":
                    value = float(serial + (i % 3))
                else:
                    value = (
                        0.45 * y
                        + math.sin((i + 1) * (j + 1) / 37.0)
                        + 0.03 * (j % 5)
                    )
                vector.append(value)
            rows.append(
                {
                    "season": season,
                    "target_outcome": "HOME_WIN" if y else "AWAY_WIN",
                    "feature_order": list(FEATURE_ORDER),
                    "feature_vector": vector,
                }
            )
    return rows


def test_feature_order_removes_only_cumulative_prior_game_counts():
    retained = challenger_feature_order()

    assert ABLATION_FEATURES == ("home_prior_games", "away_prior_games")
    assert "home_prior_games" not in retained
    assert "away_prior_games" not in retained
    assert len(retained) == len(FEATURE_ORDER) - 2
    assert retained == tuple(
        name for name in FEATURE_ORDER if name not in ABLATION_FEATURES
    )


def test_ablation_fit_is_research_only_and_fail_closed_for_production():
    result = fit_prior_games_ablation(_rows(), training_code_sha="a" * 40)

    assert result["status"] == "CHALLENGER_ONLY"
    assert result["challenger_id"] == CHALLENGER_ID
    assert result["probability_publishable"] is False
    assert result["promotion_authorized"] is False
    assert result["can_execute"] is False

    payload = result["artifact_payload"]
    assert payload["ablated_features"] == list(ABLATION_FEATURES)
    assert len(payload["feature_order"]) == len(FEATURE_ORDER) - 2
    assert len(payload["coefficients"]) == len(FEATURE_ORDER) - 2
    assert math.isfinite(payload["intercept"])
    assert math.isfinite(payload["platt_a"])
    assert math.isfinite(payload["platt_b"])

    metrics = result["metrics"]
    assert metrics["train_n"] == 787
    assert metrics["calibration_n"] == 285
    assert metrics["validation_n"] == 284
    assert set(metrics["retrospective_gate_checks"]) == {
        "train_n",
        "calibration_n",
        "validation_n",
        "beats_baseline_brier",
        "beats_baseline_log_loss",
        "auc_not_below_chance",
        "ece_within_incumbent_limit",
        "platt_positive_slope",
    }


def test_source_feature_contract_is_not_mutated():
    before = tuple(FEATURE_ORDER)
    fit_prior_games_ablation(_rows(), training_code_sha="b" * 40)
    assert tuple(FEATURE_ORDER) == before
