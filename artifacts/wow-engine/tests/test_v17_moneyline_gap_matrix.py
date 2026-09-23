from __future__ import annotations

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.team_event_moneyline_gap_matrix import (
    GAP_MATRIX_VERSION,
    build_moneyline_gap_matrix,
    moneyline_gap_audit,
)


def _rows():
    return {row["sport"]: row for row in build_moneyline_gap_matrix()}


def test_gap_matrix_accounts_for_every_cataloged_sport_once():
    rows = build_moneyline_gap_matrix()

    assert len(rows) == len(EXPECTED_TEAM_EVENT_SPORTS)
    assert {row["sport"] for row in rows} == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert all(row["gap_matrix_version"] == GAP_MATRIX_VERSION for row in rows)


def test_gap_matrix_is_observability_only_and_fail_closed():
    forbidden_probability_fields = {
        "raw_probability",
        "calibrated_probability",
        "calibrated_lower_bound",
        "lower_bound",
        "upper_bound",
    }

    for row in build_moneyline_gap_matrix():
        assert row["legacy_generic_fallback_allowed"] is False
        assert row["market_probability_substitution_allowed"] is False
        assert row["generic_reasoning_substitution_allowed"] is False
        assert row["probability_authority_inferred_from_importability"] is False
        assert row["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert row["can_execute"] is False
        assert "CALIBRATED_LOWER_BOUND" in row["rank_metric"]
        assert forbidden_probability_fields.isdisjoint(row)


def test_nfl_uses_exact_governed_owner_and_runtime_schema_without_inference():
    nfl = _rows()["NFL"]

    assert nfl["route_owner"] == "wow.nfl-game-win-probability-expert"
    assert nfl["route_owner_source"] == "nfl_event_model_contract.py:CONTROLLING_SPECIALIST"
    assert nfl["runtime_feature_schema_version"] == "NFL_EVENT_PREGAME_PRIOR_V1"
    assert nfl["runtime_feature_schema_source"] == "nfl_event_model_contract.py:FEATURE_SCHEMA_VERSION"
    assert nfl["calibration_mode"] == "BRIDGE_OWNED_CHAMPION"
    assert nfl["hydration_mode"] == "NFL_SPORT_SPECIFIC_PUBLICATION_CHAIN"


def test_candidate_feature_schemas_do_not_promote_runtime_authority():
    rows = _rows()

    for sport in ("NBA", "WNBA"):
        row = rows[sport]
        assert row["development_feature_schema_version"] == "BASKETBALL_TEAM_EVENT_FEATURES_V2"
        assert row["runtime_feature_schema_version"] is None
        assert row["registered_capability"] is False
        assert row["legacy_generic_fallback_allowed"] is False

    ncaaf = rows["NCAAF"]
    assert ncaaf["development_feature_schema_version"] == "NCAAF_FEATURES_V1"
    assert ncaaf["runtime_feature_schema_version"] is None
    assert ncaaf["registered_capability"] is False
    assert "RUNTIME_FEATURE_SCHEMA_NOT_DECLARED_IN_CROSS_SPORT_AUDIT" in ncaaf["known_gaps"]


def test_unknown_route_ownership_stays_explicit_instead_of_being_fabricated():
    rows = _rows()

    for sport in ("NHL", "PGA", "MMA", "BOXING", "CRICKET"):
        row = rows[sport]
        assert row["route_owner"] is None
        assert row["route_owner_source"] is None
        assert "CANONICAL_ROUTE_OWNER_NOT_DECLARED_IN_AUDIT_SOURCES" in row["known_gaps"]


def test_legacy_generic_moneyline_path_is_explicitly_non_authoritative():
    audit = moneyline_gap_audit()

    assert audit["all_cataloged_sports_accounted_for"] is True
    assert audit["probability_behavior_changed"] is False
    assert audit["can_execute"] is False
    assert len(audit["legacy_paths"]) == 1

    legacy = audit["legacy_paths"][0]
    assert legacy["path"].endswith("gate_engine/moneyline/sport_model.py")
    assert legacy["authority_state"] == "LEGACY_NON_AUTHORITATIVE"
    assert legacy["production_fallback_allowed"] is False
    assert legacy["probability_authority"] is False
    assert legacy["market_probability_substitution_allowed"] is False
    assert legacy["generic_reasoning_substitution_allowed"] is False
    assert legacy["can_execute"] is False
