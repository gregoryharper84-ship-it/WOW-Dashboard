"""Offline five-season genuine NHL REG/OT/SO goals research replay.

Verified first-party NHL public game-result projections for all 2021-22
through 2025-26 regular-season games. Researcher must first reproduce all
32 team schedule mirrors per season, then load accepted immutable local
fixtures. The *projections* are settled retrospective views, NOT original
pregame snapshots or the raw WOW D1 payload. No network or credentials here.

Fitted numerical comparison is class-C exploratory science, NOT certified
sporting probability, never a V17 serving scorer.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
import json

from v17.nhl_total_goals_challenger import (
    TotalRow, fit_total_goals_candidate,
)
from v17.nhl_total_goals_official_fixture import (
    APPROVED_SEASONS, load_checked_in_season,
    build_real_reconstructed_regime_rows,
)
from v17.nhl_total_goals_regime_mixture_research import (
    fit_regime_mixture_research,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
AUTOMATIC_CERTIFICATION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RESEARCH_COHORT_YEARS = (2021, 2022, 2023, 2024, 2025)
OFFICIAL_GAMES_TOTAL = 6560
OFFICIAL_TEAM_SOURCE_INSTANCES = 13120


def replay_real_official_nhl_total_regimes(*, fixture_directory: Path) -> dict[str, Any]:
    """Fit *research-only* regime vs Poisson on identical actual NHL games."""
    seasons = []
    source_audits = []
    event_map = {}
    for year in RESEARCH_COHORT_YEARS:
        games,audit = load_checked_in_season(year, fixture_directory=fixture_directory)
        seasons.append((year,games))
        source_audits.append(audit)
        for item in games:
            if item.event_id in event_map:
                raise RuntimeError("NHL_TOTALS_HISTORICAL_DUPLICATE_EVENT")
            event_map[item.event_id] = item
    assert sum(x["games"] for x in source_audits) == OFFICIAL_GAMES_TOTAL
    assert sum(x["team_mirror_instances"] for x in source_audits) == OFFICIAL_TEAM_SOURCE_INSTANCES
    model_rows, source_audit = build_real_reconstructed_regime_rows(seasons)
    # Same event, total, frozen previous-result feature, split and lineage
    # for BOTH rivals; do not allow separate selection populations.
    old_rows = []
    for item in model_rows:
        game=event_map[item.event_id]
        old_rows.append(TotalRow(
            event_id=item.event_id,start_utc=item.start_utc,
            season_start_year=item.season_start_year,
            home=game.home_team,away=game.away_team,
            total_goals=item.total_goals,
            reconstructed_as_of=item.feature_as_of,
            features=item.features,
            source_hash=item.official_source_sha256,
        ))
    mixture_artifact, mixture_receipt = fit_regime_mixture_research(model_rows)
    old_artifact, old_receipt = fit_total_goals_candidate(old_rows)
    if (mixture_artifact.train_n!=old_artifact.train_n
        or mixture_artifact.calibration_n!=old_artifact.calibration_n
        or mixture_artifact.development_n!=old_artifact.validation_n
        or len(event_map) != OFFICIAL_GAMES_TOTAL):
        raise RuntimeError("NHL_TOTALS_EXPERIMENT_COMPARISON_COHORT_MISMATCH")
    # Comparable proper-score science, where smaller Brier / count NLL wins.
    baseline_brier = old_receipt["validation"]["under_5_5_brier"]
    baseline_nll = old_receipt["validation"]["poisson_negative_log_likelihood"]
    mixture_brier = mixture_receipt["research_development_under55_brier"]
    mixture_nll = mixture_receipt["research_development_count_nll"]
    class_totals = {
        str(year*10000+year+1):audit["ending_regime_counts"]
        for year,audit in zip(RESEARCH_COHORT_YEARS,source_audits)
    }
    return {
        "status": "NHL_TOTALS_REAL_5SEASON_REGIME_REPLAY_RESEARCH_ONLY",
        "source_provenance": "NHL_OFFICIAL_PUBLIC_SETTLED_GAME_PROJECTIONS",
        "firstparty_source_instances_verified": OFFICIAL_TEAM_SOURCE_INSTANCES,
        "canonical_official_games": OFFICIAL_GAMES_TOTAL,
        "eligible_prior_game_feature_rows": len(model_rows),
        "regime_by_season": class_totals,
        "historical_source_audit": {
            "source_projection_not_raw_d1_payload": True,
            "retrospective_feature_construction": True,
            "immutable_pregame_snapshots": False,
            "regime_labels_from_official_final_outcomes": True,
        },
        "fit_years": [2021,2022],
        "calibration_year":2023,
        "development_year":2024,
        "development_was_previously_consulted":True,
        "2025_reserve_not_used_in_this_fit":True,
        "2025_was_previously_unblinded_for_another_candidate":True,
        "training_rows": mixture_artifact.train_n,
        "calibration_rows": mixture_artifact.calibration_n,
        "development_rows": mixture_artifact.development_n,
        "regime_model_training_sha256": mixture_artifact.training_hash,
        "research_development_metrics": {
            "mixture_under55_brier":mixture_brier,
            "poisson_under55_brier":baseline_brier,
            "mixture_minus_poisson_under55_brier":mixture_brier-baseline_brier,
            "mixture_exact_six_brier":mixture_receipt["research_development_exact_six_brier"],
            "poisson_predicted_exact_six":old_receipt["parity_research_comparison"]["poisson_predicted_exact_six_probability"],
            "observed_exact_six":old_receipt["validation"]["under_6_0_observed_push_frequency"],
            "mixture_goals_nll":mixture_nll,
            "poisson_goals_nll":baseline_nll,
            "mixture_minus_poisson_goals_nll":mixture_nll-baseline_nll,
        },
        "goalie_starter_pregame_confirmed":False,
        "data_license_certification_complete":False,
        "certified_probability_package":None,
        "governed_calibrated_lower_bound":None,
        "current_pregame_action_receipt":None,
        "model_registered_for_production":False,
        "automatic_certification":False,
        "rank_eligible":False,
        "probability_publishable":False,
        "can_execute":False,
        "terminal_authority":TERMINAL_AUTHORITY,
    }


if __name__=="__main__":
    root=Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    print(json.dumps(replay_real_official_nhl_total_regimes(fixture_directory=root),sort_keys=True))
