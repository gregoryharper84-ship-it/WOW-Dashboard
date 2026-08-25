from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from gate_engine.scan_integrity import (
    DATA_UNOBTAINABLE,
    MLB_1IP,
    MLB_UPSET_DISCOVERY,
    OUTRIGHT_WINNER,
    PLAYER_PROP,
    WNBA_PRA,
    build_scan_integrity_report,
)
from jobs.wow_daily_scan import _mlb_moneyline_discovery, run_scan
from services.odds_api import _normalize_rundown_to_h2h_events


def _h2h_event(event_id="mlb-event-1"):
    now = datetime.now(timezone.utc)
    return {
        "id": event_id,
        "home_team": "Home Club",
        "away_team": "Away Club",
        "commence_time": (now + timedelta(hours=4)).isoformat(),
        "bookmakers": [{
            "key": "book-one",
            "last_update": (now - timedelta(minutes=5)).isoformat(),
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Home Club", "price": -140},
                    {"name": "Away Club", "price": 120},
                ],
            }],
        }],
    }


def _score_result(row, enrichment=None):
    return {
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "blockers": [],
        "model_id": "mlb-moneyline-logit-v1",
        "model_status": "ACTIVE",
        "probability_snapshot": {
            "raw_probability": 0.64,
            "calibrated_probability": 0.62,
            "calibrated_probability_lower_bound": 0.57,
            "calibrated_probability_upper_bound": 0.67,
            "net_edge": 0.04,
            "probability_audit": {"passed": True},
            "moneyline_architecture_layers": {
                "classification": {"qualification_gate": "QUALIFIES"},
            },
        },
        "can_execute": False,
    }


def test_mlb_h2h_discovery_scores_both_sides_in_disjoint_winner_and_upset_lanes():
    with patch(
        "gate_engine.moneyline.team_acquisition.acquire_team_data",
        return_value={"season_win_pct": 0.6},
    ), patch(
        "gate_engine.moneyline_probability.score_outright_winner_row",
        side_effect=_score_result,
    ):
        result = _mlb_moneyline_discovery(
            [_h2h_event()], "AVAILABLE: h2h acquired", "2026-08-23"
        )

    assert len(result["inventory_by_family"][OUTRIGHT_WINNER]) == 1
    assert len(result["inventory_by_family"][MLB_UPSET_DISCOVERY]) == 1
    assert result["evaluated_by_family"][OUTRIGHT_WINNER] == 1
    assert result["evaluated_by_family"][MLB_UPSET_DISCOVERY] == 1
    assert result["terminal_by_family"] == result["evaluated_by_family"]
    assert result["source_by_family"][OUTRIGHT_WINNER]["events"].startswith("AVAILABLE")
    assert all(
        row["market_family"] == OUTRIGHT_WINNER
        for row in result["candidates_by_family"][OUTRIGHT_WINNER]
        + result["candidates_by_family"][MLB_UPSET_DISCOVERY]
    )
    assert all(
        row["can_execute"] is False
        for rows in result["candidates_by_family"].values()
        for row in rows
    )


def test_active_mlb_h2h_failure_is_a_visible_integrity_failure_not_no_board_inventory():
    odds_status = {
        "events": "AVAILABLE: event list acquired",
        "props": "NOT_CALLED: no events",
        "coverage_props": "AVAILABLE: 1/1 event prop fetches completed",
        "event_count": 1,
    }
    with patch("jobs.wow_daily_scan.fetch_all_props", return_value=([], odds_status)), patch(
        "jobs.wow_daily_scan.fetch_backup_props",
        return_value=([], "NOT_CALLED: backup has no inventory"),
    ), patch(
        "jobs.wow_daily_scan.get_h2h_odds",
        return_value=([], "FAILED: upstream timeout"),
    ):
        result = run_scan(sports=["MLB"], environment="test")

    by_lane = {
        row["lane_id"]: row for row in result["scan_integrity"]["coverage_matrix"]
    }
    assert result["run_status"] == "DEGRADED_ENGINE_RUN"
    assert by_lane[f"MLB:{OUTRIGHT_WINNER}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert by_lane[f"MLB:{MLB_UPSET_DISCOVERY}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert result["moneyline_discovery"]["source_status"].startswith("FAILED")


def test_daily_scan_exposes_real_h2h_candidates_without_requiring_prop_inventory():
    odds_status = {
        "events": "AVAILABLE: event list acquired",
        "props": "NOT_CALLED: no events",
        "coverage_props": "AVAILABLE: 1/1 event prop fetches completed",
        "event_count": 1,
    }
    with patch("jobs.wow_daily_scan.fetch_all_props", return_value=([], odds_status)), patch(
        "jobs.wow_daily_scan.fetch_backup_props",
        return_value=([], "NOT_CALLED: backup has no inventory"),
    ), patch(
        "jobs.wow_daily_scan.get_h2h_odds",
        return_value=([_h2h_event()], "AVAILABLE: h2h acquired"),
    ), patch(
        "gate_engine.moneyline.team_acquisition.acquire_team_data",
        return_value={"season_win_pct": 0.6},
    ), patch(
        "gate_engine.moneyline_probability.score_outright_winner_row",
        side_effect=_score_result,
    ):
        result = run_scan(sports=["MLB"], environment="test")

    by_lane = {
        row["lane_id"]: row for row in result["scan_integrity"]["coverage_matrix"]
    }
    assert len(result["moneyline_discovery"]["candidates"]) == 2
    assert by_lane[f"MLB:{OUTRIGHT_WINNER}"]["coverage_outcome"] == "COMPLETED"
    assert by_lane[f"MLB:{MLB_UPSET_DISCOVERY}"]["coverage_outcome"] == "COMPLETED"
    assert by_lane[f"MLB:{MLB_1IP}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert result["run_status"] == "DEGRADED_ENGINE_RUN"
    assert result["can_execute"] is False
    assert result["dry_run_only"] is True
    assert result["moneyline_discovery"]["candidates"][0]["can_execute"] is False


def test_active_wnba_pra_omitted_by_upstream_acquisition_degrades_the_run():
    odds_status = {
        "events": "AVAILABLE: event list acquired",
        "props": "NOT_CALLED: no props returned",
        "coverage_props": "AVAILABLE: 1/1 event prop fetches completed",
        "event_count": 1,
    }
    with patch(
        "jobs.wow_daily_scan.fetch_all_props",
        return_value=([], odds_status),
    ), patch(
        "jobs.wow_daily_scan.fetch_backup_props",
        return_value=([], "NOT_CALLED: backup has no inventory"),
    ):
        result = run_scan(sports=["WNBA"], environment="test")

    by_lane = {
        row["lane_id"]: row for row in result["scan_integrity"]["coverage_matrix"]
    }
    assert by_lane[f"WNBA:{WNBA_PRA}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert result["run_status"] == "DEGRADED_ENGINE_RUN"
    assert result["scan_integrity"]["reconciliation"]["integrity_valid"] is False


def test_one_sided_h2h_event_is_recorded_as_unresolved_instead_of_dropped():
    event = _h2h_event()
    event["bookmakers"][0]["markets"][0]["outcomes"] = [{
        "name": "Home Club", "price": -140,
    }]
    result = _mlb_moneyline_discovery(
        [event], "AVAILABLE: h2h acquired", "2026-08-23"
    )

    assert result["acquisition_status"].startswith("FAILED")
    assert result["normalization_failures"] == [{
        "event_id": "mlb-event-1",
        "reason": "H2H_TWO_SIDED_MARKET_UNAVAILABLE",
        "missing_participants": ["Away Club"],
    }]


def test_rundown_h2h_fallback_keeps_a_stable_event_id_for_moneyline_discovery():
    now = datetime.now(timezone.utc)
    fallback_event = {
        "event_date": (now + timedelta(hours=4)).isoformat(),
        "teams_normalized": [{"name": "Home Club"}, {"name": "Away Club"}],
        "lines": {
            "book": {
                "moneyline": {
                    "moneyline_home": -140,
                    "moneyline_away": 120,
                },
            },
        },
    }
    normalized = _normalize_rundown_to_h2h_events([fallback_event])
    assert normalized[0]["id"].startswith("rundown:")
    normalized[0]["bookmakers"][0]["markets"][0]["last_update"] = (
        now - timedelta(minutes=5)
    ).isoformat()

    with patch(
        "gate_engine.moneyline.team_acquisition.acquire_team_data",
        return_value={"season_win_pct": 0.6},
    ), patch(
        "gate_engine.moneyline_probability.score_outright_winner_row",
        side_effect=_score_result,
    ):
        result = _mlb_moneyline_discovery(
            normalized, "FALLBACK_RUNDOWN:AVAILABLE (1 events)", "2026-08-23"
        )

    assert result["acquisition_status"].startswith("AVAILABLE")
    assert sum(len(rows) for rows in result["inventory_by_family"].values()) == 2


def test_started_mlb_h2h_event_is_purged_before_candidate_publication():
    event = _h2h_event()
    event["commence_time"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with patch("gate_engine.moneyline_probability.score_outright_winner_row") as scorer:
        result = _mlb_moneyline_discovery(
            [event], "AVAILABLE: h2h acquired", datetime.now(timezone.utc).date().isoformat()
        )

    scorer.assert_not_called()
    assert sum(len(rows) for rows in result["candidates_by_family"].values()) == 0
    assert result["normalization_failures"][0]["reason"] == "EVENT_ALREADY_STARTED"


def test_board_signaled_special_lanes_without_independent_inventory_fail_closed():
    report = build_scan_integrity_report(["MLB", "WNBA"], {
        "MLB": {
            "active_events": 1,
            "events_status": "AVAILABLE",
            "props_status": "AVAILABLE",
            "expected_families": [MLB_1IP],
            "inventory": [],
            "source_by_family": {
                MLB_1IP: {
                    "events": "AVAILABLE",
                    "props": "FAILED: board-signaled lane has no independent source inventory",
                    "backup": "NOT_CALLED",
                },
            },
        },
        "WNBA": {
            "active_events": 1,
            "events_status": "AVAILABLE",
            "props_status": "AVAILABLE",
            "expected_families": [WNBA_PRA],
            "inventory": [],
            "source_by_family": {
                WNBA_PRA: {
                    "events": "AVAILABLE",
                    "props": "FAILED: board-signaled lane has no independent source inventory",
                    "backup": "NOT_CALLED",
                },
            },
        },
    })
    by_lane = {row["lane_id"]: row for row in report["coverage_matrix"]}

    assert by_lane[f"MLB:{MLB_1IP}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert by_lane[f"WNBA:{WNBA_PRA}"]["coverage_outcome"] == DATA_UNOBTAINABLE
    assert report["reconciliation"]["integrity_valid"] is False


def test_special_prop_inventory_is_not_counted_again_in_generic_player_prop_lane():
    one_ip = {"sport": "MLB", "player": "P", "prop": "1ip_pitches", "line": 15.5}
    generic = {"sport": "MLB", "player": "B", "prop": "batter_hits", "line": 0.5}
    report = build_scan_integrity_report(["MLB"], {
        "MLB": {
            "active_events": 1,
            "events_status": "AVAILABLE",
            "props_status": "AVAILABLE",
            "inventory": [one_ip, generic],
            "inventory_by_family": {
                PLAYER_PROP: [generic],
                MLB_1IP: [one_ip],
                OUTRIGHT_WINNER: [],
                MLB_UPSET_DISCOVERY: [],
            },
            "evaluated_by_family": {PLAYER_PROP: 1, MLB_1IP: 1},
            "terminal_by_family": {PLAYER_PROP: 1, MLB_1IP: 1},
        },
    })
    by_lane = {row["lane_id"]: row for row in report["coverage_matrix"]}

    assert by_lane["MLB:PLAYER_PROP"]["received_inventory_count"] == 1
    assert by_lane[f"MLB:{MLB_1IP}"]["received_inventory_count"] == 1
    assert report["reconciliation"]["received_inventory_count"] == 2


def test_limit_trimmed_rows_stay_unresolved_and_request_targeted_refresh():
    first = {"sport": "WNBA", "player": "A", "prop": "player_points", "line": 20.5}
    second = {"sport": "WNBA", "player": "B", "prop": "player_points", "line": 20.5}
    report = build_scan_integrity_report(["WNBA"], {
        "WNBA": {
            "active_events": 1,
            "events_status": "AVAILABLE",
            "props_status": "AVAILABLE",
            "inventory": [first, second],
            "inventory_by_family": {PLAYER_PROP: [first, second]},
            "evaluated_by_family": {PLAYER_PROP: 1},
            "terminal_by_family": {PLAYER_PROP: 1},
        },
    })
    lane = report["coverage_matrix"][0]

    assert lane["coverage_outcome"] == DATA_UNOBTAINABLE
    assert lane["unresolved_inventory_count"] == 1
    assert lane["targeted_refresh_count"] == 1
    assert report["reconciliation"]["unresolved_inventory_lanes"] == ["WNBA:PLAYER_PROP"]


def test_compact_daily_scan_cards_cannot_advertise_execution():
    from app import _compact_prop

    assert _compact_prop({
        "player": "Sample Player",
        "sport": "MLB",
        "prop": "batter_hits",
        "classification": "Market Verified Approved",
    })["can_execute"] is False