from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from ncaaf_feature_compiler import (
    NCAAFFeatureCompileBlocked,
    REQUIRED_SCOPED_KEYS,
    compile_training_feature_row,
)
from ncaaf_feature_transform import model_features_from_snapshot


START = datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)


def game() -> dict:
    return {
        "training_game_id": "00000000-0000-0000-0000-000000000001",
        "official_event_id": "evt-1",
        "event_start_time": START.isoformat(),
        "home_team": "Texas",
        "away_team": "Oklahoma",
        "neutral_site": False,
        "home_won": True,
        "can_execute": False,
    }


def _row(kind: str, scope: str, *, idx: int, value: float = 1.0) -> dict:
    team = None
    if scope == "HOME":
        team = "Texas"
    elif scope == "AWAY":
        team = "Oklahoma"
    payload: dict[str, float] = {"value": value}
    if kind == "REST_TRAVEL":
        payload = {"rest_days": 7.0}
        if scope == "AWAY":
            payload["travel_distance_miles"] = 191.0
    elif kind == "WEATHER":
        payload = {"wind_mph": 8.0, "precip_probability": 0.2, "temperature_f": 81.0}
    return {
        "evidence_id": f"ev-{idx}",
        "official_event_id": "evt-1",
        "event_start_time": START.isoformat(),
        "evidence_kind": kind,
        "scope": scope,
        "team": team,
        "source_provider": "TEST_APPROVED_PROVIDER",
        "evidence_timestamp": (START - timedelta(hours=4) + timedelta(seconds=idx)).isoformat(),
        "payload": payload,
        "payload_sha256": f"{idx + 1:064x}"[-64:],
        "provenance_grade": "A",
        "blocker_codes": [],
        "can_execute": False,
    }


def complete_evidence() -> list[dict]:
    return [_row(kind, scope, idx=i, value=float(i + 1) / 10.0) for i, (kind, scope) in enumerate(REQUIRED_SCOPED_KEYS)]


def test_complete_pregame_evidence_compiles_exact_model_feature_contract():
    compiled = compile_training_feature_row(game(), complete_evidence())
    assert compiled["feature_schema_version"] == "NCAAF_FEATURES_V1"
    assert compiled["market_home_no_vig"] is None
    assert compiled["market_away_no_vig"] is None
    assert compiled["market_timestamp"] is None
    assert compiled["can_execute"] is False
    assert compiled["feature_source_manifest"]["market_features_used"] is False
    assert compiled["feature_source_manifest"]["can_execute"] is False
    assert datetime.fromisoformat(compiled["feature_as_of"]) < START

    # This is the strongest schema check: the output must be consumable by the
    # exact shared training/serving transform without defaults or market priors.
    transformed = model_features_from_snapshot({**compiled, **game()})
    assert len(transformed) == 18
    assert "power_delta" in transformed
    assert "weather_precip_probability" in transformed


def test_missing_required_evidence_fails_closed_instead_of_imputing():
    rows = complete_evidence()
    rows = [row for row in rows if not (row["evidence_kind"] == "QB_VALUE" and row["scope"] == "AWAY")]
    with pytest.raises(NCAAFFeatureCompileBlocked) as caught:
        compile_training_feature_row(game(), rows)
    assert caught.value.code == "NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE"
    assert "QB_VALUE:AWAY" in caught.value.blockers


def test_post_start_evidence_is_ineligible_and_cannot_leak_into_training():
    rows = complete_evidence()
    target = next(row for row in rows if row["evidence_kind"] == "TEAM_POWER" and row["scope"] == "HOME")
    target["evidence_timestamp"] = (START + timedelta(seconds=1)).isoformat()
    with pytest.raises(NCAAFFeatureCompileBlocked) as caught:
        compile_training_feature_row(game(), rows)
    assert caught.value.code == "NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE"
    assert "TEAM_POWER:HOME" in caught.value.blockers


def test_team_identity_mismatch_cannot_supply_a_feature():
    rows = complete_evidence()
    target = next(row for row in rows if row["evidence_kind"] == "OFF_EPA" and row["scope"] == "HOME")
    target["team"] = "Not Texas"
    with pytest.raises(NCAAFFeatureCompileBlocked) as caught:
        compile_training_feature_row(game(), rows)
    assert caught.value.code == "NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE"
    assert "OFF_EPA:HOME" in caught.value.blockers


def test_latest_eligible_pregame_evidence_wins_deterministically():
    rows = complete_evidence()
    original = next(row for row in rows if row["evidence_kind"] == "TEAM_POWER" and row["scope"] == "HOME")
    newer = deepcopy(original)
    newer["evidence_id"] = "ev-newer"
    newer["payload_sha256"] = "f" * 64
    newer["payload"] = {"value": 99.0}
    newer["evidence_timestamp"] = (START - timedelta(minutes=5)).isoformat()
    rows.append(newer)
    compiled = compile_training_feature_row(game(), rows)
    assert compiled["home_power_rating"] == 99.0


def test_market_evidence_never_enters_fitted_feature_manifest_or_values():
    rows = complete_evidence()
    rows.append({
        **_row("WEATHER", "EVENT", idx=900),
        "evidence_id": "market-1",
        "evidence_kind": "MARKET_NO_VIG",
        "payload": {"home_no_vig": 0.99, "away_no_vig": 0.01},
        "payload_sha256": "e" * 64,
    })
    compiled = compile_training_feature_row(game(), rows)
    assert compiled["market_home_no_vig"] is None
    assert compiled["market_away_no_vig"] is None
    assert all(
        item["evidence_kind"] != "MARKET_NO_VIG"
        for item in compiled["feature_source_manifest"]["evidence"]
    )


def test_invalid_numeric_payload_is_typed_and_never_persistable():
    rows = complete_evidence()
    target = next(row for row in rows if row["evidence_kind"] == "QB_CERTAINTY" and row["scope"] == "HOME")
    target["payload"] = {"value": 1.5}
    with pytest.raises(NCAAFFeatureCompileBlocked) as caught:
        compile_training_feature_row(game(), rows)
    assert caught.value.code == "NCAAF_MODEL_FEATURE_PAYLOAD_INVALID"
    assert "QB_CERTAINTY:HOME:OUT_OF_RANGE" in caught.value.blockers
