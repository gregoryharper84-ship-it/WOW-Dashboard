from __future__ import annotations

from v17.full_board_overlay import enrich_full_board_result


def _base_payload():
    return {
        "discovery": {
            "sports_queried": ["MLB", "WNBA", "NCAAF", "NHL"],
            "sports_with_events": ["MLB", "WNBA", "NCAAF"],
            "events_discovered": 3,
        },
        "rows": [
            {
                "event_key": "MLB:1",
                "official_event_id": "1",
                "sport": "MLB",
                "league": "MLB",
                "event_status": "PREGAME",
                "bucket": "MODEL_COMPLETED",
                "model_status": "SPORTING_PROBABILITY_COMPLETED",
                "rank_eligible": True,
                "probability_publishable": True,
                "detail": {
                    "calibrated_probability": 0.70,
                    "calibrated_lower_bound": 0.64,
                    "probability_package_valid": True,
                    "dynamic_calibration_complete": True,
                    "probability_audit_passed": True,
                    "event_governor_complete": True,
                    "final_refresh_passed": True,
                    "terminal_label": "FINAL_APPROVED",
                    "market_acquisition_status": "AUTH_FAILED",
                    "model_invoked": True,
                    "can_execute": False,
                    "global_terminal_authority": "V17_TERMINAL_REDUCER",
                },
            },
            {
                "event_key": "WNBA:2",
                "official_event_id": "2",
                "sport": "WNBA",
                "league": "WNBA",
                "event_status": "PREGAME",
                "bucket": "MODEL_UNAVAILABLE",
                "model_status": "MODEL_UNAVAILABLE",
                "rank_eligible": False,
                "probability_publishable": False,
                "detail": {
                    "reason": "WNBA_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE",
                    "model_invoked": False,
                },
            },
            {
                "event_key": "NCAAF:3",
                "official_event_id": "3",
                "sport": "NCAAF",
                "league": "NCAAF",
                "event_status": "PREGAME",
                "bucket": "MODEL_UNAVAILABLE",
                "model_status": "MODEL_UNAVAILABLE",
                "rank_eligible": False,
                "probability_publishable": False,
                "detail": {
                    "reason": "NCAAF_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE",
                    "model_invoked": False,
                },
            },
        ],
        "reconciliation": {
            "row_reconciliation": "PASS",
            "acquisition_audit": [
                {"family": "MLB", "request_status": "EVENTS_RETURNED"},
                {"family": "WNBA", "request_status": "EVENTS_RETURNED"},
                {"family": "NCAAF", "request_status": "EVENTS_RETURNED"},
                {"family": "NHL", "request_status": "NO_EVENTS_RETURNED"},
            ],
        },
        "can_execute": False,
    }


def test_overlay_preserves_every_sport_and_independent_status_axes():
    result = enrich_full_board_result(_base_payload())
    assert result["publication_complete"] is True
    assert result["full_board_contract"]["rows_discovered"] == 3
    assert result["full_board_contract"]["rows_final"] == 3
    assert result["full_board_contract"]["stages"] == [
        "DISCOVERED",
        "MODEL_CAPABILITY_CHECK",
        "MODEL_ATTEMPT",
        "GOVERNANCE",
        "FINAL",
    ]

    rows = {row["sport"]: row for row in result["rows"]}
    assert rows["MLB"]["market_acquisition_status"] == "AUTH_FAILED"
    assert rows["MLB"]["model_status"] == "SPORTING_PROBABILITY_COMPLETED"
    assert rows["MLB"]["rank_eligible"] is True
    assert rows["WNBA"]["model_status"] == "MODEL_UNAVAILABLE"
    assert rows["NCAAF"]["model_status"] == "MODEL_UNAVAILABLE"

    coverage = {
        row["sport"]: row
        for row in result["full_board_reconciliation"]["sport_coverage"]
    }
    assert coverage["WNBA"]["coverage_status"] == "MODEL_CAPABILITY_UNAVAILABLE"
    assert coverage["NCAAF"]["coverage_status"] == "MODEL_CAPABILITY_UNAVAILABLE"
    assert coverage["NHL"]["coverage_status"] == "NO_CURRENT_PREGAME_EVENTS"


def test_source_failure_is_not_mislabeled_as_no_events():
    payload = _base_payload()
    payload["discovery"]["sports_queried"].append("TENNIS")
    payload["reconciliation"]["acquisition_audit"].append(
        {"family": "TENNIS", "request_status": "PROVIDER_REQUEST_FAILED"}
    )
    result = enrich_full_board_result(payload)
    coverage = {
        row["sport"]: row
        for row in result["full_board_reconciliation"]["sport_coverage"]
    }
    assert coverage["TENNIS"]["coverage_status"] == "SOURCE_OR_STATUS_BLOCKED"
    assert coverage["TENNIS"]["discovery_acquisition_status"] == "PROVIDER_REQUEST_FAILED"


def test_numeric_nonranked_row_exposes_first_publication_chain_blocker():
    payload = _base_payload()
    mlb = payload["rows"][0]
    mlb["rank_eligible"] = False
    mlb["probability_publishable"] = False
    mlb["bucket"] = "OTHER_GOVERNED_HOLD"
    mlb["detail"]["probability_audit_passed"] = False
    mlb["detail"]["event_governor_complete"] = False
    result = enrich_full_board_result(payload)
    audit = result["rows"][0]["publication_chain_audit"]
    assert audit["status"] == "BLOCKED"
    assert audit["first_blocker"] == "PROBABILITY_AUDIT_NOT_COMPLETE"
    assert result["rows"][0]["rank_eligible"] is False


def test_capability_preflight_is_included_before_user_facing_publication():
    result = enrich_full_board_result(_base_payload())
    preflight = result["capability_preflight"]
    assert "registered_models" in preflight
    assert "sports" in preflight
    assert preflight["can_execute"] is False
