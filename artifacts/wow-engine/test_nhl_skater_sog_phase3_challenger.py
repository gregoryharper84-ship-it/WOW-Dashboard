from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import nhl_skater_sog_phase3_challenger as p3
from nhl_skater_sog_ingestion import (
    PARTICIPATION_DRESSED_PLAYED,
    RECONCILIATION_UNOFFICIAL_FULL_ROSTER_ASSERTED,
    SOURCE_ID,
    SkaterGameSogRecord,
    TeamIdentity,
)

HOME = TeamIdentity("10", "TOR")
AWAY = TeamIdentity("20", "NYR")


def _record(game_id, season, start, player_id, team_id, value, position="C", toi="18:00"):
    return SkaterGameSogRecord(
        canonical_game_id=game_id, provider_game_id=game_id, provider_season_id=season,
        season_label=f"{season[:4]}-{season[4:]}", game_start_time=start, home_team=HOME, away_team=AWAY,
        player_id=player_id, player_name=player_id, position=position, team_id=team_id,
        stat_type="SHOTS_ON_GOAL", participation_status=PARTICIPATION_DRESSED_PLAYED, actual_value=value, toi=toi,
        source=SOURCE_ID, source_uri="u", source_retrieved_at=start, source_payload_sha256="x",
        effective_at=start, available_at=start, official_team_sog_total=None,
        team_shot_reconciliation_status=RECONCILIATION_UNOFFICIAL_FULL_ROSTER_ASSERTED,
    )


def _synthetic_history(*, n_players=20, n_games=30, seasons=("20212022",), seed=1, dispersion=None):
    """Poisson (dispersion=None) or overdispersed (dispersion=k>0 -> gamma-mixed
    Poisson, i.e. genuinely Negative Binomial) synthetic history built directly
    as SkaterGameSogRecord objects, still routed through the real leakage-safe
    hydration path in build_training_dataset -- only the raw record
    construction is synthetic, not the pipeline logic exercised on it."""
    rng = np.random.default_rng(seed)
    history: list[SkaterGameSogRecord] = []
    player_rates = {f"P{i}": float(rng.gamma(2.0, 1.5)) for i in range(n_players)}
    season_base = datetime(2021, 10, 1, tzinfo=timezone.utc)
    for season_idx, season in enumerate(seasons):
        base = season_base + timedelta(days=season_idx * 200)
        for g in range(n_games):
            start = (base + timedelta(days=g * 2)).isoformat()
            team = HOME if g % 2 == 0 else AWAY
            for i in range(n_players):
                pid = f"P{i}"
                mu = player_rates[pid]
                if dispersion:
                    mu_i = float(rng.gamma(1.0 / dispersion, mu * dispersion))
                else:
                    mu_i = mu
                val = int(rng.poisson(max(mu_i, 1e-6)))
                history.append(_record(f"{season}_G{g}", season, start, pid, team.team_id, val, position="C" if i % 2 == 0 else "D"))
    return history


def _complete_rows(rows, feature_names):
    return [r for r in rows if all(r.feature(name) is not None for name in feature_names)]


# --- chronological split integrity ---


def test_chronological_split_holdout_is_most_recent_season():
    history = _synthetic_history(n_players=10, n_games=10, seasons=("20202021", "20212022", "20222023"))
    rows, _ = p3.build_training_dataset(history)
    split = p3.build_chronological_split(rows)
    assert split.holdout_season == "20222023"
    assert all(r.provider_season_id != split.holdout_season for r in split.development_rows)
    assert all(r.provider_season_id == split.holdout_season for r in split.holdout_rows)


def test_chronological_split_folds_never_validate_on_past_relative_to_train():
    history = _synthetic_history(n_players=10, n_games=10, seasons=("20202021", "20212022", "20222023"))
    rows, _ = p3.build_training_dataset(history)
    split = p3.build_chronological_split(rows)
    for train_rows, validate_rows in split.rolling_folds:
        max_train_start = max(r.game_start_time for r in train_rows)
        min_validate_start = min(r.game_start_time for r in validate_rows)
        assert max_train_start < min_validate_start


# --- holdout never touched during model selection ---


def test_holdout_rows_excluded_from_development_and_folds():
    history = _synthetic_history(n_players=10, n_games=10, seasons=("20202021", "20212022", "20222023"))
    rows, _ = p3.build_training_dataset(history)
    split = p3.build_chronological_split(rows)
    holdout_ids = {(r.game_id, r.player_id) for r in split.holdout_rows}
    dev_ids = {(r.game_id, r.player_id) for r in split.development_rows}
    assert not (holdout_ids & dev_ids)
    for train_rows, validate_rows in split.rolling_folds:
        fold_ids = {(r.game_id, r.player_id) for r in train_rows} | {(r.game_id, r.player_id) for r in validate_rows}
        assert not (fold_ids & holdout_ids)


# --- deterministic training with fixed seed/config ---


def test_poisson_glm_fitting_is_deterministic():
    history = _synthetic_history(n_players=15, n_games=20)
    rows, _ = p3.build_training_dataset(history)
    m1 = p3.fit_poisson_glm(rows, p3.FEATURE_BLOCKS["F0"])
    m2 = p3.fit_poisson_glm(rows, p3.FEATURE_BLOCKS["F0"])
    assert m1.beta == m2.beta


def test_negative_binomial_fitting_is_deterministic():
    history = _synthetic_history(n_players=15, n_games=20, dispersion=0.5)
    rows, _ = p3.build_training_dataset(history)
    m1 = p3.fit_negative_binomial_glm(rows, p3.FEATURE_BLOCKS["F0"])
    m2 = p3.fit_negative_binomial_glm(rows, p3.FEATURE_BLOCKS["F0"])
    assert m1.beta == m2.beta
    assert m1.dispersion_alpha == m2.dispersion_alpha


# --- Poisson fitting ---


def test_poisson_glm_beats_naive_baseline_on_signal_bearing_data():
    history = _synthetic_history(n_players=25, n_games=25)
    rows, _ = p3.build_training_dataset(history)
    m0 = p3.fit_naive_baseline(rows)
    m1 = p3.fit_poisson_glm(rows, p3.FEATURE_BLOCKS["F0"])
    ev0 = p3.evaluate_model(m0, rows)
    ev1 = p3.evaluate_model(m1, rows)
    assert ev1.nll <= ev0.nll + 1e-6


# --- NB fitting ---


def test_negative_binomial_glm_fits_without_error_and_predicts_positive_rates():
    history = _synthetic_history(n_players=20, n_games=20, dispersion=0.5)
    rows, _ = p3.build_training_dataset(history)
    m2 = p3.fit_negative_binomial_glm(rows, p3.FEATURE_BLOCKS["F0"])
    usable = _complete_rows(rows, p3.FEATURE_BLOCKS["F0"])
    mu = m2.predict_mu(usable)
    assert np.all(mu > 0)


# --- hierarchical challenger ---


def test_hierarchical_nb_applies_nonzero_player_shrinkage_for_players_with_history():
    history = _synthetic_history(n_players=15, n_games=25, dispersion=0.5)
    rows, _ = p3.build_training_dataset(history)
    m3 = p3.fit_hierarchical_nb(rows, p3.FEATURE_BLOCKS["F0"])
    assert len(m3.player_shrinkage) > 0
    assert any(abs(v) > 1e-9 for v in m3.player_shrinkage.values())


def test_hierarchical_nb_falls_back_to_population_for_unseen_player():
    history = _synthetic_history(n_players=15, n_games=25, dispersion=0.5)
    rows, _ = p3.build_training_dataset(history)
    m3 = p3.fit_hierarchical_nb(rows, p3.FEATURE_BLOCKS["F0"])
    unseen_row = _complete_rows(rows, p3.FEATURE_BLOCKS["F0"])[0]
    # Construct a row for a player never seen in training by cloning a
    # snapshot's feature values under a new player_id.
    from dataclasses import replace
    fake_row = replace(unseen_row, player_id="UNSEEN_PLAYER_999")
    mu_population_only = m3.predict_mu([fake_row])[0]
    # No manual league-average substitution: shrinkage for an unseen id is
    # exactly zero, so this must equal the pure population NB prediction.
    population_equivalent = p3.FittedCountModel(
        p3.MODEL_NEGATIVE_BINOMIAL, m3.feature_spec, m3.beta, m3.dispersion_alpha, training_rows=m3.training_rows
    )
    mu_pop = population_equivalent.predict_mu([fake_row])[0]
    assert mu_population_only == pytest.approx(mu_pop)


# --- overdispersion diagnostics ---


def test_dispersion_alpha_higher_for_overdispersed_data():
    poisson_history = _synthetic_history(n_players=25, n_games=25, seed=7)
    overdispersed_history = _synthetic_history(n_players=25, n_games=25, seed=7, dispersion=2.0)
    rows_poisson, _ = p3.build_training_dataset(poisson_history)
    rows_nb, _ = p3.build_training_dataset(overdispersed_history)
    m_poisson_like = p3.fit_negative_binomial_glm(rows_poisson, p3.FEATURE_BLOCKS["F0"])
    m_overdispersed = p3.fit_negative_binomial_glm(rows_nb, p3.FEATURE_BLOCKS["F0"])
    assert m_overdispersed.dispersion_alpha > m_poisson_like.dispersion_alpha


# --- synthetic threshold probability generation ---


def test_threshold_probabilities_are_valid_and_monotonic_decreasing():
    mu = np.array([2.0, 4.0])
    probs = p3.threshold_probabilities(mu, alpha=None)
    lines = sorted(probs)
    values_for_first_player = [probs[line][0] for line in lines]
    assert all(0.0 <= v <= 1.0 for v in values_for_first_player)
    assert all(values_for_first_player[i] >= values_for_first_player[i + 1] for i in range(len(values_for_first_player) - 1))


# --- NLL calculation ---


def test_poisson_nll_matches_hand_computed_value():
    y = np.array([2.0])
    mu = np.array([2.0])
    from scipy.special import gammaln
    expected = float(mu[0] - y[0] * np.log(mu[0]) + gammaln(y[0] + 1))
    assert p3.poisson_nll(y, mu) == pytest.approx(expected)


def test_negative_binomial_nll_reduces_to_poisson_as_alpha_shrinks():
    y = np.array([0.0, 1.0, 2.0, 3.0, 5.0])
    mu = np.array([1.0, 1.5, 2.0, 2.5, 4.0])
    nb_small_alpha = p3.negative_binomial_nll(y, mu, 1e-6)
    poisson = p3.poisson_nll(y, mu)
    assert nb_small_alpha == pytest.approx(poisson, abs=1e-3)


# --- Brier calculation ---


def test_threshold_brier_score_hand_computed():
    y = np.array([0.0, 1.0])
    mu = np.array([0.0001, 0.0001])  # essentially always predicts 0
    per_line, _ = p3.threshold_brier_scores(y, mu, alpha=None, lines=(0.5,), min_support=0)
    # P(SOG>0.5) ~= 0 for both rows; actual_over = [0, 1] -> Brier = mean((0-0)^2,(0-1)^2) = 0.5
    assert per_line[0.5] == pytest.approx(0.5, abs=1e-3)


# --- block ablation ---


def test_feature_block_acceptance_passes_when_consistent_and_significant():
    assert p3.feature_block_clears_acceptance([1.0, 1.2, 0.8], [0.001, 0.0005, 0.0008], ci_excludes_zero=True)


def test_feature_block_acceptance_fails_when_inconsistent_across_folds():
    assert not p3.feature_block_clears_acceptance([1.0, -0.5, -0.3, -0.2], [0.001, -0.001, -0.001, -0.001], ci_excludes_zero=True)


def test_feature_block_acceptance_fails_when_ci_includes_zero():
    assert not p3.feature_block_clears_acceptance([1.0, 1.2, 0.8], [0.001, 0.0005, 0.0008], ci_excludes_zero=False)


def test_feature_block_acceptance_fails_when_improvement_too_small():
    assert not p3.feature_block_clears_acceptance([0.1, 0.1, 0.1], [0.0001, 0.0001, 0.0001], ci_excludes_zero=True)


# --- simpler-model tie break ---


def test_model_promotion_rejected_when_improvement_below_threshold_simpler_wins():
    cleared = p3.model_family_clears_promotion(
        holdout_nll_pct_improvement=0.2, holdout_macro_brier_abs_improvement=0.0005,
        ci_excludes_zero_for_claimed_primary=True, other_primary_regressed_materially=False,
        cohort_regressions=(), baseline_cohort_metrics={},
    )
    assert cleared is False


def test_model_promotion_accepted_when_improvement_clears_bar_and_no_cohort_regression():
    baseline = {"FORWARDS": p3.CohortMetrics("FORWARDS", 600, nll=2.0, macro_brier=0.20)}
    challenger_cohort = p3.CohortMetrics("FORWARDS", 600, nll=1.9, macro_brier=0.195)
    cleared = p3.model_family_clears_promotion(
        holdout_nll_pct_improvement=1.5, holdout_macro_brier_abs_improvement=0.0,
        ci_excludes_zero_for_claimed_primary=True, other_primary_regressed_materially=False,
        cohort_regressions=(challenger_cohort,), baseline_cohort_metrics=baseline,
    )
    assert cleared is True


def test_model_promotion_rejected_on_material_cohort_regression():
    baseline = {"FORWARDS": p3.CohortMetrics("FORWARDS", 600, nll=2.0, macro_brier=0.20)}
    regressed_cohort = p3.CohortMetrics("FORWARDS", 600, nll=2.0, macro_brier=0.25)  # +0.05 Brier, way over 0.010
    cleared = p3.model_family_clears_promotion(
        holdout_nll_pct_improvement=2.0, holdout_macro_brier_abs_improvement=0.005,
        ci_excludes_zero_for_claimed_primary=True, other_primary_regressed_materially=False,
        cohort_regressions=(regressed_cohort,), baseline_cohort_metrics=baseline,
    )
    assert cleared is False


# --- low-sample cohort reporting ---


def test_cohort_report_buckets_by_prior_qualifying_games():
    history = _synthetic_history(n_players=10, n_games=15)
    rows, _ = p3.build_training_dataset(history)
    m0 = p3.fit_naive_baseline(rows)
    report = p3.cohort_report(m0, rows)
    names = {c.cohort for c in report}
    assert {"0_4_PRIOR_GAMES", "5_9_PRIOR_GAMES", "10_19_PRIOR_GAMES", "20_PLUS_PRIOR_GAMES"} <= names
    total_n = sum(c.n for c in report if c.cohort.endswith("PRIOR_GAMES"))
    assert total_n == len(rows)


# --- no market-derived features ---


def test_no_feature_block_contains_market_derived_variables():
    forbidden = ("market", "odds", "line", "prizepicks", "implied", "sportsbook", "xg", "pp_unit", "projected", "goalie")
    for block_name, features in p3.FEATURE_BLOCKS.items():
        for feature in features:
            lowered = feature.lower()
            assert not any(term in lowered for term in forbidden), f"{block_name}.{feature}"


# --- no post-event evidence (leakage) ---


def test_training_row_snapshot_never_includes_its_own_game_as_evidence():
    history = _synthetic_history(n_players=10, n_games=15)
    rows, _ = p3.build_training_dataset(history)
    for row in rows[:50]:
        assert row.game_id not in row.snapshot.evidence_ids


# --- training-fold-only preprocessing ---


def test_feature_matrix_spec_fitted_only_on_given_rows_not_whole_dataset():
    history = _synthetic_history(n_players=10, n_games=20)
    rows, _ = p3.build_training_dataset(history)
    half = len(rows) // 2
    spec_half = p3.fit_feature_matrix_spec(rows[:half], p3.FEATURE_BLOCKS["F0"])
    spec_full = p3.fit_feature_matrix_spec(rows, p3.FEATURE_BLOCKS["F0"])
    assert spec_half.means != spec_full.means


# --- research artifact cannot enter production registry ---


def test_module_never_imports_a_production_registry():
    import_lines = [
        line for line in inspect.getsource(p3).splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    forbidden_terms = ("prop_fitted_provider", "prop_capability_manifest", "prop_terminal_reducer", "score_discrete_prop", "register_model_family_adapter")
    for line in import_lines:
        for term in forbidden_terms:
            assert term not in line, line


def test_research_artifact_flags_are_always_non_publishable():
    history = _synthetic_history(n_players=10, n_games=15)
    rows, _ = p3.build_training_dataset(history)
    model = p3.fit_poisson_glm(rows, p3.FEATURE_BLOCKS["F0"])
    metrics = p3.evaluate_model(model, rows)
    artifact = p3.serialize_research_artifact(
        model, training_rows=rows, training_cutoff=rows[-1].game_start_time,
        training_metrics=metrics, oos_metrics=metrics, code_version_sha="deadbeef",
    )
    assert artifact.research_only is True
    assert artifact.registered is False
    assert artifact.probability_publishable is False
    assert artifact.can_execute is False


# --- can_execute=false ---


def test_module_level_can_execute_is_false():
    assert p3.CAN_EXECUTE is False
    assert p3.PROBABILITY_PUBLISHABLE is False
    assert p3.REGISTERED is False


# --- data sufficiency gate (pure arithmetic, no hydration needed for speed) ---


def _fake_training_row(game_id, player_id, season):
    import types
    fake_snapshot = types.SimpleNamespace(feature_values={}, feature_sample_counts={}, evidence_ids=())
    return p3.TrainingRow(
        game_id=game_id, player_id=player_id, position="C", provider_season_id=season,
        game_start_time="2022-01-01T00:00:00+00:00", is_home=True, target_sog=1, snapshot=fake_snapshot,  # type: ignore[arg-type]
    )


def test_data_sufficiency_reports_insufficient_below_thresholds():
    rows = [_fake_training_row(f"G{i}", f"P{i%10}", "20222023") for i in range(100)]
    report = p3.check_data_sufficiency(rows, holdout_season="20222023")
    assert report.meets_promotion_threshold is False
    assert "SEASONS_BELOW_MINIMUM" in " ".join(report.reasons)


def test_data_sufficiency_meets_threshold_when_synthetically_large_enough():
    rows = []
    seasons = ["20202021", "20212022", "20222023"]
    for s_idx, season in enumerate(seasons):
        for i in range(7500):
            rows.append(_fake_training_row(f"{season}_G{i}", f"P{i % 450}", season))
    report = p3.check_data_sufficiency(rows, holdout_season="20222023")
    assert report.meets_promotion_threshold is True
    assert report.seasons == 3
    assert report.unique_skaters >= p3.MIN_UNIQUE_SKATERS


# --- performance: build_training_dataset must not be quadratic in history size ---


def test_build_training_dataset_scales_subquadratically():
    import time

    small_history = _synthetic_history(n_players=15, n_games=20, seed=3)   # 300 records
    large_history = _synthetic_history(n_players=45, n_games=20, seed=3)   # 900 records (3x)

    t0 = time.perf_counter()
    small_rows, _ = p3.build_training_dataset(small_history)
    t_small = time.perf_counter() - t0

    t0 = time.perf_counter()
    large_rows, _ = p3.build_training_dataset(large_history)
    t_large = time.perf_counter() - t0

    ratio_rows = len(large_rows) / len(small_rows)
    ratio_time = t_large / max(t_small, 1e-6)
    # A true O(n^2) implementation would show ratio_time ~= ratio_rows^2 (~9x
    # for a 3x row increase). Indexed lookups should keep this close to
    # linear; allow generous slack (2x the row ratio) to avoid CI flakiness
    # while still catching a real quadratic regression.
    assert ratio_time < ratio_rows * 2.0, f"ratio_time={ratio_time:.2f} ratio_rows={ratio_rows:.2f}"
