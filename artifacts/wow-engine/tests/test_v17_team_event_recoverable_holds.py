from v17.team_event_recoverable_hold_overlay import _typed_hold


def test_mlb_lineup_gap_is_recoverable_not_removed():
    detail = _typed_hold(
        {
            "status": "MODEL_INPUTS_INSUFFICIENT",
            "blocker_code": "MLB_TEAM_EVENT_LINEUP_NOT_YET_AVAILABLE",
            "missing_fields": ["home_lineup_status", "away_lineup_status"],
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
    )
    assert detail["candidate_state"] == "HELD_PENDING_FINAL_LINEUP"
    assert detail["recoverable_hold"] is True
    assert detail["candidate_removed"] is False
    assert detail["retry_trigger"] == "OFFICIAL_LINEUP_AVAILABLE"
    assert detail["probability_package_present"] is False
    assert detail["rank_eligible"] is False
    assert detail["can_execute"] is False


def test_calibration_gap_is_typed_separately_from_sport_evidence_gap():
    calibration = _typed_hold(
        {
            "code": "MODEL_INPUTS_INSUFFICIENT",
            "blockers": ["CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE"],
            "missing_fields": ["calibration_artifact"],
        }
    )
    assert calibration["candidate_state"] == "HELD_PENDING_CALIBRATION_ARTIFACT"
    assert calibration["retry_trigger"] == "CERTIFIED_CALIBRATION_ARTIFACT_AVAILABLE"
    assert calibration["candidate_removed"] is False

    evidence = _typed_hold(
        {
            "code": "MODEL_INPUTS_INSUFFICIENT",
            "blockers": ["SPORT_SPECIFIC_MODEL_INPUTS_INSUFFICIENT"],
            "missing_fields": ["injury_report", "expected_starters_rotation"],
        }
    )
    assert evidence["candidate_state"] == "HELD_PENDING_SPORT_EVIDENCE"
    assert evidence["retry_trigger"] == "REQUIRED_SPORT_STATUS_EVIDENCE_AVAILABLE"
    assert evidence["candidate_removed"] is False


def test_non_input_failure_is_not_rewritten_as_recoverable_hold():
    original = {
        "code": "MODEL_SCORER_FAILED",
        "blockers": ["TEAM_EVENT_SCORER_EXCEPTION"],
        "can_execute": False,
    }
    assert _typed_hold(original) == original


def test_recoverable_hold_is_bound_to_governed_hourly_refresh():
    detail = _typed_hold(
        {
            "code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": ["goalie_status"],
        }
    )
    assert detail["retry_required"] is True
    assert detail["durable_retry_watcher_bound"] is True
    assert detail["retry_watcher_mode"] == "HOURLY_CANONICAL_FULL_SLATE_REFRESH"
    assert detail["retry_watcher_workflow"] == "wow-v17-team-event-recoverable-refresh.yml"
    assert detail["retry_watcher_cadence_minutes"] == 60
    assert detail["probability_publishable"] is False
    assert detail["rank_eligible"] is False
    assert detail["can_execute"] is False
