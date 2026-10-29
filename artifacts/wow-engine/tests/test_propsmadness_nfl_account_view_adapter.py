"""Offline-only regression tests for authenticated-view NFL outcome extraction."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_nfl_account_view_adapter import (
    SCHEMA, AccountViewHold, normalize_nfl_history_view,
)

URL = "https://propsmadness.com/nfl?market=featured&player=123&match=456"
CAPTURE = "2026-10-29T13:33:00Z"


def view(rows: str | None = None) -> str:
    rows = rows if rows is not None else (
        "<tr><th>Sep 13, 2026</th><td>vs Chicago Bears</td>"
        "<td>320</td><td>252.5</td><td>Over</td></tr>"
        "<tr><th>Oct 25, 2026</th><td>at Tampa Bay Buccaneers</td>"
        "<td>163</td><td>252.5</td><td>Under</td></tr>"
        "<tr><th>Oct 29, 2026</th><td>vs Green Bay Packers</td>"
        "<td>—</td><td>252.5</td><td>Upcoming</td></tr>"
    )
    return (
        'Main navigation NBA NFL MLB WNBA\n'
        '<button aria-label="Account menu"></button>\n'
        'NFL player props\n'
        'Taylor Sample\nPass Yds\n252.5\n'
        '<table>Game-by-game detail for the chart above'
        '<tr><th>Date</th><th>Opponent</th><th>Result</th>'
        '<th>Line</th><th>Over or under</th></tr>'
        + rows + '</table>\n'
        'Bar chart of Taylor Sample 2 games against a line of 252.5\n'
        'NBA player props\n'
        '<table>Game-by-game detail for the chart above'
        '<tr><th>Not</th></tr></table>\n'
    )


def run(text=None, **overrides):
    args = dict(
        rendered_view_text=view() if text is None else text,
        source_url=URL,
        captured_at=CAPTURE,
        selected_player_name="Taylor Sample",
        stat_type="PASSING_YARDS",
        account_ui_confirmed_by_operator=True,
    )
    args.update(overrides)
    return normalize_nfl_history_view(**args)


def test_three_rows_two_completed_one_upcoming_and_no_false_history_price():
    output = run()
    assert output["schema"] == SCHEMA
    assert output["row_count"] == 3
    assert output["completed_game_rows"] == 2
    assert output["upcoming_rows"] == 1
    assert [r["observed_stat"] for r in output["rows"]] == [320.0, 163.0, None]
    assert all(r["chart_comparison_threshold"] == 252.5 for r in output["rows"])
    assert all(r["historical_closing_line"] is None for r in output["rows"])
    assert all(r["historical_market_timestamp"] is None for r in output["rows"])
    assert all(r["official_event_id"] is None for r in output["rows"])
    assert output["rows"][2]["row_status"] == "UPCOMING_NOT_HISTORICAL"
    assert output["source_rights_verified"] is False
    assert output["historical_training_authorized"] is False
    assert output["production_storage_authorized"] is False
    assert output["official_identity_verified"] is False
    assert output["probability_publishable"] is False
    assert output["can_execute"] is False
    assert output["model_consumption_receipts"] == []


@pytest.mark.parametrize("kwargs,code", [
    ({"account_ui_confirmed_by_operator": False}, "PM_ACCOUNT_VIEW_OPERATOR_CONFIRMATION_MISSING"),
    ({"stat_type": "PASSING_TOUCHDOWNS"}, "PM_ACCOUNT_VIEW_MARKET_CONFLICT"),
    ({"source_url": URL.replace("propsmadness.com", "evil.example")}, "PM_ACCOUNT_VIEW_SOURCE_INVALID"),
    ({"source_url": URL + "&token=secret"}, "PM_ACCOUNT_VIEW_SOURCE_INVALID"),
    ({"source_url": URL.replace("market=featured", "market=player-receptions")}, "PM_ACCOUNT_VIEW_MARKET_CONFLICT"),
    ({"captured_at": "2026-10-29T13:33:00"}, "PM_ACCOUNT_VIEW_TIMESTAMP_INVALID"),
    ({"selected_player_name": "Other Person"}, "PM_ACCOUNT_VIEW_IDENTITY_UNRESOLVED"),
])
def test_fail_closed_scope_and_identity(kwargs, code):
    with pytest.raises(AccountViewHold, match=code):
        run(**kwargs)


def test_duplicate_game_cannot_be_silently_merged():
    row = ("<tr><th>Oct 25, 2026</th><td>vs Tampa Bay Buccaneers</td>"
           "<td>201</td><td>252.5</td><td>Under</td></tr>")
    with pytest.raises(AccountViewHold, match="PM_ACCOUNT_VIEW_DUPLICATE_GAME"):
        run(view(row + row))


def test_unsupported_row_values_are_not_inferred():
    invalid = ("<tr><th>Oct 25, 2026</th><td>vs Tampa Bay Buccaneers</td>"
               "<td>—</td><td>252.5</td><td>Over</td></tr>")
    with pytest.raises(AccountViewHold, match="PM_ACCOUNT_VIEW_ROW_INVALID"):
        run(view(invalid))


def test_missing_chart_does_not_invent_historical_games():
    with pytest.raises(AccountViewHold, match="PM_ACCOUNT_VIEW_HISTORY_TABLE_INVALID"):
        run("NFL player props Taylor Sample no historical chart")


def test_account_view_is_not_public_scraper_or_password_export():
    result = run(view() + "\nAUTH_COOKIE=should-not-be-exported\n")
    assert "AUTH_COOKIE" not in repr(result)
    assert result["source_page_without_credentials"] == URL
    assert result["account_authentication_independently_attested"] is False
    assert result["view_table_sha256"] and len(result["view_table_sha256"]) == 64



def test_future_upcoming_row_is_not_mistaken_for_a_historical_result():
    upcoming = ("<tr><th>Nov 1, 2026</th><td>at New York Jets</td>"
                "<td>—</td><td>252.5</td><td>Upcoming</td></tr>")
    r = run(view(upcoming))
    assert r["completed_game_rows"] == 0 and r["upcoming_rows"] == 1
    assert r["rows"][0]["historical_closing_line"] is None


def test_inconsistent_chart_side_is_rejected_even_though_not_historical_line():
    bad = ("<tr><th>Oct 25, 2026</th><td>vs Tampa Bay Buccaneers</td>"
           "<td>220</td><td>252.5</td><td>Over</td></tr>")
    with pytest.raises(AccountViewHold, match="PM_ACCOUNT_VIEW_ROW_INVALID"):
        run(view(bad))


def test_future_completed_result_is_rejected_as_time_leakage():
    bad = ("<tr><th>Nov 1, 2026</th><td>at New York Jets</td>"
           "<td>320</td><td>252.5</td><td>Over</td></tr>")
    with pytest.raises(AccountViewHold, match="PM_ACCOUNT_VIEW_ROW_INVALID"):
        run(view(bad))
