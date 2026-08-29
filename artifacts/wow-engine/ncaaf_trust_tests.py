"""Focused regressions for ncaaf_trust.py.

Run from artifacts/wow-engine with:
    python ncaaf_trust_tests.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ncaaf_trust import (
    CLVGrade,
    FailureRegime,
    MarketRole,
    NCAAFEventEvidence,
    NCAAFTrustState,
    REQUIRED_FAILURE_REGIMES,
    apply_qb_ceiling,
    assess_ncaaf_trust,
    calibration_metrics,
    grade_clv,
    reconcile_failure_regimes,
    two_way_no_vig,
    validate_ncaaf_evidence,
)


def _evidence(**overrides):
    now = datetime.now(timezone.utc)
    base = dict(
        official_event_id="NCAAF-2026-TEST-001",
        event_date=(now + timedelta(days=1)).date().isoformat(),
        scheduled_start_utc=(now + timedelta(days=1)).isoformat(),
        venue="Test Stadium",
        neutral_site=False,
        home_away="HOME",
        team="Alpha State",
        opponent="Beta Tech",
        starting_qb_status="CONFIRMED_STARTER",
        backup_qb_downgrade_value=-4.5,
        offensive_line_injury_status="VERIFIED",
        defensive_front_pass_rush_health="VERIFIED",
        top_wr_rb_availability="VERIFIED",
        travel_rest_spot="VERIFIED",
        weather_summary="CLEAR",
        wind_mph=8.0,
        market_role="FAVORITE",
        selection_price_american=-150,
        opposing_price_american=130,
        market_timestamp=(now - timedelta(minutes=2)).isoformat(),
        no_vig_probability=two_way_no_vig(-150, 130)[0],
        model_timestamp=(now - timedelta(minutes=1)).isoformat(),
        source_snapshot_id="11111111-1111-1111-1111-111111111111",
        conference_tier="P4",
        fbs_vs_fcs="FBS_VS_FBS",
        qb_certainty=1.0,
        depth_chart_certainty=0.95,
        injury_reporting_quality=0.90,
        market_liquidity=0.80,
        weather_variance=0.20,
        team_tempo=0.65,
        turnover_volatility=0.40,
        special_teams_volatility=0.35,
        model_disagreement=0.15,
    )
    base.update(overrides)
    return NCAAFEventEvidence(**base)


def test_evidence_happy_path():
    assert validate_ncaaf_evidence(_evidence()) == []


def test_qb_unconfirmed_caps_row():
    evidence = _evidence(starting_qb_status="EXPECTED_STARTER")
    blockers = validate_ncaaf_evidence(evidence)
    assert "NCAAF_QB_STATUS_UNCONFIRMED" in blockers
    assert apply_qb_ceiling("MODEL_QUALIFIED_HOLD", blockers, "FAVORITE") == "WINNER_WATCH"
    assert apply_qb_ceiling("MODEL_QUALIFIED_HOLD", blockers, "UNDERDOG") == "UPSET_WATCH"


def test_backup_requires_downgrade_value():
    evidence = _evidence(starting_qb_status="BACKUP_CONFIRMED", backup_qb_downgrade_value=None)
    assert "NCAAF_BACKUP_QB_DOWNGRADE_UNRESOLVED" in validate_ncaaf_evidence(evidence)


def test_stale_market_blocks():
    now = datetime.now(timezone.utc)
    evidence = _evidence(market_timestamp=(now - timedelta(minutes=11)).isoformat())
    assert "NCAAF_MARKET_PRICE_STALE" in validate_ncaaf_evidence(evidence, as_of=now)


def test_market_role_conflict_blocks():
    evidence = _evidence(market_role="CONFLICT")
    assert "FAVORITE_STATUS_CONFLICT" in validate_ncaaf_evidence(evidence)


def test_failure_regimes_are_unconditional():
    probabilities = [0.55, 0.10, 0.10, 0.09, 0.08, 0.08]
    conditional_wins = [0.72, 0.45, 0.34, 0.40, 0.55, 0.48]
    regimes = [
        FailureRegime(name, p, win)
        for name, p, win in zip(REQUIRED_FAILURE_REGIMES, probabilities, conditional_wins)
    ]
    result = reconcile_failure_regimes(regimes)
    expected = sum(p * win for p, win in zip(probabilities, conditional_wins))
    assert abs(result.unconditional_probability - expected) < 1e-12
    assert result.largest_failure_path in REQUIRED_FAILURE_REGIMES[1:]
    assert abs(result.regime_probability_sum - 1.0) < 1e-12


def test_failure_regime_missing_fails_closed():
    regimes = [FailureRegime("BASE_SCRIPT", 1.0, 0.60)]
    try:
        reconcile_failure_regimes(regimes)
    except ValueError as exc:
        assert "Missing required" in str(exc)
    else:
        raise AssertionError("missing failure regimes must fail closed")


def test_clv_grading():
    assert grade_clv(0.55, 0.58) == CLVGrade.BEAT_CLOSE
    assert grade_clv(0.55, 0.55) == CLVGrade.CLOSED_SAME
    assert grade_clv(0.55, 0.52) == CLVGrade.LOST_TO_CLOSE
    assert grade_clv(0.55, None) == CLVGrade.NO_CLOSE_AVAILABLE


def test_calibration_metrics():
    brier, log_loss = calibration_metrics(0.70, True)
    assert abs(brier - 0.09) < 1e-12
    assert log_loss > 0


def test_trust_under_25_is_test_only():
    result = assess_ncaaf_trust(
        settled_candidates=24,
        ncaaf_moneyline_bucket_candidates=20,
        review_25_passed=False,
        confirmation_50_passed=False,
        clv_positive_rate=0.70,
        roi=0.10,
        repeating_failure_tag=False,
        active_banned_failure_pattern=False,
        market_role=MarketRole.FAVORITE.value,
    )
    assert result.state == NCAAFTrustState.TEST_ONLY
    assert result.publication_ceiling == "RESEARCH_INTEREST"


def test_25_review_passed_but_under_50_is_watch():
    result = assess_ncaaf_trust(
        settled_candidates=30,
        ncaaf_moneyline_bucket_candidates=20,
        review_25_passed=True,
        confirmation_50_passed=False,
        clv_positive_rate=0.70,
        roi=0.10,
        repeating_failure_tag=False,
        active_banned_failure_pattern=False,
        market_role=MarketRole.UNDERDOG.value,
    )
    assert result.state == NCAAFTrustState.WATCH
    assert result.publication_ceiling == "UPSET_WATCH"


def test_20_bucket_cannot_bypass_50_confirmation():
    result = assess_ncaaf_trust(
        settled_candidates=40,
        ncaaf_moneyline_bucket_candidates=25,
        review_25_passed=True,
        confirmation_50_passed=False,
        clv_positive_rate=0.65,
        roi=0.12,
        repeating_failure_tag=False,
        active_banned_failure_pattern=False,
        market_role=MarketRole.FAVORITE.value,
    )
    assert result.state == NCAAFTrustState.WATCH


def test_primary_candidate_threshold():
    result = assess_ncaaf_trust(
        settled_candidates=55,
        ncaaf_moneyline_bucket_candidates=15,
        review_25_passed=True,
        confirmation_50_passed=True,
        clv_positive_rate=0.56,
        roi=0.04,
        repeating_failure_tag=False,
        active_banned_failure_pattern=False,
        market_role=MarketRole.FAVORITE.value,
    )
    assert result.state == NCAAFTrustState.PRIMARY_CANDIDATE
    assert result.publication_ceiling == "MODEL_QUALIFIED_HOLD"


def test_trusted_requires_50_confirmation_plus_bucket():
    result = assess_ncaaf_trust(
        settled_candidates=60,
        ncaaf_moneyline_bucket_candidates=20,
        review_25_passed=True,
        confirmation_50_passed=True,
        clv_positive_rate=0.60,
        roi=0.02,
        repeating_failure_tag=False,
        active_banned_failure_pattern=False,
        market_role=MarketRole.FAVORITE.value,
    )
    assert result.state == NCAAFTrustState.TRUSTED
    assert result.publication_ceiling == "NO_ADDITIONAL_NCAAF_TRUST_CEILING"


def test_scale_requires_100_and_no_banned_pattern():
    result = assess_ncaaf_trust(
        settled_candidates=100,
        ncaaf_moneyline_bucket_candidates=50,
        review_25_passed=True,
        confirmation_50_passed=True,
        clv_positive_rate=0.62,
        roi=0.03,
        repeating_failure_tag=False,
        active_banned_failure_pattern=False,
        market_role=MarketRole.FAVORITE.value,
    )
    assert result.state == NCAAFTrustState.SCALE_ELIGIBLE

    blocked = assess_ncaaf_trust(
        settled_candidates=100,
        ncaaf_moneyline_bucket_candidates=50,
        review_25_passed=True,
        confirmation_50_passed=True,
        clv_positive_rate=0.62,
        roi=0.03,
        repeating_failure_tag=False,
        active_banned_failure_pattern=True,
        market_role=MarketRole.FAVORITE.value,
    )
    assert blocked.state == NCAAFTrustState.TRUSTED
    assert "NCAAF_ACTIVE_BANNED_FAILURE_PATTERN" in blocked.blockers


def run_all():
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in sorted(tests, key=lambda fn: fn.__name__):
        test()
    print(f"PASS: {len(tests)} NCAAF trust-layer tests")


if __name__ == "__main__":
    run_all()
