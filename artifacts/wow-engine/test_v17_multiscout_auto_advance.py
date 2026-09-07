from __future__ import annotations

from v17.multiscout_auto_advance import build_dispatch, execute_auto_advance


def _handoff():
    prop = {
        "official_event_id": "mlb-event-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-08T00:10:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "route": "WOW_PROP_LANE",
        "discovery_status": "DISCOVERY_ONLY",
        "research_ceiling": "RESEARCH_INTEREST",
        "market_evidence": {
            "bookmaker": "book-a",
            "market_key": "pitcher_strikeouts",
            "outcome_name": "Over",
            "description": "Example Pitcher",
            "price": -120,
            "point": 5.5,
            "market_last_update": "2026-09-07T14:00:00Z",
        },
    }
    duplicate = {
        **prop,
        "market_evidence": {
            **prop["market_evidence"],
            "bookmaker": "book-b",
            "price": -118,
        },
    }
    event = {
        "official_event_id": "12345",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-08T00:10:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "discovery_status": "DISCOVERY_ONLY",
        "research_ceiling": "RESEARCH_INTEREST",
        "market_evidence": [],
    }
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": "DISCOVERY_COMPLETE",
        "generated_at": "2026-09-07T14:01:00Z",
        "run_id": "wow-scout-test-1",
        "research_run_id": "wow-scout-test-1",
        "model_handoff_ready": True,
        "model_handoff": {
            "prop_candidates": [prop, duplicate],
            "team_event_candidates": [event],
        },
        "governance": {
            "sportsbook_implied_probability_is_model_probability": False,
            "scout_consensus_is_model_probability": False,
            "v17_terminal_reducer_is_terminal_authority": True,
            "can_execute": False,
        },
    }


def test_build_dispatch_uses_canonical_governed_routes_and_dedupes_books():
    dispatch = build_dispatch(_handoff())

    assert dispatch["source_run_id"] == "wow-scout-test-1"
    assert dispatch["research_run_id"] == "wow-scout-test-1"
    assert dispatch["governance"]["can_execute"] is False
    assert dispatch["governance"]["scout_probability_authority"] is False

    assert len(dispatch["prop_batches"]) == 1
    prop_rows = dispatch["prop_batches"][0]["rows"]
    assert len(prop_rows) == 1
    row = prop_rows[0]
    assert row["sport"] == "MLB"
    assert row["player"] == "Example Pitcher"
    assert row["stat_type"] == "pitcher_strikeouts"
    assert row["line"] == 5.5
    assert row["direction"] == "MORE"
    assert row["source_type"] == "AUTONOMOUS_DISCOVERY"
    assert row["money_lane_status"] == "PAYOUT_UNRESOLVED"
    assert "model_probability" not in row
    assert "calibrated_probability" not in row
    assert "price" not in row

    assert dispatch["mapping"]["source_prop_rows"] == 2
    assert dispatch["mapping"]["mapped_prop_rows"] == 1
    assert dispatch["mapping"]["duplicate_prop_rows"] == 1
    assert dispatch["mapping"]["source_prop_reconciliation_pass"] is True

    team_rows = dispatch["team_event_batches"][0]["rows"]
    assert len(team_rows) == 2
    assert {row["objective_lane"] for row in team_rows} == {
        "OUTRIGHT_WIN_PROBABILITY",
        "UPSET_PROBABILITY",
    }
    assert all(row["research_run_id"] == "wow-scout-test-1" for row in team_rows)
    assert all(row["event_key"] == "MLB:12345" for row in team_rows)
    assert dispatch["mapping"]["source_team_reconciliation_pass"] is True


def test_missing_action_key_is_explicit_block_not_silent_success():
    receipt = execute_auto_advance(_handoff(), token=None)
    assert receipt["status"] == "BLOCKED_BACKEND_HANDOFF"
    assert receipt["code"] == "WOW_ACTION_API_KEY_UNCONFIGURED"
    assert receipt["source_run_id"] == "wow-scout-test-1"
    assert receipt["can_execute"] is False
    assert receipt["prop_receipts"] == []
    assert receipt["team_event_receipts"] == []


def test_downstream_held_rows_are_reconciled_governed_completion():
    calls = []

    def fake_post(origin, path, token, payload):
        calls.append((origin, path, token, payload))
        if path == "/score-pick-request":
            return {
                "ok": True,
                "http_status": 200,
                "body": {
                    "rows": [
                        {
                            "row_key": payload["rows"][0]["row_key"],
                            "terminal_status": "HELD",
                            "code": "MODEL_UNAVAILABLE",
                            "can_execute": False,
                        }
                    ],
                    "reconciliation_pass": True,
                    "can_execute": False,
                },
                "can_execute": False,
            }
        return {
            "ok": True,
            "http_status": 200,
            "body": {
                "rows": [
                    {
                        "terminal_status": "COMPLETED",
                        "code": "SPORTING_PROBABILITY_COMPLETED",
                        "can_execute": False,
                    },
                    {
                        "terminal_status": "HELD",
                        "code": "INPUT_INCOMPLETE",
                        "can_execute": False,
                    },
                ],
                "reconciliation_pass": True,
                "can_execute": False,
            },
            "can_execute": False,
        }

    receipt = execute_auto_advance(
        _handoff(), token="test-token", origin="https://example.invalid", post_fn=fake_post
    )

    assert [call[1] for call in calls] == [
        "/score-pick-request",
        "/score-team-event-request",
    ]
    assert receipt["status"] == "AUTO_ADVANCE_COMPLETE_WITH_BLOCKERS"
    assert receipt["code"] == "DOWNSTREAM_GOVERNED_BLOCKERS_PRESENT"
    assert receipt["reconciliation"]["expected_prop_rows"] == 1
    assert receipt["reconciliation"]["returned_prop_rows"] == 1
    assert receipt["reconciliation"]["expected_team_objective_rows"] == 2
    assert receipt["reconciliation"]["returned_team_objective_rows"] == 2
    assert receipt["reconciliation"]["response_reconciliation_pass"] is True
    assert receipt["reconciliation"]["completed_downstream_rows"] == 1
    assert receipt["reconciliation"]["blocked_downstream_rows"] == 2
    assert receipt["reconciliation"]["mapping_blocked_rows"] == 0
    assert receipt["can_execute"] is False


def test_mapping_rejects_unusable_prop_without_silent_drop():
    handoff = _handoff()
    handoff["model_handoff"]["prop_candidates"] = [
        {
            **handoff["model_handoff"]["prop_candidates"][0],
            "market_evidence": {
                **handoff["model_handoff"]["prop_candidates"][0]["market_evidence"],
                "outcome_name": "Yes",
            },
        }
    ]
    dispatch = build_dispatch(handoff)
    assert dispatch["mapping"]["source_prop_rows"] == 1
    assert dispatch["mapping"]["mapped_prop_rows"] == 0
    assert dispatch["mapping"]["duplicate_prop_rows"] == 0
    assert dispatch["mapping"]["rejected_prop_rows"] == [
        {"source_index": 1, "code": "PROP_DIRECTION_UNSUPPORTED"}
    ]
    assert dispatch["mapping"]["source_prop_reconciliation_pass"] is True


def test_mapping_reject_is_complete_with_blockers_not_silent_success():
    handoff = _handoff()
    handoff["model_handoff"]["prop_candidates"] = [
        {
            **handoff["model_handoff"]["prop_candidates"][0],
            "market_evidence": {
                **handoff["model_handoff"]["prop_candidates"][0]["market_evidence"],
                "outcome_name": "Yes",
            },
        }
    ]

    def fake_post(origin, path, token, payload):
        assert path == "/score-team-event-request"
        return {
            "ok": True,
            "http_status": 200,
            "body": {
                "rows": [
                    {"terminal_status": "COMPLETED", "code": "SPORTING_PROBABILITY_COMPLETED", "can_execute": False},
                    {"terminal_status": "COMPLETED", "code": "SPORTING_PROBABILITY_COMPLETED", "can_execute": False},
                ],
                "reconciliation_pass": True,
                "can_execute": False,
            },
            "can_execute": False,
        }

    receipt = execute_auto_advance(
        handoff,
        token="test-token",
        origin="https://example.invalid",
        post_fn=fake_post,
    )
    assert receipt["status"] == "AUTO_ADVANCE_COMPLETE_WITH_BLOCKERS"
    assert receipt["code"] == "SCOUT_MAPPING_BLOCKERS_PRESENT"
    assert receipt["reconciliation"]["mapping_blocked_rows"] == 1
    assert receipt["reconciliation"]["response_reconciliation_pass"] is True
    assert receipt["can_execute"] is False
