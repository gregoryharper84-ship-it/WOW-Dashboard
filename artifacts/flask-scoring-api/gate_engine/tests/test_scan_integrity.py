from __future__ import annotations

from gate_engine.scan_integrity import (
    COMPLETED,
    DATA_UNOBTAINABLE,
    MLB_1IP,
    MLB_UPSET_DISCOVERY,
    MODEL_UNAVAILABLE,
    NO_BOARD_INVENTORY,
    OUTRIGHT_WINNER,
    PLAYER_PROP,
    WNBA_PRA,
    build_scan_integrity_report,
    correlate_board_delta,
)


def _prop(sport="MLB", prop="batter_hits", **extra):
    row = {"sport": sport, "player": "Sample Player", "prop": prop, "line": 0.5}
    row.update(extra)
    return row


def _report(requested, observations):
    return build_scan_integrity_report(requested, observations)


def _by_lane(report):
    return {row["lane_id"]: row for row in report["coverage_matrix"]}


def test_active_mlb_has_exact_once_winner_and_upset_coverage_even_without_h2h_board():
    report = _report(["MLB"], {
        "MLB": {
            "active_events": 2, "events_status": "AVAILABLE", "props_status": "AVAILABLE",
            "inventory": [_prop()], "evaluated_rows": 1,
        }
    })
    rows = _by_lane(report)

    assert rows["MLB:PLAYER_PROP"]["coverage_outcome"] == COMPLETED
    assert rows[f"MLB:{OUTRIGHT_WINNER}"]["coverage_outcome"] == NO_BOARD_INVENTORY
    assert rows[f"MLB:{MLB_UPSET_DISCOVERY}"]["coverage_outcome"] == NO_BOARD_INVENTORY
    assert rows[f"MLB:{MLB_1IP}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert report["reconciliation"]["exact_once"] is True
    assert report["reconciliation"]["integrity_valid"] is False


def test_mlb_1ip_and_wnba_pra_are_independent_inventory_lanes():
    report = _report(["MLB", "WNBA"], {
        "MLB": {
            "active_events": 1, "events_status": "AVAILABLE", "props_status": "AVAILABLE",
            "inventory": [_prop(prop="1ip_pitches_thrown", line=15.5)], "evaluated_rows": 1,
            "evaluated_by_family": {MLB_1IP: 1},
            "terminal_by_family": {MLB_1IP: 1},
        },
        "WNBA": {
            "active_events": 1, "events_status": "AVAILABLE", "props_status": "AVAILABLE",
            "inventory": [_prop("WNBA", "player_points_rebounds_assists", line=25.5)],
            "evaluated_rows": 1,
            "evaluated_by_family": {WNBA_PRA: 1},
            "terminal_by_family": {WNBA_PRA: 1},
        },
    })
    rows = _by_lane(report)

    assert rows[f"MLB:{MLB_1IP}"]["received_inventory_count"] == 1
    assert rows[f"MLB:{MLB_1IP}"]["coverage_outcome"] == COMPLETED
    assert rows[f"WNBA:{WNBA_PRA}"]["received_inventory_count"] == 1
    assert rows[f"WNBA:{WNBA_PRA}"]["coverage_outcome"] == COMPLETED


def test_active_special_lanes_missing_from_acquisition_are_visible_and_fail_closed():
    report = _report(["MLB", "WNBA"], {
        "MLB": {
            "active_events": 1,
            "events_status": "AVAILABLE: event list acquired",
            "props_status": "AVAILABLE: generic player props acquired",
            "inventory": [_prop(prop="batter_hits")],
            "evaluated_rows": 1,
        },
        "WNBA": {
            "active_events": 1,
            "events_status": "AVAILABLE: event list acquired",
            "props_status": "AVAILABLE: generic player props acquired",
            "inventory": [_prop("WNBA", "player_points")],
            "evaluated_rows": 1,
        },
    })
    rows = _by_lane(report)

    assert rows[f"MLB:{MLB_1IP}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert rows[f"WNBA:{WNBA_PRA}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert rows[f"MLB:{MLB_1IP}"]["source_status"]["props"].startswith("FAILED")
    assert rows[f"WNBA:{WNBA_PRA}"]["source_status"]["props"].startswith("FAILED")
    assert report["reconciliation"]["integrity_valid"] is False


def test_unavailable_model_is_visible_and_never_replaced_by_generic_model():
    report = _report(["NHL"], {
        "NHL": {
            "active_events": 1, "events_status": "AVAILABLE", "props_status": "AVAILABLE",
            "inventory": [_prop("NHL", "player_goals", line=0.5)], "evaluated_rows": 1,
        }
    })
    row = _by_lane(report)["NHL:PLAYER_PROP"]
    assert row["coverage_outcome"] == MODEL_UNAVAILABLE
    assert row["specialist_available"] is False
    assert row["specialist_reason"] == "NO_REGISTERED_MODEL"


def test_source_failure_is_a_fail_closed_reconciliation_result():
    report = _report(["NBA"], {
        "NBA": {
            "active_events": 1, "events_status": "FAILED: timeout", "props_status": "FAILED: timeout",
            "backup_status": "FAILED: timeout", "inventory": [], "evaluated_rows": 0,
        }
    })
    row = _by_lane(report)["NBA:PLAYER_PROP"]
    assert row["coverage_outcome"] == DATA_UNOBTAINABLE
    assert report["reconciliation"]["integrity_valid"] is False
    assert report["reconciliation"]["public_label"] == "DEGRADED_ENGINE_RUN"


def test_row_count_mismatch_is_surfaced_instead_of_silently_reconciled():
    report = _report(["WNBA"], {
        "WNBA": {
            "active_events": 1, "events_status": "AVAILABLE", "props_status": "AVAILABLE",
            "inventory": [_prop("WNBA", "player_points")],
            "evaluated_rows": 2, "terminal_outcomes": 2,
        }
    })
    assert report["reconciliation"]["integrity_valid"] is False
    assert report["reconciliation"]["row_count_mismatch_lanes"] == ["WNBA:PLAYER_PROP"]


def test_no_active_events_is_complete_coverage_not_an_omitted_lane():
    report = _report(["NBA"], {
        "NBA": {
            "active_events": 0, "events_status": "AVAILABLE", "props_status": "NOT_CALLED: no events",
            "inventory": [], "evaluated_rows": 0,
        }
    })
    assert _by_lane(report)["NBA:PLAYER_PROP"]["coverage_outcome"] == "NO_ACTIVE_EVENTS"
    assert report["reconciliation"]["integrity_valid"] is True


def test_board_delta_enriches_but_never_narrows_cross_sport_discovery():
    previous = [
        {"sport": "MLB", "player": "A", "prop": "H", "direction": "MORE", "line": 0.5, "game": "old"},
        {"sport": "WNBA", "player": "B", "prop": "PRA", "direction": "MORE", "line": 25.5, "promo": "old"},
        {"sport": "NBA", "player": "C", "prop": "PTS", "direction": "MORE", "line": 20.5},
    ]
    current = [
        {"sport": "MLB", "player": "A", "prop": "H", "direction": "MORE", "line": 0.5, "game": "new"},
        {"sport": "WNBA", "player": "B", "prop": "PRA", "direction": "MORE", "line": 25.5, "promo": "new"},
        {"sport": "NFL", "player": "D", "prop": "REC", "direction": "MORE", "line": 3.5},
    ]
    delta = correlate_board_delta(current, previous)

    assert delta["board_enriches_discovery_only"] is True
    assert len(delta["added"]) == len(delta["removed"]) == len(delta["moved"]) == len(delta["promo_changed"]) == 1
    assert set(delta["targeted_refresh"]) == set(delta["added"] + delta["moved"] + delta["promo_changed"])


def test_unchanged_rows_reuse_only_explicitly_still_valid_evidence():
    row = {"sport": "WNBA", "player": "A", "prop": "PRA", "direction": "MORE", "line": 25.5}
    key = "WNBA|a|pra|MORE"
    reusable = correlate_board_delta([row], [dict(row)], {key: {
        "still_valid": True,
        "event_hydration": {"status": "FINAL"},
        "model_evidence": {"model_id": "wnba_counting_poisson_v1"},
    }})
    stale = correlate_board_delta([row], [dict(row)], {key: {"still_valid": False}})

    assert reusable["unchanged_reused"] == [key]
    assert reusable["unchanged"] == [key]
    assert reusable["reused_evidence"][0]["model_evidence"]["model_id"] == "wnba_counting_poisson_v1"
    assert reusable["targeted_refresh"] == []
    assert stale["unchanged_reused"] == []
    assert stale["targeted_refresh"] == [key]