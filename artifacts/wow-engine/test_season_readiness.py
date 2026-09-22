from datetime import date
from pathlib import Path

from v17.season_readiness import (
    BASE_WEEK1_GATES,
    CAN_EXECUTE,
    TERMINAL_AUTHORITY,
    build_board,
    load_json,
    phase_for,
    required_gates_for_phase,
    validate_registry,
)


REGISTRY_PATH = Path(__file__).parent / "v17" / "season-readiness-registry.json"
REGISTRY = load_json(REGISTRY_PATH)


def _row(board, sport):
    return next(item for item in board["rows"] if item["sport"] == sport)


def test_registry_is_valid():
    assert validate_registry(REGISTRY) == []


def test_september_22_2026_phase_map_is_season_aware():
    board = build_board(REGISTRY, {}, on_date=date(2026, 9, 22))

    assert _row(board, "MLB")["phase"] == "POSTSEASON_PREP"
    assert _row(board, "WNBA")["phase"] == "POSTSEASON_PREP"
    assert _row(board, "NFL")["phase"] == "OPENING_RAMP"
    assert _row(board, "NCAAF")["phase"] == "REGULAR_SEASON"
    assert _row(board, "NHL")["phase"] == "PRESEASON"
    assert _row(board, "NBA")["phase"] == "PRESEASON"


def test_week1_milestones_raise_preseason_priority_before_opening_day():
    board = build_board(REGISTRY, {}, on_date=date(2026, 9, 22))
    nba = _row(board, "NBA")
    nhl = _row(board, "NHL")

    assert nba["days_to_regular_season_start"] == 28
    assert nba["active_readiness_milestone"] == "T-30"
    assert nba["priority"] == "SEASON_GATE_HIGH"

    assert nhl["days_to_regular_season_start"] == 7
    assert nhl["active_readiness_milestone"] == "T-7"
    assert nhl["priority"] == "SEASON_GATE_CRITICAL"


def test_postseason_prep_requires_extra_readiness_gates():
    required = required_gates_for_phase("POSTSEASON_PREP")
    assert "postseason_rules" in required
    assert "postseason_workload_context" in required
    assert "postseason_distribution_validation" in required


def test_all_base_gates_can_be_marked_ready_without_changing_probability_governance():
    state = {
        "sports": {
            "NFL": {
                "gates": {gate: "PASS" for gate in BASE_WEEK1_GATES}
            }
        }
    }
    board = build_board(REGISTRY, state, on_date=date(2026, 9, 22))
    nfl = _row(board, "NFL")

    assert nfl["readiness"]["ready"] is True
    assert board["can_execute"] is False
    assert board["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert CAN_EXECUTE is False
    assert TERMINAL_AUTHORITY == "V17_TERMINAL_REDUCER"


def test_calendar_staleness_fails_closed_instead_of_guessing_phase():
    nba = REGISTRY["sports"]["NBA"]
    assert phase_for(nba, date(2027, 4, 17)) == "CALENDAR_REFRESH_REQUIRED"


def test_competition_driven_sports_require_calendar_adapter_and_event_acceptance():
    board = build_board(REGISTRY, {}, on_date=date(2026, 9, 22))
    tennis = _row(board, "TENNIS")

    assert tennis["phase"] == "COMPETITION_DRIVEN"
    assert tennis["adapter"] == "TENNIS_TOUR_CALENDAR_ADAPTER"
    assert "competition_calendar_adapter" in tennis["readiness"]["required"]
    assert "event_acceptance" in tennis["readiness"]["required"]


def test_invalid_bounded_calendar_is_rejected():
    bad = {
        "version": "1.0",
        "sports": {
            "TEST": {
                "engine": "LLP_TEAM_EVENT_ENGINE",
                "calendar_kind": "bounded",
                "regular_season_start": "2026-10-01",
                "regular_season_end": "2026-09-01",
                "calendar_sources": [
                    {"url": "https://example.com/schedule", "verified_at": "2026-09-22"}
                ],
            }
        },
    }
    errors = validate_registry(bad)
    assert any("regular_season_start must not exceed" in error for error in errors)
