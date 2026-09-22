from pathlib import Path
from types import SimpleNamespace

from v17.mlb_team_event_hydration import _same_mlb_team, _team_match_strength
from v17.sep16_evidence_handoff_rank_fix import (
    RUN_INVALID_EVIDENCE_BINDING,
    _annotate_schema_mismatch,
)


ROOT = Path(__file__).resolve().parents[1]


def test_mlb_provider_city_aliases_match_canonical_club_names():
    assert _same_mlb_team("San Francisco", "San Francisco Giants")
    assert _same_mlb_team("Minnesota", "Minnesota Twins")
    assert _same_mlb_team("Los Angeles Dodgers", "LA Dodgers")
    assert not _same_mlb_team("San Francisco", "Minnesota Twins")


def test_ambiguous_city_only_labels_are_compatible_but_not_exact_aliases():
    assert _team_match_strength("New York Yankees", "New York") == 1
    assert _team_match_strength("New York Mets", "New York") == 1
    assert _team_match_strength("Chicago Cubs", "Chicago") == 1
    assert _team_match_strength("Chicago White Sox", "Chicago") == 1
    assert _team_match_strength("Los Angeles Dodgers", "Los Angeles") == 1
    assert _team_match_strength("Los Angeles Angels", "Los Angeles") == 1
    assert _team_match_strength("Tampa Bay Rays", "Tampa Bay") == 2
    assert _team_match_strength("Minnesota Twins", "Minnesota") == 2


def test_evidence_binding_contradiction_is_typed_run_invalid_without_erasing_model_package():
    req = SimpleNamespace(
        sport_specific_evidence={
            "home_lineup_status": "CONFIRMED",
            "away_lineup_status": "CONFIRMED",
        }
    )
    model_result = {
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
            "FAVORITE_FAILURE_PATH_PROBABILITY_MISSING",
            "LARGEST_FAVORITE_LOSS_PATH_MISSING",
            "UNDERDOG_UPSET_PATH_MISSING",
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
    assert out["evidence_handoff_schema_mismatch"]["status"] == "FAIL"
    assert out["evidence_handoff_schema_mismatch"]["run_invalid_code"] == RUN_INVALID_EVIDENCE_BINDING
    assert out["calibrated_probability"] == 0.52
    assert out["calibrated_lower_bound"] == 0.44
    assert out["rank_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False


def test_score_timestamp_migration_uses_wall_clock_without_rewriting_history():
    migration = (
        ROOT / "migrations" / "20260921_v17_mlb_score_timestamp_chronology.sql"
    ).read_text().lower()

    assert "alter column model_timestamp set default clock_timestamp()" in migration
    assert "update public.wow_mlb_forward_score_snapshots" not in migration
    assert "can_execute" in migration


def test_failure_registry_contains_evidence_binding_run_invalid_code():
    registry = (ROOT / "docs" / "failure_codes.md").read_text()
    assert "`RUN_INVALID_EVIDENCE_BINDING`" in registry
    assert "producer→consumer binding" in registry
