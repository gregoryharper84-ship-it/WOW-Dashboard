from types import SimpleNamespace

from v17.mlb_team_event_hydration import _same_mlb_team, _team_match_strength
from v17.sep16_evidence_handoff_rank_fix import (
    RUN_INVALID_EVIDENCE_BINDING,
    _annotate_schema_mismatch,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.team_event_sport_parity import build_discovery_evidence, parity_health


def test_every_cataloged_sport_has_same_parity_contract_shape():
    health = parity_health({})
    assert set(health) == set(EXPECTED_TEAM_EVENT_SPORTS)

    shapes = {tuple(sorted(row.keys())) for row in health.values()}
    assert len(shapes) == 1
    for sport, row in health.items():
        assert row["sport"] == sport
        assert row["cataloged"] is True
        assert row["discovery_required"] is True
        assert row["canonical_identity_required"] is True
        assert row["hydration_owner"] != "UNASSIGNED"
        assert row["publication_is_row_scoped"] is True
        assert row["market_probability_substitution_allowed"] is False
        assert row["generic_reasoning_substitution_allowed"] is False
        assert row["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
        assert row["can_execute"] is False


def test_discovery_handoff_preserves_explicit_sporting_inputs_not_market_price():
    event = SimpleNamespace(
        sport="WNBA",
        provider="RUNDOWN",
        provider_sport_id="11",
        regime="REGULAR_SEASON",
        official_event_id="provider-123",
        raw={
            "price": -150,
            "implied_probability": 0.60,
            "evidence": {
                "home_win_pct": 0.64,
                "away_win_pct": 0.51,
                "expected_starters_rotation": "CONFIRMED",
            },
        },
    )
    registration = SimpleNamespace(
        required_inputs=("home_win_pct", "away_win_pct", "calibration_artifact")
    )

    evidence = build_discovery_evidence(event, registration)

    assert evidence["home_win_pct"] == 0.64
    assert evidence["away_win_pct"] == 0.51
    assert evidence["expected_starters_rotation"] == "CONFIRMED"
    assert "price" not in evidence
    assert "implied_probability" not in evidence
    assert evidence["market_probability_used_as_model"] is False
    assert evidence["generic_reasoning_used_as_model"] is False
    assert evidence["can_execute"] is False


def test_capability_state_is_equal_shape_not_fake_equal_availability():
    bridge_health = {
        "MLB": {
            "registered": True,
            "scorer_resolvable": True,
            "certification_status": "CERTIFIED",
        },
        "NBA": {
            "registered": False,
            "scorer_resolvable": False,
            "certification_status": "UNAVAILABLE",
        },
    }
    health = parity_health(bridge_health)

    assert health["MLB"]["model_capability_ready"] is True
    assert health["NBA"]["model_capability_ready"] is False
    assert health["MLB"]["publication_is_row_scoped"] is True
    assert health["NBA"]["publication_is_row_scoped"] is True
    assert health["NBA"]["can_execute"] is False


def test_mlb_provider_city_aliases_resolve_without_guessing_ambiguous_city():
    assert _same_mlb_team("San Francisco Giants", "San Francisco")
    assert _same_mlb_team("Minnesota Twins", "Minnesota")
    assert _team_match_strength("New York Yankees", "New York") == 1
    assert _team_match_strength("New York Mets", "New York") == 1
    assert _team_match_strength("Chicago Cubs", "Chicago") == 1
    assert _team_match_strength("Chicago White Sox", "Chicago") == 1
    assert _team_match_strength("Los Angeles Dodgers", "Los Angeles") == 1
    assert _team_match_strength("Los Angeles Angels", "Los Angeles") == 1
    assert _team_match_strength("San Francisco Giants", "Minnesota") == 0


def test_present_upstream_missing_downstream_is_run_invalid_not_no_pick():
    req = SimpleNamespace(
        official_event_id="823169",
        sport_specific_evidence={
            "home_lineup_status": "CONFIRMED",
            "away_lineup_status": "CONFIRMED",
        },
    )
    model_result = {
        "official_event_id": "823169",
        "independent_home_probability": 0.51,
        "independent_away_probability": 0.49,
        "favorite_failure_paths_json": [{"path": "BULLPEN"}],
        "favorite_failure_path_probability": 0.19,
        "largest_favorite_loss_path": "BULLPEN",
        "underdog_upset_path_json": [{"path": "BULLPEN"}],
        "lineup_context": {"status": "CONFIRMED"},
        "calibrated_probability": 0.52,
        "calibrated_lower_bound": 0.44,
    }
    downstream = {
        "blockers": [
            "HOME_LINEUP_NOT_CALLED",
            "AWAY_LINEUP_NOT_CALLED",
            "INDEPENDENT_PROBABILITY_MISSING",
            "FAVORITE_FAILURE_PATHS_MISSING",
            "OFFICIAL_EVENT_ID_EVIDENCE_MISSING",
        ],
        "calibrated_probability": 0.52,
        "calibrated_lower_bound": 0.44,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }

    out = _annotate_schema_mismatch(req, model_result, downstream)

    assert out["run_validity_status"] == RUN_INVALID_EVIDENCE_BINDING
    assert RUN_INVALID_EVIDENCE_BINDING in out["blockers"]
    assert out["calibrated_probability"] == 0.52
    assert out["calibrated_lower_bound"] == 0.44
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False
