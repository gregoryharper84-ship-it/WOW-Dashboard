from v17.team_event_official_publication_guard import evaluate_team_event_official_publication


def governed_result() -> dict:
    return {
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "llp_probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "event_mutex_status": "PASS",
        "calibrated_probability": 0.62,
        "calibrated_lower_bound": 0.58,
        "llp_governance": {
            "probability_publishable": True,
            "rank_eligible": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
            "probability_audit_result": "PASS_PROBABILITY_AUDIT",
            "event_mutex_status": "PASS",
            "postmodel_gates_status": "PASS",
            "final_gates_status": "PASS",
            "terminal_label": "FINAL_APPROVED",
        },
    }


def test_fully_governed_team_event_is_officially_publishable():
    decision = evaluate_team_event_official_publication(governed_result())
    assert decision["status"] == "PASS"
    assert decision["official_publication_allowed"] is True
    assert decision["probability_publishable"] is True
    assert decision["rank_eligible"] is True
    assert decision["publication_lane"] == "WINNER"
    assert decision["probability_tier"] == "WINNER_WATCH"
    assert decision["can_execute"] is False


def test_seattle_style_shadow_row_cannot_publish_from_two_booleans():
    row = {
        "source_mode": "FORWARD_SHADOW",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
        "calibrated_probability": 0.563781,
        "calibrated_lower_bound": 0.563781,
    }
    decision = evaluate_team_event_official_publication(row)
    assert decision["status"] == "HELD"
    assert decision["official_publication_allowed"] is False
    assert any("TEAM_EVENT_RESEARCH_ARTIFACT_NOT_OFFICIAL" in blocker for blocker in decision["blockers"])
    assert "TEAM_EVENT_LLP_GOVERNANCE_PACKAGE_MISSING" in decision["blockers"]


def test_missing_calibrated_bound_cannot_publish_even_with_governance_markers():
    row = governed_result()
    row.pop("calibrated_lower_bound")
    decision = evaluate_team_event_official_publication(row)
    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_CALIBRATED_BOUND_PACKAGE_NOT_PROVEN" in decision["blockers"]


def test_wrong_terminal_authority_fails_closed():
    row = governed_result()
    row["global_terminal_authority"] = "SCOUT"
    decision = evaluate_team_event_official_publication(row)
    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_TERMINAL_AUTHORITY_NOT_PROVEN" in decision["blockers"]


def test_minnesota_regression_best_side_cannot_publish_as_clean_winner():
    """MIN 52.39%, LB 42.91% must not leak from BEST_SIDE into winner publication."""
    row = governed_result()
    row.update({
        "candidate_family": "OUTRIGHT_WINNER",
        "decision_intent": "BEST_SIDE",
        "calibrated_probability": 0.5239,
        "calibrated_lower_bound": 0.4291,
        "market_role": "UNDERDOG",
    })
    decision = evaluate_team_event_official_publication(row)
    assert decision["status"] == "HELD"
    assert decision["official_publication_allowed"] is False
    assert decision["publication_lane"] == "WINNER"
    assert decision["probability_tier"] == "WINNER_REJECT"
    assert "TEAM_EVENT_UNDERDOG_ON_WINNER_LANE" in decision["blockers"]
    assert "TEAM_EVENT_WINNER_LOWER_BOUND_BELOW_WATCH_FLOOR" in decision["blockers"]
    assert decision["can_execute"] is False


def test_home_away_only_best_side_package_uses_same_selected_lower_bound_as_batch_dispatcher():
    row = governed_result()
    row.pop("calibrated_probability")
    row.pop("calibrated_lower_bound")
    row.update({
        "candidate_family": "TEAM_EVENT",
        "decision_intent": "WINNER",
        "calibrated_home_probability": 0.5239,
        "calibrated_home_lower_bound": 0.4291,
        "calibrated_away_probability": 0.4761,
        "calibrated_away_lower_bound": 0.4010,
    })

    decision = evaluate_team_event_official_publication(row)

    assert decision["status"] == "HELD"
    assert decision["selected_calibrated_probability"] == 0.5239
    assert decision["selected_calibrated_lower_bound"] == 0.4291
    assert decision["probability_tier"] == "WINNER_REJECT"
    assert "TEAM_EVENT_WINNER_LOWER_BOUND_BELOW_WATCH_FLOOR" in decision["blockers"]
    assert decision["can_execute"] is False


def test_minnesota_probability_fits_explicit_verified_upset_lane_only():
    """The same governed probability may be classified as a qualified upset, not a winner."""
    row = governed_result()
    row.update({
        "candidate_family": "UPSET",
        "decision_intent": "UPSET",
        "calibrated_probability": 0.5239,
        "calibrated_lower_bound": 0.4291,
        "market_role": "UNDERDOG",
        "underdog_verified": True,
    })
    decision = evaluate_team_event_official_publication(row)
    assert decision["status"] == "PASS"
    assert decision["official_publication_allowed"] is True
    assert decision["publication_lane"] == "UPSET"
    assert decision["probability_tier"] == "QUALIFIED_UPSET_PROFILE"
    assert decision["can_execute"] is False


def test_upset_lane_requires_verified_underdog_status():
    row = governed_result()
    row.update({
        "candidate_family": "UPSET",
        "decision_intent": "UPSET",
        "calibrated_probability": 0.51,
        "calibrated_lower_bound": 0.44,
    })
    decision = evaluate_team_event_official_publication(row)
    assert decision["official_publication_allowed"] is False
    assert "TEAM_EVENT_UPSET_UNDERDOG_STATUS_NOT_VERIFIED" in decision["blockers"]

def _certified_sample_evidence(sport: str = "NBA") -> dict:
    from datetime import datetime, timedelta, timezone
    from v17.cross_sport_evidence_integrity import TEAM_ROSTER_SPORTS

    start = datetime(2026, 10, 26, 23, tzinfo=timezone.utc)

    def participant(index):
        return {
            "participant_id": f"team-{index}",
            "snapshot_id": f"snapshot-{index}",
            "source_id": f"source-{index}",
            "verification_status": "VERIFIED",
            "source_timestamp": (start - timedelta(hours=3)).isoformat(),
            "effective_at": (start - timedelta(days=1)).isoformat(),
            "latest_material_update_at": (start - timedelta(hours=3)).isoformat(),
            **({
                "roster_reconciliation_status": "PASS",
                "personnel_snapshot_id": f"roster-{index}",
            } if sport in TEAM_ROSTER_SPORTS else {}),
        }

    return {
        "sport": sport,
        "official_event_id": "event-test",
        "event_start_utc": start.isoformat(),
        "model_timestamp": (start - timedelta(hours=1)).isoformat(),
        "participants": [participant(1), participant(2)],
        "feature_snapshot_hash": "a" * 64,
        "model_consumed_feature_hash": "a" * 64,
        "feature_consumption_verification_status": "VERIFIED",
        "critical_feature_consumption_complete": True,
        "model_artifact_version": "fitted-test-v1",
        "model_invoked": True,
        "required_modules": ["hydrate", "fitted_scorer", "calibrate"],
        "completed_modules": ["hydrate", "fitted_scorer", "calibrate"],
        "stage_errors": [],
    }


def test_opted_in_scoring_without_evidence_cannot_publish():
    row = governed_result()
    row["sport"] = "NBA"
    row["cross_sport_evidence_required"] = True
    decision = evaluate_team_event_official_publication(row)
    assert decision["status"] == "HELD"
    assert "TEAM_EVENT_EVIDENCE_PREFLIGHT_FAILED" in decision["blockers"]
    assert decision["evidence_integrity_audit"]["evidence_status"] == "HELD"
    assert row["calibrated_probability"] == 0.62  # sporting probability survives


def test_opted_in_scoring_with_current_verified_evidence_can_pass():
    row = governed_result()
    row["sport"] = "NBA"
    row["official_event_id"] = "event-test"
    row["cross_sport_evidence_required"] = True
    row["cross_sport_evidence"] = _certified_sample_evidence()
    decision = evaluate_team_event_official_publication(row)
    assert decision["status"] == "PASS"
    assert decision["evidence_integrity_audit"]["evidence_status"] == "PASS"
    assert decision["can_execute"] is False


def test_stale_or_mismatched_producer_evidence_blocks_publication():
    row = governed_result()
    row["sport"] = "NBA"
    row["official_event_id"] = "event-test"
    row["cross_sport_evidence_required"] = True
    row["cross_sport_evidence"] = _certified_sample_evidence()
    row["cross_sport_evidence"]["model_consumed_feature_hash"] = "b" * 64
    decision = evaluate_team_event_official_publication(row)
    assert decision["status"] == "HELD"
    assert "MODEL_FEATURE_SNAPSHOT_MISMATCH" in decision["evidence_integrity_audit"]["blockers"]
    assert "TEAM_EVENT_EVIDENCE_PREFLIGHT_FAILED" in decision["blockers"]
    assert decision["selected_calibrated_probability"] == 0.62


def test_cross_sport_evidence_event_identity_conflict_holds():
    row = governed_result()
    row["sport"] = "NFL"
    row["official_event_id"] = "event-test"
    row["cross_sport_evidence_required"] = True
    row["cross_sport_evidence"] = _certified_sample_evidence("NBA")
    decision = evaluate_team_event_official_publication(row)
    assert decision["status"] == "HELD"
    assert "SCORED_EVENT_EVIDENCE_IDENTITY_MISMATCH" in decision["evidence_integrity_audit"]["blockers"]


def test_legacy_results_never_claim_unearned_evidence_proof():
    decision = evaluate_team_event_official_publication(governed_result())
    assert decision["evidence_integrity_audit"]["evidence_status"] == "NOT_ENFORCED"
