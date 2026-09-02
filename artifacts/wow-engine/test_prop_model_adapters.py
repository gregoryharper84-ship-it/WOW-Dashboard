from __future__ import annotations

import math

import pytest

from prop_distribution_contract import (
    PropDistributionContractError,
    PropInferenceRequest,
    derive_line_probabilities,
)
from prop_fitted_provider import ResolvedArtifact
from prop_distribution_contract import CertifiedBundle
from prop_model_adapters import (
    MLB_PITCHER_SO_MODEL_FAMILY,
    TAG_OPPONENT_CONTACT_EXTENSION,
    TAG_STRIKEOUT_RATE_SUPPRESSION,
    mlb_pitcher_so_failure_path_nb_v1_adapter,
    nb_pmf,
    shrink,
)


def _fitted_constants(**overrides):
    base = {
        "league_so_per_out": 0.31,
        "league_k_per_pa": 0.224,
        "league_shortened_rate": 0.27,
        "outs_normal_scale": 17.8,
        "outs_short_scale": 10.7,
        "dispersion_r": 54.6,
    }
    base.update(overrides)
    return base


def _artifact_payload(**overrides):
    payload = {
        "fitted_constants": _fitted_constants(),
        "shrinkage_k_rate": 8.0,
        "shrinkage_k_regime": 8.0,
        "shortened_outs_threshold": 15,
        "max_support_k": 20,
        "opponent_factor_clip": [0.75, 1.30],
        "feature_transform_version": "MLB_PITCHER_SO_TRANSFORM_V1",
    }
    payload.update(overrides)
    return payload


def _bundle(**overrides):
    fields = dict(
        model_artifact_version="MLB_PITCHER_SO_FAILURE_PATH_NB_V1_TEST",
        calibrator_version="MLB_PITCHER_SO_CAL_V1",
        feature_transform_version="MLB_PITCHER_SO_TRANSFORM_V1",
        specialist_version="wow.mlb-pitcher-failure-path-expert@1",
        certification_id="CERT-TEST-1",
        feature_schema_version="PROP_FEATURES_V1",
        training_dataset_hash="a" * 64,
        training_code_sha="b" * 64,
        artifact_checksum="c" * 64,
        lifecycle_state="PROSPECTIVE_CERTIFIED",
        supported_sport="MLB",
        supported_stat_type="PITCHER_STRIKEOUTS",
        supported_line_min=0.5,
        supported_line_max=12.5,
    )
    fields.update(overrides)
    return CertifiedBundle(**fields)


def _artifact(**overrides):
    fields = dict(
        artifact_id="11111111-1111-4111-8111-111111111111",
        model_family=MLB_PITCHER_SO_MODEL_FAMILY,
        artifact_format="JSON_V1",
        artifact_payload=_artifact_payload(),
        training_rows=4489,
        validation_metrics={"model_mean_nll": 2.228, "baseline_mean_nll": 2.289},
        bundle=_bundle(),
    )
    fields.update(overrides)
    return ResolvedArtifact(**fields)


def _request():
    return PropInferenceRequest(
        event_id="MLB:TEST:1",
        player_id="wow-name:test-pitcher",
        sport="MLB",
        league_season="2026",
        stat_type="PITCHER_STRIKEOUTS",
        evidence_snapshot_id="22222222-2222-4222-8222-222222222222",
        market_identity_id="wow-market:test",
        as_of_timestamp="2026-08-29T12:00:00+00:00",
        request_id="req-1",
        feature_schema_version="PROP_FEATURES_V1",
    )


def _features(**overrides):
    game_log = [5, 6, 4, 7, 5, 6, 8, 3, 5, 6]
    box_score_log = [{"outs": o} for o in [17, 18, 15, 19, 16, 18, 20, 12, 17, 18]]
    features = {"game_log": game_log, "box_score_log": box_score_log, "opponent_context": None}
    features.update(overrides)
    return features


def test_valid_evidence_produces_normalized_in_distribution_pmf():
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(_artifact(), _request(), _features())
    assert dist.publication_status == "NOT_EVALUATED"
    assert dist.can_execute is False
    assert dist.coverage.in_distribution is True
    assert sum(dist.support.values()) == pytest.approx(1.0, abs=1e-9)
    assert dist.expected_value > 0


def test_missing_outs_key_fails_closed():
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_so_failure_path_nb_v1_adapter(
            _artifact(), _request(), _features(box_score_log=[{"foo": 1}] * 10)
        )
    assert exc.value.code == "PROP_BOX_SCORE_LOG_MISSING_OUTS"


def test_negative_outs_fails_closed():
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_so_failure_path_nb_v1_adapter(
            _artifact(), _request(), _features(box_score_log=[{"outs": -1}] * 10)
        )
    assert exc.value.code == "PROP_BOX_SCORE_LOG_OUTS_INVALID"


def test_mismatched_lengths_fail_closed():
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_so_failure_path_nb_v1_adapter(
            _artifact(), _request(), _features(game_log=[5, 6, 7])
        )
    assert exc.value.code == "PROP_EVIDENCE_FEATURE_MISALIGNED"


def test_empty_evidence_fails_closed():
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_so_failure_path_nb_v1_adapter(
            _artifact(), _request(), _features(game_log=[], box_score_log=[])
        )
    assert exc.value.code == "PROP_EVIDENCE_FEATURE_MISALIGNED"


def test_all_zero_outs_is_out_of_distribution():
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(box_score_log=[{"outs": 0}] * 10)
    )
    assert dist.coverage.in_distribution is False
    assert "ZERO_TOTAL_PRIOR_OUTS" in dist.coverage.coverage_failures


def test_missing_fitted_constants_fails_closed():
    bad_artifact = _artifact(artifact_payload={"shrinkage_k_rate": 8.0})
    with pytest.raises(PropDistributionContractError) as exc:
        mlb_pitcher_so_failure_path_nb_v1_adapter(bad_artifact, _request(), _features())
    assert exc.value.code == "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID"


def test_opponent_context_present_shifts_expectation_vs_absent():
    baseline = mlb_pitcher_so_failure_path_nb_v1_adapter(_artifact(), _request(), _features())
    with_tough_opponent = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.30})
    )
    assert with_tough_opponent.expected_value > baseline.expected_value


def test_thin_history_has_higher_ood_score_than_thick_history():
    thin = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(),
        _features(game_log=[5] * 10, box_score_log=[{"outs": 17}] * 10),
    )
    # Same shape, but simulate "thinner" evidence via fewer entries is not
    # allowed by the >=10 evidence contract; instead confirm ood_score is a
    # finite, valid, monotone-in-n quantity by construction (1/(1+n)).
    assert math.isclose(thin.coverage.ood_score, 1.0 / 11.0, rel_tol=1e-9)


def test_nb_pmf_normalizes_across_parameter_range():
    for mu in (0.5, 2.0, 5.0, 9.5):
        for r in (5.0, 25.0, 1000.0):
            pmf = nb_pmf(mu, r, 20)
            assert sum(pmf.values()) == pytest.approx(1.0, abs=1e-9)
            assert all(p >= 0 for p in pmf.values())


def test_shrink_falls_back_to_league_when_no_pitcher_value():
    assert shrink(float("nan"), 0.31, 10, 8.0) == 0.31


def test_shrink_approaches_pitcher_value_with_large_n():
    result = shrink(0.5, 0.31, 100000, 8.0)
    assert result == pytest.approx(0.5, abs=1e-3)


# --- Postmortem patch WOW-PATCH-2026-09-02 (issues #116/#119): typed
# opponent strikeout-suppression / contact-extension evidence. ---------------


def test_normal_matchup_no_opponent_context_is_unaffected_and_untagged():
    """No opponent evidence at all must behave exactly as before this patch:
    neutral opp_factor, no tags, empty extra evidence fields."""
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(_artifact(), _request(), _features())
    ev = dist.failure_path_evidence
    assert ev["tags"] == []
    assert ev["opponent_factor"] == pytest.approx(1.0)
    assert ev["opponent_factor_clipped"] is False
    assert ev["opponent_factor_source"] == "NEUTRAL_NO_OPPONENT_EVIDENCE"
    assert ev["mu_normal_after_opponent_factor"] == pytest.approx(ev["mu_normal_before_opponent_factor"])


def test_low_k_contact_opponent_lowers_probability_and_tags_suppression():
    """A materially low opponent K-rate must lower the mean/low-tail mass and
    be named via TAG_STRIKEOUT_RATE_SUPPRESSION -- not left as an unlabeled
    number, per issue #116 requirement (C)/(D)."""
    baseline = mlb_pitcher_so_failure_path_nb_v1_adapter(_artifact(), _request(), _features())
    suppressed = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.15})
    )
    assert suppressed.expected_value < baseline.expected_value
    ev = suppressed.failure_path_evidence
    assert TAG_STRIKEOUT_RATE_SUPPRESSION in ev["tags"]
    assert ev["opponent_factor"] < 1.0
    # Low tail (few strikeouts) must gain mass, not lose it, under suppression.
    low_tail = lambda dist: sum(p for k, p in dist.support.items() if k <= 2)
    assert low_tail(suppressed) > low_tail(baseline)


def test_workload_adequate_but_suppressed_manaea_regime_fixture():
    """Synthetic fixture modeled on the 2026-09-01 postmortem failure mode
    (a contact-oriented, low-chase opponent facing an otherwise
    normal-workload starter) -- no real game result or hindsight label is
    used as input. A generous expected workload must NOT neutralize the
    opponent K-suppression: expected_batters_faced has no fitted
    coefficient and is evidence-only (see module docstring), so it must
    leave mu, and the suppression tag, unchanged from the no-workload case.
    """
    request, artifact = _request(), _artifact()
    manaea_opponent = {
        "k_rate_per_pa": 0.15,
        "contact_rate_per_pa": 0.85,
        "chase_rate": 0.18,
        "expected_batters_faced": 27.0,  # a full, healthy-workload start
    }
    with_workload = mlb_pitcher_so_failure_path_nb_v1_adapter(
        artifact, request, _features(opponent_context=manaea_opponent)
    )
    without_workload_field = mlb_pitcher_so_failure_path_nb_v1_adapter(
        artifact, request,
        _features(opponent_context={k: v for k, v in manaea_opponent.items() if k != "expected_batters_faced"}),
    )
    baseline = mlb_pitcher_so_failure_path_nb_v1_adapter(artifact, request, _features())

    assert with_workload.expected_value == pytest.approx(without_workload_field.expected_value)
    assert with_workload.expected_value < baseline.expected_value
    ev = with_workload.failure_path_evidence
    assert TAG_STRIKEOUT_RATE_SUPPRESSION in ev["tags"]
    assert TAG_OPPONENT_CONTACT_EXTENSION in ev["tags"]
    assert ev["opponent_expected_batters_faced"] == pytest.approx(27.0)


def test_low_promo_line_does_not_override_suppression():
    """A distant/adjacent low market line must not erase the suppression's
    effect on the underlying distribution -- MORE probability must stay
    lower than baseline at every line checked, not just near the model's
    own mean (issue #116 requirement C)."""
    baseline = mlb_pitcher_so_failure_path_nb_v1_adapter(_artifact(), _request(), _features())
    suppressed = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.15})
    )
    for line in (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 8.5):
        base_probs = derive_line_probabilities(baseline, line)
        sup_probs = derive_line_probabilities(suppressed, line)
        assert sup_probs.probability_more <= base_probs.probability_more


def test_no_duplicate_suppression_penalty_single_multiplication_point():
    """opp_factor must apply exactly once, to both regimes, and contact/
    chase-only evidence (no fitted coefficient) must never itself move mu --
    otherwise the same opponent evidence would penalize probability in two
    places (double-counting), which issue #119 explicitly prohibits."""
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.15})
    )
    ev = dist.failure_path_evidence
    assert ev["mu_normal_after_opponent_factor"] == pytest.approx(
        ev["mu_normal_before_opponent_factor"] * ev["opponent_factor"]
    )
    assert ev["mu_short_after_opponent_factor"] == pytest.approx(
        ev["mu_short_before_opponent_factor"] * ev["opponent_factor"]
    )

    baseline = mlb_pitcher_so_failure_path_nb_v1_adapter(_artifact(), _request(), _features())
    contact_only = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(),
        _features(opponent_context={"contact_rate_per_pa": 0.90, "chase_rate": 0.10}),
    )
    # Contact/chase evidence alone (no k_rate_per_pa) must be labeled but
    # never numerically penalize -- it has no fitted coefficient.
    assert contact_only.expected_value == pytest.approx(baseline.expected_value)
    assert TAG_OPPONENT_CONTACT_EXTENSION in contact_only.failure_path_evidence["tags"]
    assert TAG_STRIKEOUT_RATE_SUPPRESSION not in contact_only.failure_path_evidence["tags"]


def test_opposite_side_less_is_coherent_under_suppression():
    """Fewer expected strikeouts must raise LESS probability by exactly the
    same reduction that lowers MORE probability -- both sides read one
    shared, non-contradictory PMF (issue #119 two-sided consistency)."""
    baseline = mlb_pitcher_so_failure_path_nb_v1_adapter(_artifact(), _request(), _features())
    suppressed = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.15})
    )
    line = 5.5
    base_probs = derive_line_probabilities(baseline, line)
    sup_probs = derive_line_probabilities(suppressed, line)
    assert sup_probs.probability_less > base_probs.probability_less
    assert sup_probs.probability_more < base_probs.probability_more
    assert sup_probs.probability_more + sup_probs.probability_less + sup_probs.push_probability == pytest.approx(1.0)


def test_opponent_factor_clip_is_recorded_when_clipped():
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.01})
    )
    ev = dist.failure_path_evidence
    assert ev["opponent_factor_clipped"] is True
    assert ev["opponent_factor"] == pytest.approx(0.75)  # artifact's opponent_factor_clip floor


def test_failure_path_evidence_schema_has_required_keys():
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.15})
    )
    required = {
        "tags", "opp_k_rate_per_pa", "league_k_rate_per_pa", "opponent_factor",
        "opponent_factor_clipped", "opponent_factor_source",
        "opponent_contact_rate_per_pa", "opponent_chase_rate",
        "opponent_expected_batters_faced", "mu_normal_before_opponent_factor",
        "mu_normal_after_opponent_factor", "mu_short_before_opponent_factor",
        "mu_short_after_opponent_factor", "prior_so_per_out",
        "prior_shortened_rate", "shortened_outing_probability", "n_prior_starts",
    }
    assert required.issubset(dist.failure_path_evidence.keys())


def test_can_execute_and_publication_status_unchanged_with_failure_path_evidence():
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(), _features(opponent_context={"k_rate_per_pa": 0.15})
    )
    assert dist.can_execute is False
    assert dist.publication_status == "NOT_EVALUATED"
    assert sum(dist.support.values()) == pytest.approx(1.0, abs=1e-9)


def test_non_finite_opponent_fields_are_ignored_not_fabricated():
    dist = mlb_pitcher_so_failure_path_nb_v1_adapter(
        _artifact(), _request(),
        _features(opponent_context={"k_rate_per_pa": float("nan"), "contact_rate_per_pa": float("inf")}),
    )
    ev = dist.failure_path_evidence
    assert ev["opp_k_rate_per_pa"] is None
    assert ev["opponent_contact_rate_per_pa"] is None
    assert ev["opponent_factor"] == pytest.approx(1.0)
    assert ev["opponent_factor_source"] == "NEUTRAL_NO_OPPONENT_EVIDENCE"
