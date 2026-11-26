from datetime import date
from pathlib import Path

import pytest
from v17 import season_readiness as readiness

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


_ACTIVE_SPEC = {
    "calendar_kind": "bounded",
    "regular_season_start": "2026-05-08",
    "regular_season_end": "2026-09-24",
    "postseason_start": "2026-09-27",
    "postseason_end": "2026-10-31",
    "calendar_valid_through": "2026-10-31",
}


@pytest.mark.parametrize("day,expected", [
    (date(2026, 5, 7), (False, "PRESEASON")),
    (date(2026, 5, 8), (True, "OPENING_RAMP")),
    (date(2026, 8, 1), (True, "REGULAR_SEASON")),
    (date(2026, 10, 31), (True, "POSTSEASON")),
    (date(2026, 11, 24), (False, "CALENDAR_REFRESH_REQUIRED")),
    (date(2026, 1, 1), (False, "OFFSEASON")),
])
def test_scheduled_work_admission_uses_verified_season_boundary(day, expected):
    assert readiness.new_work_admission(_ACTIVE_SPEC, day) == expected


def test_competition_calendar_does_not_auto_authorize_new_work():
    assert readiness.new_work_admission(
        {"calendar_kind": "competition_driven"}, date(2026, 11, 24)
    ) == (False, "COMPETITION_CALENDAR_REQUIRED")


def test_calendar_refresh_allows_next_season_reentry():
    refreshed = {**_ACTIVE_SPEC, "regular_season_start": "2027-05-08",
                 "regular_season_end": "2027-09-24", "postseason_start": "2027-09-27",
                 "postseason_end": "2027-10-31", "calendar_valid_through": "2027-10-31"}
    assert readiness.new_work_admission(refreshed, date(2027, 5, 8)) == (True, "OPENING_RAMP")


@pytest.mark.parametrize("spec", [None, {}, {**_ACTIVE_SPEC, "calendar_valid_through": None},
    {**_ACTIVE_SPEC, "regular_season_start": "invalid"},
    {**_ACTIVE_SPEC, "regular_season_start": "2026-10-01"}])
def test_missing_or_invalid_calendar_does_not_authorize_acquisition(spec):
    assert readiness.new_work_admission(spec, date(2026, 11, 24)) == (False, "CALENDAR_UNVERIFIED")


@pytest.mark.parametrize("change", [
    {"postseason_start": "2026-01-01"},
    {"postseason_end": "2026-09-01"},
    {"championship_start": "2026-09-01"},
])
def test_inconsistent_postseason_calendar_is_not_admitted(change):
    assert readiness.new_work_admission({**_ACTIVE_SPEC, **change}, date(2026, 10, 1)) == (False, "CALENDAR_UNVERIFIED")


def test_readiness_board_reconciles_all_fourteen_declared_categories():
    board = build_board(REGISTRY, {}, on_date=date(2026, 11, 24))
    assert {row["sport"] for row in board["rows"]} == {
        "NFL", "NBA", "NHL", "NCAAF", "NCAAB", "NCAAW", "SOCCER", "TENNIS",
        "GOLF", "MMA", "BOXING", "CRICKET", "MLB", "WNBA",
    }
    assert _row(board, "NCAAW")["readiness"]["ready"] is False
    assert board["can_execute"] is False


def test_sport_lookup_does_not_lose_active_league_due_to_unrelated_bad_calendar():
    nba = {**_ACTIVE_SPEC, "engine": "LLP", "calendar_sources": [{"url": "https://example.com/nba", "verified_at": "2026-05-01"}]}
    registry = {"version": "1.0", "sports": {"NBA": nba, "WNBA": None}}
    assert readiness.new_work_admission_for_sport(registry, "NBA", date(2026, 8, 1)) == (True, "REGULAR_SEASON")
    assert readiness.new_work_admission_for_sport(registry, "WNBA", date(2026, 8, 1)) == (False, "CALENDAR_UNVERIFIED")


def test_sport_lookup_requires_registry_version_and_registered_sport():
    assert readiness.new_work_admission_for_sport({"version": "2.0", "sports": {"NBA": _ACTIVE_SPEC}}, "NBA", date(2026, 8, 1)) == (False, "CALENDAR_UNVERIFIED")
    assert readiness.new_work_admission_for_sport({"version": "1.0", "sports": {}}, "NBA", date(2026, 8, 1)) == (False, "CALENDAR_UNVERIFIED")


def test_sport_lookup_rejects_calendar_verified_after_target_day():
    spec = {**_ACTIVE_SPEC, "calendar_sources": [{"url": "https://example.com/schedule", "verified_at": "2026-08-02"}]}
    assert readiness.new_work_admission_for_sport({"version": "1.0", "sports": {"NBA": spec}}, "NBA", date(2026, 8, 1)) == (False, "CALENDAR_UNVERIFIED")


@pytest.mark.parametrize("sport,exit_code,reason", [
    ("NFL", 0, None),
    ("MLB", 3, "CALENDAR_REFRESH_REQUIRED"),
    ("WNBA", 3, "CALENDAR_REFRESH_REQUIRED"),
    ("NCAAW", 3, "COMPETITION_CALENDAR_REQUIRED"),
    ("UNREGISTERED", 3, "CALENDAR_UNVERIFIED"),
])
def test_scheduler_cli_fails_closed_without_fabricating_season_state(sport, exit_code, reason, capsys):
    import json
    assert readiness.main(["admission", "--sport", sport, "--date", "2026-11-24"]) == exit_code
    row = json.loads(capsys.readouterr().out)
    assert row["new_work_allowed"] is (exit_code == 0)
    if reason is not None:
        assert row["reason"] == reason
    assert row["can_execute"] is False
