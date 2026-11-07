import re
from pathlib import Path

from ncaaf_feature_compiler import REQUIRED_SCOPED_KEYS
from ncaaf_feature_source_coverage import (
    APPROVED_PROVIDER_KINDS,
    build_feature_source_matrix,
)


def test_approved_provider_seed_matches_the_reviewed_sql():
    root = Path(__file__).parent
    sql = "\n".join(
        path.read_text()
        for path in (
            root / "ncaaf_verified_availability_sources.sql",
            root / "ncaaf_schedule_geometry_source.sql",
        )
    )
    for provider, kinds in APPROVED_PROVIDER_KINDS.items():
        match = re.search(rf"'{provider}'.+?array\[([^\]]+)\]", sql)
        assert match is not None, provider
        found = {part.strip().strip("'") for part in match.group(1).split(",")}
        assert found == set(kinds)


def test_compiler_slots_and_only_six_have_an_approved_source():
    matrix = build_feature_source_matrix()
    assert matrix["required_slot_count"] == len(REQUIRED_SCOPED_KEYS) == 29
    assert matrix["approved_slots"] == [
        "QB_CERTAINTY:HOME",
        "QB_CERTAINTY:AWAY",
        "SKILL_AVAILABILITY:HOME",
        "SKILL_AVAILABILITY:AWAY",
        "REST_TRAVEL:HOME",
        "REST_TRAVEL:AWAY",
    ]
    assert matrix["missing_slot_count"] == 23
    assert "TEAM_POWER:HOME" in matrix["missing_slots"]
    assert "OFF_EPA:AWAY" in matrix["missing_slots"]
    assert "WEATHER:EVENT" in matrix["missing_slots"]
    assert "QB_VALUE:HOME" in matrix["missing_slots"]
    assert matrix["complete_feature_rows_possible_from_approved_sources"] is False
    assert matrix["challenger_fit_allowed"] is False
    assert matrix["challenger_fit_block"] == "MISSING_APPROVED_SOURCE"
    assert matrix["probability_publishable"] is False
    assert matrix["can_execute"] is False
    assert "PLAYER_AVAILABILITY_REPORT" in matrix["raw_reports_are_not_feature_slots"]
    assert "MARKET_NO_VIG" in matrix["raw_reports_are_not_feature_slots"]
    assert matrix["fitted_features_missing_source"] == [
        "power_delta",
        "off_epa_delta",
        "def_epa_delta",
        "success_rate_delta",
        "explosiveness_delta",
        "qb_value_delta",
        "ol_health_delta",
        "def_front_health_delta",
        "tempo_delta",
        "turnover_volatility_delta",
        "special_teams_delta",
        "weather_wind_mph",
        "weather_precip_probability",
    ]
    covered_features = [row["feature"] for row in matrix["fitted_features"] if row["coverage"] == "APPROVED_SOURCE"]
    assert covered_features == [
        "qb_certainty_delta",
        "skill_availability_delta",
        "rest_days_delta",
        "travel_distance_miles",
    ]
