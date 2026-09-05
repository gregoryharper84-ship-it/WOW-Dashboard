from copy import deepcopy

from v17.team_event_official_publication_guard import (
    audit_official_team_event_publication,
    official_team_event_rank_eligible,
)


def _valid_mlb_row():
    failure_paths = {
        "schema_version": "WOW_V16_MLB_FAILURE_PATH_V1",
        "regimes": [
            {"name": "STARTER_UNDERPERFORMANCE", "loss_joint_probability": 0.12},
            {"name": "BULLPEN_FAILURE", "loss_joint_probability": 0.08},
            {"name": "LOW_SCORING_ONE_RUN_VARIANCE", "loss_joint_probability": 0.06},
        ],
        "largest_favorite_loss_path": "STARTER_UNDERPERFORMANCE",
        "favorite_failure_path_probability": 0.21,
    }
    return {
        "sport": "MLB",
        "league": "MLB",
        "score_status": "MODEL_SCORED_PROSPECTIVE",
        "probability_publishable": True,
        "rank_eligible": True,
        "model_probability_publishable": True,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "calibration_health_status": "PASS",
        "model_valid_after_latest_update": True,
        "model_inputs_hash": "abc123",
        "bounds_method_version": "V2D_DYNAMIC_BOUND_PLUS_CONTEXT_HAIRCUT_V1",
        "calibrated_home_probability": 0.60,
        "calibrated_home_lower_bound": 0.55,
        "calibrated_home_upper_bound": 0.65,
        "calibrated_away_probability": 0.40,
        "calibrated_away_lower_bound": 0.35,
        "calibrated_away_upper_bound": 0.45,
        "lineup_context": {
            "status": "CONFIRMED",
            "lineup_identity_sha256": "lineuphash",
            "model_version": "MLB_LINEUP_PLATOON_SHRINK_V1",
        },
        "favorite_failure_paths_json": failure_paths,
        "largest_favorite_loss_path": "STARTER_UNDERPERFORMANCE",
        "favorite_failure_path_probability": 0.21,
        "regime_model_version": "MLB_FAILURE_REGIME_MIXTURE_V1",
        "can_execute": False,
    }


def test_current_certified_style_package_can_enter_official_ranking():
    audit = audit_official_team_event_publication(_valid_mlb_row())
    assert audit.eligible is True
    assert audit.blockers == ()
    assert official_team_event_rank_eligible(_valid_mlb_row()) is True
    assert audit.can_execute is False


def test_seattle_incident_style_shadow_row_is_never_official_rank_eligible():
    row = _valid_mlb_row()
    row.update(
        {
            "score_status": "SHADOW_SCORED_PREGAME",
            "probability_publishable": False,
            "rank_eligible": False,
            "model_probability_publishable": False,
            "home_bound_status": "PASS_RESEARCH_BOUND",
            "calibrated_home_probability": 0.563781072435688,
            "calibrated_home_lower_bound": 0.563781072435688,
            "calibrated_home_upper_bound": 0.788277663310722,
            "favorite_failure_paths_json": None,
        }
    )
    audit = audit_official_team_event_publication(row)
    assert audit.eligible is False
    assert "PROBABILITY_NOT_PUBLISHABLE" in audit.blockers
    assert "RANK_ELIGIBILITY_NOT_PROVEN" in audit.blockers
    assert "SHADOW_OR_RESEARCH_STATUS_NOT_OFFICIAL" in audit.blockers
    assert "HOME_CALIBRATED_BOUND_NOT_STRICT" in audit.blockers
    assert "FAVORITE_FAILURE_PATH_PACKAGE_MISSING" in audit.blockers


def test_point_estimate_cannot_be_relabelled_as_lower_bound():
    row = _valid_mlb_row()
    row["calibrated_home_lower_bound"] = row["calibrated_home_probability"]
    audit = audit_official_team_event_publication(row)
    assert audit.eligible is False
    assert "HOME_CALIBRATED_BOUND_NOT_STRICT" in audit.blockers


def test_missing_explicit_failure_path_package_blocks_official_ranking():
    row = _valid_mlb_row()
    row["favorite_failure_paths_json"] = None
    audit = audit_official_team_event_publication(row)
    assert audit.eligible is False
    assert "FAVORITE_FAILURE_PATH_PACKAGE_MISSING" in audit.blockers


def test_confirmed_mlb_lineup_must_be_bound_into_numeric_model_inputs():
    row = _valid_mlb_row()
    row["lineup_context"] = {
        "status": "CONFIRMED",
        "model_version": "MLB_LINEUP_PLATOON_SHRINK_V1",
        "lineup_identity_sha256": "",
    }
    audit = audit_official_team_event_publication(row)
    assert audit.eligible is False
    assert "MLB_LINEUP_IDENTITY_FINGERPRINT_MISSING" in audit.blockers


def test_projected_lineup_package_remains_research_only():
    row = _valid_mlb_row()
    row["lineup_context"] = {
        "status": "PROJECTED",
        "model_version": "MLB_LINEUP_SCENARIO_MIXTURE_V1",
        "lineup_identity_sha256": "projectedhash",
    }
    row["probability_publishable"] = False
    row["rank_eligible"] = False
    audit = audit_official_team_event_publication(row)
    assert audit.eligible is False
    assert "MLB_LINEUP_NOT_CONFIRMED_IN_MODEL_PACKAGE" in audit.blockers


def test_terminal_reducer_receipt_is_mandatory():
    row = _valid_mlb_row()
    row.pop("global_terminal_authority")
    audit = audit_official_team_event_publication(row)
    assert audit.eligible is False
    assert "V17_TERMINAL_REDUCER_RECEIPT_MISSING" in audit.blockers


def test_can_execute_must_remain_false():
    row = deepcopy(_valid_mlb_row())
    row["can_execute"] = True
    audit = audit_official_team_event_publication(row)
    assert audit.eligible is False
    assert "CAN_EXECUTE_INVARIANT_NOT_PROVEN" in audit.blockers
