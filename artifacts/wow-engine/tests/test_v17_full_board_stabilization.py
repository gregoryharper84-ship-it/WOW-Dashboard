from __future__ import annotations

from v17.full_board_stabilization import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_UNAVAILABLE,
    RANK_ELIGIBLE,
    canonicalize_provider_identity,
    classify_market_acquisition,
    compact_event_page,
    lineup_pending_status,
    publication_chain_audit,
    reconcile_full_board,
)


def test_rundown_401_is_typed_market_auth_failure_not_model_unavailable():
    status = classify_market_acquisition(
        "RUNDOWN", provider_code="RUNDOWN_HTTP_401", snapshot=None
    ).as_dict()
    assert status["market_acquisition_status"] == "AUTH_FAILED"
    assert status["market_snapshot_present"] is False
    assert status["auth_ok"] is False
    assert status["provider_code"] == "RUNDOWN_HTTP_401"
    assert status.get("model_status") is None
    assert status["can_execute"] is False


def test_valid_market_snapshot_does_not_create_sporting_probability():
    status = classify_market_acquisition(
        "RUNDOWN", provider_code="MARKET_EVIDENCE_FETCH_OK", snapshot={"events": [1]}
    ).as_dict()
    assert status["market_acquisition_status"] == "PASS"
    assert status["market_snapshot_present"] is True
    assert "calibrated_probability" not in status


def test_provider_id_is_never_promoted_to_official_event_id_by_guess():
    result = canonicalize_provider_identity(
        sport="NCAAF",
        league="NCAAF",
        official_event_id=None,
        provider_event_ids={"other_provider": "uuid-123"},
        scheduled_start_utc="2026-09-19T23:00:00Z",
    )
    assert result["status"] == "IDENTITY_UNRESOLVED"
    assert result["official_event_id"] is None
    assert result["provider_event_ids"] == {"OTHER_PROVIDER": "uuid-123"}
    assert result["canonical_event_key"] is None


def test_official_identity_produces_provider_neutral_canonical_key():
    result = canonicalize_provider_identity(
        sport="WNBA",
        league="WNBA",
        official_event_id="official-44",
        provider_event_ids={"ESPN": "401234", "RUNDOWN": "rd-44"},
        scheduled_start_utc="2026-09-19T20:00:00Z",
    )
    assert result["status"] == "PASS"
    assert result["canonical_event_key"] == "WNBA:WNBA:official-44:2026-09-19T20:00:00Z"
    assert result["provider_event_ids"]["ESPN"] == "401234"


def test_lineup_not_yet_available_is_retryable_inputs_insufficient():
    result = lineup_pending_status(
        sport="MLB",
        missing_inputs=["home_lineup_status", "away_lineup_status"],
        expected_refresh_at="2026-09-19T22:00:00Z",
    )
    assert result["model_status"] == MODEL_INPUTS_INSUFFICIENT
    assert result["input_availability"] == "NOT_YET_AVAILABLE"
    assert result["retry_recommended"] is True
    assert result["permanent_failure"] is False
    assert result["rank_eligible"] is False


def test_publication_chain_exposes_first_missing_stage():
    audit = publication_chain_audit(
        {
            "probability_package_valid": True,
            "dynamic_calibration_complete": True,
            "calibrated_probability": 0.68,
            "calibrated_lower_bound": 0.61,
            "probability_audit_passed": False,
            "event_governor_complete": False,
            "official_event_status": "PREGAME",
            "rank_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
        }
    ).as_dict()
    assert audit["status"] == "BLOCKED"
    assert audit["first_blocker"] == "PROBABILITY_AUDIT_NOT_COMPLETE"
    assert audit["stages"]["probability_package_valid"] is True
    assert audit["stages"]["dynamic_calibration_complete"] is True
    assert audit["rank_eligible"] is False


def test_complete_chain_requires_existing_rank_and_publication_proof():
    audit = publication_chain_audit(
        {
            "probability_package_valid": True,
            "dynamic_calibration_complete": True,
            "probability_audit_passed": True,
            "event_governor_complete": True,
            "calibrated_probability": 0.71,
            "calibrated_lower_bound": 0.65,
            "final_refresh_passed": True,
            "rank_eligible": True,
            "probability_publishable": True,
            "can_execute": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
        }
    ).as_dict()
    assert audit["status"] == "PASS"
    assert audit["rank_eligible"] is True
    assert audit["first_blocker"] is None


def test_full_board_retains_unsupported_wnba_and_ncaaf_rows():
    discovered = [
        {"candidate_id": "mlb-1", "sport": "MLB", "league": "MLB"},
        {"candidate_id": "wnba-1", "sport": "WNBA", "league": "WNBA"},
        {"candidate_id": "cfb-1", "sport": "NCAAF", "league": "NCAAF"},
    ]
    finals = [
        {
            "candidate_id": "mlb-1",
            "sport": "MLB",
            "model_status": "PASS",
            "rank_eligible": True,
            "probability_publishable": True,
            "can_execute": False,
        },
        {
            "candidate_id": "wnba-1",
            "sport": "WNBA",
            "model_status": MODEL_UNAVAILABLE,
            "code": MODEL_UNAVAILABLE,
            "rank_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        },
        {
            "candidate_id": "cfb-1",
            "sport": "NCAAF",
            "model_status": MODEL_UNAVAILABLE,
            "code": MODEL_UNAVAILABLE,
            "rank_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    ]
    report = reconcile_full_board(
        discovered,
        finals,
        inventoried_sports=("MLB", "WNBA", "NCAAF", "NHL"),
    )
    assert report["rows_discovered"] == 3
    assert report["rows_terminal"] == 3
    assert report["reconciliation_status"] == "PASS"
    assert report["rows_by_disposition"][RANK_ELIGIBLE] == 1
    assert report["rows_by_disposition"][MODEL_UNAVAILABLE] == 2
    by_sport = {row["sport"]: row for row in report["sport_coverage"]}
    assert by_sport["WNBA"]["coverage_status"] == "MODEL_CAPABILITY_UNAVAILABLE"
    assert by_sport["NCAAF"]["coverage_status"] == "MODEL_CAPABILITY_UNAVAILABLE"
    assert by_sport["NHL"]["coverage_status"] == "NO_CURRENT_PREGAME_EVENTS"


def test_missing_terminal_disposition_fails_reconciliation_without_dropping_row():
    report = reconcile_full_board(
        [{"candidate_id": "wnba-1", "sport": "WNBA"}],
        [],
        inventoried_sports=("WNBA",),
    )
    assert report["rows_discovered"] == 1
    assert report["rows_terminal"] == 1
    assert report["reconciliation_status"] == "FAIL"
    assert report["publication_complete"] is False
    assert report["missing_terminal_candidate_ids"] == ["wnba-1"]
    assert report["rows"][0]["code"] == "DISCOVERED_ROW_MISSING_TERMINAL_DISPOSITION"


def test_market_failure_and_model_failure_are_independent_axes():
    report = reconcile_full_board(
        [{"candidate_id": "ncaaf-1", "sport": "NCAAF"}],
        [{
            "candidate_id": "ncaaf-1",
            "sport": "NCAAF",
            "model_status": MODEL_UNAVAILABLE,
            "market_acquisition_status": "AUTH_FAILED",
            "rank_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }],
        inventoried_sports=("NCAAF",),
    )
    row = report["rows"][0]
    assert row["model_status"] == MODEL_UNAVAILABLE
    assert row["market_acquisition_status"] == "AUTH_FAILED"
    assert row["disposition"] == MODEL_UNAVAILABLE


def test_large_event_inventory_is_identity_paginated_not_full_payload():
    events = [
        {
            "id": f"espn-{i}",
            "official_event_id": f"official-{i}",
            "sport": "MLB",
            "league": "MLB",
            "start_time": "2026-09-19T20:00:00Z",
            "home_team": "A",
            "away_team": "B",
            "status": "SCHEDULED",
            "huge_nested_payload": {"ignored": "x" * 1000},
        }
        for i in range(250)
    ]
    page = compact_event_page(events, page=2, page_size=100)
    assert page["total_events"] == 250
    assert len(page["events"]) == 100
    assert page["has_more"] is True
    assert "huge_nested_payload" not in page["events"][0]
    assert page["events"][0]["provider_event_id"] == "espn-100"
