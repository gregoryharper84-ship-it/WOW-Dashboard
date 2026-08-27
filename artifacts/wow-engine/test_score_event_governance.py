"""Future-artifact acceptance boundaries; no fitted model is supplied here."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate_spec

import api
from event_governance import audit_future_event_claim, govern_event_decision


def valid_claim():
    return {
        "simulation_count": 50_000,
        "shared_simulation_run_id": "shared-game-run-1",
        "raw_home_probability": 0.56,
        "raw_away_probability": 0.44,
        "independent_home_probability": 0.55,
        "independent_away_probability": 0.45,
        "calibrated_home_probability": 0.54,
        "calibrated_home_lower_bound": 0.50,
        "calibrated_home_upper_bound": 0.58,
        "calibrated_away_probability": 0.46,
        "calibrated_away_lower_bound": 0.42,
        "calibrated_away_upper_bound": 0.50,
        "model_version": "fitted-shadow-v1",
        "model_artifact_id": "artifact-1",
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_version": "cal-1",
        "bounds_method_version": "bounds-1",
        "source_snapshot_id": "snapshot-1",
        "model_timestamp": datetime(2026, 8, 27, 23, tzinfo=timezone.utc),
        "latest_material_update_timestamp": datetime(2026, 8, 27, 22, tzinfo=timezone.utc),
        "market_prior_weight": 0.20,
        "non_normal_regime_probability": 0.20,
        "normal_regime_favorite_probability": 0.60,
        "final_favorite_probability": 0.54,
    }


def test_16_shared_game_run_is_required():
    claim = valid_claim(); claim["shared_simulation_run_id"] = None
    with pytest.raises(ValueError, match="SHARED_GAME_SIMULATION_REQUIRED"):
        audit_future_event_claim(claim)


def test_17_raw_pair_must_normalize():
    claim = valid_claim(); claim["raw_away_probability"] = 0.50
    with pytest.raises(ValueError, match="OUTCOME_SPACE_NOT_NORMALIZED"):
        audit_future_event_claim(claim)


def test_18_independent_pair_must_normalize():
    claim = valid_claim(); claim["independent_away_probability"] = 0.50
    with pytest.raises(ValueError, match="OUTCOME_SPACE_NOT_NORMALIZED"):
        audit_future_event_claim(claim)


def test_19_calibrated_pair_must_normalize():
    claim = valid_claim(); claim["calibrated_away_probability"] = 0.50
    with pytest.raises(ValueError, match="OUTCOME_SPACE_NOT_NORMALIZED"):
        audit_future_event_claim(claim)


def test_20_bound_order_is_enforced():
    claim = valid_claim(); claim["calibrated_home_lower_bound"] = 0.57
    with pytest.raises(ValueError, match="PROBABILITY_RANGE_UNSUPPORTED"):
        audit_future_event_claim(claim)


def test_21_missing_lower_bound_is_not_rank_eligible():
    claim = valid_claim(); claim["calibrated_home_lower_bound"] = None
    with pytest.raises(ValueError, match="NOT_RANK_ELIGIBLE"):
        audit_future_event_claim(claim)


def test_22_simulation_minimum_is_enforced():
    claim = valid_claim(); claim["simulation_count"] = 49_999
    with pytest.raises(ValueError, match="SIMULATION_COUNT_BELOW_MINIMUM"):
        audit_future_event_claim(claim)


def test_23_stale_model_is_invalidated():
    claim = valid_claim(); claim["model_timestamp"] = datetime(2026, 8, 27, 21, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="STALE_MODEL_INVALIDATED"):
        audit_future_event_claim(claim)


def test_24_market_prior_over_half_gets_ceiling_not_rejection():
    claim = valid_claim(); claim["market_prior_weight"] = 0.60
    assert audit_future_event_claim(claim) == "PASS_WITH_CONFIDENCE_CEILING:MARKET_DEPENDENT_MODEL"


def test_25_normal_regime_probability_cannot_be_final():
    claim = valid_claim(); claim["final_favorite_probability"] = 0.60
    with pytest.raises(ValueError, match="UNCONDITIONAL_PROBABILITY_REQUIRED"):
        audit_future_event_claim(claim)


def test_26_complete_future_claim_passes_audit_boundary():
    assert audit_future_event_claim(valid_claim()) == "PASS_PROBABILITY_AUDIT"


def test_27_governor_selects_at_most_one_side():
    assert govern_event_decision(0.57, 0.43) == {
        "event_decision": "SELECT_ONE_SIDE", "selected_side": "HOME"
    }


def test_28_close_game_rule_is_no_pick():
    assert govern_event_decision(0.51, 0.48) == {
        "event_decision": "NO_PICK_CLOSE_GAME", "selected_side": None
    }


def test_29_client_backend_owned_probability_field_is_forbidden():
    payload = {
        "research_run_id": "r", "requested_slate_date": "2099-01-01",
        "requested_timezone": "UTC", "scan_stage": "PREGAME", "event_key": "e",
        "official_event_id": "o", "event_start_time_utc": "2099-01-01T01:00:00Z",
        "sport": "MLB", "league": "MLB", "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS", "home_team": "H",
        "away_team": "A", "venue": "V", "home_starting_pitcher": "HP",
        "away_starting_pitcher": "AP", "home_starter_status": "CONFIRMED",
        "away_starter_status": "CONFIRMED", "home_lineup_status": "CONFIRMED",
        "away_lineup_status": "CONFIRMED",
        "source_snapshot_id": "11111111-1111-4111-8111-111111111111",
        "raw_home_probability": 0.9,
    }
    with pytest.raises(Exception):
        api.ScoreEventRequest(**payload)


def test_30_schema_has_one_grade_per_prediction():
    schema = Path("event_schema.sql").read_text()
    assert "constraint uq_wow_event_outcome_prediction unique (event_prediction_id)" in schema


def test_31_schema_has_no_cascade_delete():
    schema = Path("event_schema.sql").read_text().lower()
    assert "on delete cascade" not in schema


def test_32_schema_locks_controlling_specialist():
    schema = Path("event_schema.sql").read_text()
    assert "check (controlling_specialist = 'wow.mlb-game-win-probability-expert')" in schema


def test_33_openapi_validates_with_unique_operations():
    spec = yaml.safe_load(Path("openapi.custom-gpt.template.yaml").read_text())
    validate_spec(spec)
    operation_ids = [op["operationId"] for path in spec["paths"].values()
                     for op in path.values() if isinstance(op, dict) and "operationId" in op]
    assert len(operation_ids) == len(set(operation_ids)) == 5


def test_34_can_execute_remains_false_in_event_schema():
    assert "check (can_execute = false)" in Path("event_schema.sql").read_text()
