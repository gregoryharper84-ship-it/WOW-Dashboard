from __future__ import annotations

import pytest

from v17.upset_pathway_training import (
    SPORT_FEATURE_CONTRACTS,
    SPORT_REGIME_MECHANISMS,
    UpsetTrainingInvalid,
    fit_upset_pathway_challenger,
    predict_upset_pathway,
    walk_forward_metrics,
)


def rows_for(sport: str, repetitions: int = 6):
    names = SPORT_FEATURE_CONTRACTS[sport]
    regimes = list(SPORT_REGIME_MECHANISMS[sport])
    rows, regime_labels, upset, fragility = [], [], [], []
    for regime_index, regime in enumerate(regimes):
        for i in range(repetitions):
            rows.append({name: (i + 1) * .1 + regime_index * .2 + j * .01
                         for j, name in enumerate(names)})
            regime_labels.append(regime)
            upset.append(i % 2)
            fragility.append((i + regime_index) % 2)
    return rows, regime_labels, upset, fragility


@pytest.mark.parametrize("sport", ["MLB", "NFL", "WNBA", "NBA"])
def test_each_sport_has_distinct_fitted_regime_contract(sport):
    rows, regimes, upset, fragility = rows_for(sport)
    artifact = fit_upset_pathway_challenger(
        sport=sport, rows=rows, regime_labels=regimes, upset_labels=upset,
        favorite_fragility_labels=fragility, artifact_id=f"{sport.lower()}-challenger-v1",
    )
    result = predict_upset_pathway(artifact, rows[0])
    assert result["sport"] == sport
    assert result["aggregation"]["status"] == "PASS"
    assert result["aggregation"]["favorite_fragility_probability"] == pytest.approx(
        result["independent_favorite_fragility_probability"]
    )
    assert result["aggregation"]["can_execute"] is False


def test_walk_forward_reports_metrics_but_cannot_self_promote():
    rows, regimes, upset, fragility = rows_for("MLB", repetitions=12)
    artifact = fit_upset_pathway_challenger(
        sport="MLB", rows=rows, regime_labels=regimes, upset_labels=upset,
        favorite_fragility_labels=fragility, artifact_id="mlb-challenger-v1",
    )
    # Reuse a sufficiently large synthetic validation cohort only to exercise
    # metric and promotion boundaries; production callers enforce chronology.
    report = walk_forward_metrics(
        artifact=artifact, validation_rows=rows,
        upset_labels=upset, minimum_n=40,
    )
    assert 0 <= report["brier_score"] <= 1
    assert report["promotion_eligible"] is False
    assert report["promotion_blocker"] == "CHAMPION_CHALLENGER_COMPARISON_REQUIRED"


def test_feature_contract_rejects_market_fields():
    original = SPORT_FEATURE_CONTRACTS["MLB"]
    SPORT_FEATURE_CONTRACTS["MLB"] = (*original[:-1], "market_odds")
    try:
        rows, regimes, upset, fragility = rows_for("MLB")
        with pytest.raises(UpsetTrainingInvalid, match="MARKET_OR_NARRATIVE_FEATURE_LEAKAGE"):
            fit_upset_pathway_challenger(
                sport="MLB", rows=rows, regime_labels=regimes, upset_labels=upset,
                favorite_fragility_labels=fragility, artifact_id="bad",
            )
    finally:
        SPORT_FEATURE_CONTRACTS["MLB"] = original
