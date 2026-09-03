"""Tests for V17 candidate envelopes (patch section 2-5)."""
from v17.v17_candidate_envelopes import (
    V17TeamEventCandidateEnvelope,
    V17GovernedProbabilityPackage,
    DataUnavailable,
)


def test_data_unavailable_with_source():
    """Test DataUnavailable explicit provenance."""
    unavailable = DataUnavailable(
        status="DATA_UNOBTAINABLE",
        source_attempted=["mlb_api", "fallback_source"],
        error_type="ConnectionError",
    )
    assert unavailable.status == "DATA_UNOBTAINABLE"
    assert "mlb_api" in unavailable.source_attempted
    assert unavailable.error_type == "ConnectionError"


def test_team_event_envelope_identity_validation():
    """Test identity lock validation per patch section 3."""
    envelope = V17TeamEventCandidateEnvelope(
        research_run_id="test-run",
        requested_slate_date="2026-09-03",
        requested_timezone="America/Chicago",
        event_key="MLB:12345",
        official_event_id="12345",
        official_event_id_source="CANONICAL_MLB_LEDGER",
        event_start_time_utc="2026-09-03T20:00:00Z",
        event_date_local="2026-09-03",
        sport="MLB",
        league="MLB",
        home_team="HOME",
        away_team="AWAY",
        venue="STADIUM",
        official_event_status="SCHEDULED",
        official_event_status_source="CANONICAL_MLB_LEDGER",
        settlement_market="OUTRIGHT_WINNER",
        settlement_basis="FULL_GAME",
        settlement_rule="STANDARD",
        settlement_source="CANONICAL_MLB_LEDGER",
        home_starter="H_PITCHER",
        home_starter_status="PROBABLE",
        home_starter_source="CANONICAL_MLB_LEDGER",
        away_starter="A_PITCHER",
        away_starter_status="PROBABLE",
        away_starter_source="CANONICAL_MLB_LEDGER",
        home_lineup_status="PROJECTED",
        home_lineup_source="CANONICAL_MLB_LEDGER",
        away_lineup_status="PROJECTED",
        away_lineup_source="CANONICAL_MLB_LEDGER",
        injury_status="NONE",
        injury_source="CANONICAL_MLB_LEDGER",
        weather_status="CLEAR",
        weather_source="MARKET_WEATHER_SERVICE",
        bullpen_status="READY",
        bullpen_source="CANONICAL_MLB_LEDGER",
        market_snapshot_id="snap-123",
        market_snapshot_timestamp="2026-09-03T19:00:00Z",
        market_source="SPORTSBOOK",
        market_status="EXACT_LINE",
        book_count=10,
        market_role="MONEYLINE",
        market_role_status="ACTIVE",
        consensus_probability_no_vig=0.55,
        market_prior_probability=0.54,
        source_snapshot_id="src-123",
        source_snapshot_timestamp="2026-09-03T19:00:00Z",
        latest_material_update_timestamp="2026-09-03T19:00:00Z",
        evidence_as_of="2026-09-03T19:00:00Z",
    )

    ok, errors = envelope.validate_identity()
    assert ok is True
    assert len(errors) == 0


def test_team_event_envelope_identity_validation_missing_fields():
    """Test identity lock fails when required fields are missing."""
    envelope = V17TeamEventCandidateEnvelope(
        research_run_id="test-run",
        requested_slate_date="2026-09-03",
        requested_timezone="America/Chicago",
        event_key="MLB:12345",
        official_event_id="",
        official_event_id_source="",
        event_start_time_utc="",
        event_date_local="2026-09-03",
        sport="MLB",
        league="MLB",
        home_team="",
        away_team="",
        venue="STADIUM",
        official_event_status="SCHEDULED",
        official_event_status_source="CANONICAL_MLB_LEDGER",
        settlement_market="",
        settlement_basis="FULL_GAME",
        settlement_rule="STANDARD",
        settlement_source="CANONICAL_MLB_LEDGER",
        home_starter="H_PITCHER",
        home_starter_status="PROBABLE",
        home_starter_source="CANONICAL_MLB_LEDGER",
        away_starter="A_PITCHER",
        away_starter_status="PROBABLE",
        away_starter_source="CANONICAL_MLB_LEDGER",
        home_lineup_status="PROJECTED",
        home_lineup_source="CANONICAL_MLB_LEDGER",
        away_lineup_status="PROJECTED",
        away_lineup_source="CANONICAL_MLB_LEDGER",
        injury_status="NONE",
        injury_source="CANONICAL_MLB_LEDGER",
        weather_status="CLEAR",
        weather_source="MARKET_WEATHER_SERVICE",
        bullpen_status="READY",
        bullpen_source="CANONICAL_MLB_LEDGER",
        market_snapshot_id="snap-123",
        market_snapshot_timestamp="2026-09-03T19:00:00Z",
        market_source="SPORTSBOOK",
        market_status="EXACT_LINE",
        book_count=10,
        market_role="MONEYLINE",
        market_role_status="ACTIVE",
        consensus_probability_no_vig=0.55,
        market_prior_probability=0.54,
        source_snapshot_id="src-123",
        source_snapshot_timestamp="2026-09-03T19:00:00Z",
        latest_material_update_timestamp="2026-09-03T19:00:00Z",
        evidence_as_of="2026-09-03T19:00:00Z",
    )

    ok, errors = envelope.validate_identity()
    assert ok is False
    assert len(errors) > 0
    assert any("official_event_id" in e for e in errors)


def test_governed_probability_package_calibration_validation():
    """Test calibration consistency check per patch section 5."""
    package = V17GovernedProbabilityPackage(
        research_run_id="test-run",
        event_key="MLB:12345",
        official_event_id="12345",
        participant="HOME",
        opponent="AWAY",
        market_role="MONEYLINE",
        outcome_space="MONEYLINE",
        raw_model_probability=0.55,
        independent_model_probability=0.55,
        market_prior_probability=0.54,
        market_prior_weight=0.1,
        calibrated_probability=0.52,
        calibrated_probability_lower_bound=0.50,
        calibrated_probability_upper_bound=0.54,
        calibration_method="ISOTONIC_REGRESSION",
        calibration_version="1.0",
        calibration_sample_scope="SEASON_2026_MLB",
        calibration_health_status="PASS",
        model_version="FITTED_V3",
        model_timestamp="2026-09-03T18:00:00Z",
        latest_material_update_timestamp="2026-09-03T19:00:00Z",
        model_valid_after_latest_material_update=True,
        source_snapshot_id="src-123",
        source_snapshot_timestamp="2026-09-03T19:00:00Z",
    )

    ok, errors = package.validate_calibration()
    assert ok is True
    assert len(errors) == 0


def test_governed_probability_package_calibration_missing_after_health_pass():
    """Test INVALID_CALIBRATION_PACKAGE when calibration_health=PASS but fields missing."""
    package = V17GovernedProbabilityPackage(
        research_run_id="test-run",
        event_key="MLB:12345",
        official_event_id="12345",
        participant="HOME",
        opponent="AWAY",
        market_role="MONEYLINE",
        outcome_space="MONEYLINE",
        raw_model_probability=0.55,
        independent_model_probability=0.55,
        market_prior_probability=0.54,
        market_prior_weight=0.1,
        calibrated_probability=0.52,
        calibrated_probability_lower_bound=0.50,
        calibrated_probability_upper_bound=0.54,
        calibration_method="",
        calibration_version="",
        calibration_sample_scope="",
        calibration_health_status="PASS",
        model_version="FITTED_V3",
        model_timestamp="2026-09-03T18:00:00Z",
        latest_material_update_timestamp="2026-09-03T19:00:00Z",
        model_valid_after_latest_material_update=True,
        source_snapshot_id="src-123",
        source_snapshot_timestamp="2026-09-03T19:00:00Z",
    )

    ok, errors = package.validate_calibration()
    assert ok is False
    assert len(errors) > 0


def test_governed_probability_package_domain_validation():
    """Test probability domain validation (0 < p < 1)."""
    package = V17GovernedProbabilityPackage(
        research_run_id="test-run",
        event_key="MLB:12345",
        official_event_id="12345",
        participant="HOME",
        opponent="AWAY",
        market_role="MONEYLINE",
        outcome_space="MONEYLINE",
        raw_model_probability=0.55,
        independent_model_probability=0.55,
        market_prior_probability=0.54,
        market_prior_weight=0.1,
        calibrated_probability=0.52,
        calibrated_probability_lower_bound=0.50,
        calibrated_probability_upper_bound=0.54,
        calibration_method="ISOTONIC_REGRESSION",
        calibration_version="1.0",
        calibration_sample_scope="SEASON_2026_MLB",
        calibration_health_status="PASS",
        model_version="FITTED_V3",
        model_timestamp="2026-09-03T18:00:00Z",
        latest_material_update_timestamp="2026-09-03T19:00:00Z",
        model_valid_after_latest_material_update=True,
        source_snapshot_id="src-123",
        source_snapshot_timestamp="2026-09-03T19:00:00Z",
    )

    ok, errors = package.validate_probability_domain()
    assert ok is True
    assert len(errors) == 0


def test_governed_probability_package_domain_invalid():
    """Test probability domain rejects invalid values."""
    package = V17GovernedProbabilityPackage(
        research_run_id="test-run",
        event_key="MLB:12345",
        official_event_id="12345",
        participant="HOME",
        opponent="AWAY",
        market_role="MONEYLINE",
        outcome_space="MONEYLINE",
        raw_model_probability=1.5,
        independent_model_probability=0.55,
        market_prior_probability=0.54,
        market_prior_weight=0.1,
        calibrated_probability=0.52,
        calibrated_probability_lower_bound=0.50,
        calibrated_probability_upper_bound=0.54,
        calibration_method="ISOTONIC_REGRESSION",
        calibration_version="1.0",
        calibration_sample_scope="SEASON_2026_MLB",
        calibration_health_status="PASS",
        model_version="FITTED_V3",
        model_timestamp="2026-09-03T18:00:00Z",
        latest_material_update_timestamp="2026-09-03T19:00:00Z",
        model_valid_after_latest_material_update=True,
        source_snapshot_id="src-123",
        source_snapshot_timestamp="2026-09-03T19:00:00Z",
    )

    ok, errors = package.validate_probability_domain()
    assert ok is False
    assert len(errors) > 0
