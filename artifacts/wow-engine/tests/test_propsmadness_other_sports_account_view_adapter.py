"""Synthetic contract tests, no live vendor request or account session."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.propsmadness_other_sports_account_view_adapter import (
    SPORT_STATS, SCHEMA, normalize_account_history_other_sport, AccountViewHold
)

CAPTURE = "2026-10-29T15:00:00Z"


def page(sport="NBA", player="Sample Player", label="Pts",
         rows=None, *, after="<div>Some extra stats</div>"):
    rows = rows if rows is not None else (
        "<tr><th>Oct 20, 2026</th><td>vs New York Knicks</td>"
        "<td>29</td><td>22.5</td><td>Over</td></tr>"
        "<tr><th>Oct 23, 2026</th><td>at Chicago Bulls</td>"
        "<td>18</td><td>22.5</td><td>Under</td></tr>"
        "<tr><th>Nov 1, 2026</th><td>vs Atlanta Hawks</td>"
        "<td>—</td><td>22.5</td><td>Upcoming</td></tr>"
    )
    other = next(s for s in ("NBA", "NFL", "MLB", "WNBA") if s != sport)
    return (
        "Navigation: NBA NFL MLB WNBA\n"
        + sport + " player props\n"
        + player + "\n" + label + "\n25.5\n"
        + '<button>Hit Rate</button>\n'
        + '<table>Game-by-game detail for the chart above'
        + '<tr><th>Date</th><th>Opponent</th><th>Result</th>'
        + '<th>Line</th><th>Over or under</th></tr>'
        + rows + "</table>\n" + after
        + "\n" + other + " player props\n"
        + '<table>Game-by-game detail for the chart above'
        + '<tr><th>Date</th></tr></table>'
    )


def source(sport="NBA", market="featured"):
    return f"https://propsmadness.com/{sport.lower()}?market={market}&player=219&match=171"


def normalized(sport="NBA", stat_type="POINTS", label="Pts", **kwargs):
    args = dict(
        rendered_view_text=page(sport=sport, label=label),
        source_url=source(sport),
        captured_at=CAPTURE,
        sport=sport, stat_type=stat_type,
        selected_player_name="Sample Player",
        account_ui_confirmed_by_operator=True,
    )
    args.update(kwargs)
    return normalize_account_history_other_sport(**args)


CASES = [
    ("NBA", "POINTS", "Pts", "points"),
    ("NBA", "REBOUNDS", "Reb", "rebounds"),
    ("NBA", "ASSISTS", "Ast", "assists"),
    ("NBA", "THREE_POINTERS_MADE", "3PM", "made-threes"),
    ("WNBA", "POINTS", "Pts", "points"),
    ("WNBA", "REBOUNDS", "Reb", "rebounds"),
    ("WNBA", "ASSISTS", "Ast", "assists"),
    ("WNBA", "THREE_POINTERS_MADE", "3PM", "made-threes"),
    ("MLB", "PITCHER_STRIKEOUTS", "Strikeouts", "strikeouts"),
    ("MLB", "PITCHING_OUTS", "Pitcher Outs", "pitcher-outs"),
    ("MLB", "HITS", "Hits", "hits"),
    ("MLB", "TOTAL_BASES", "Total Bases", "total-bases"),
]


@pytest.mark.parametrize("sport,stat,label,market_slug", CASES)
def test_every_sport_market_fixture_is_non_authoritative(sport, stat, label, market_slug):
    out = normalized(sport, stat, label,
                     source_url=source(sport, market="player-"+market_slug))
    assert out["schema"] == SCHEMA
    assert out["sport"] == sport and out["stat_type"] == stat
    assert out["row_count"] == 3
    assert (out["completed_game_rows"], out["upcoming_rows"]) == (2, 1)
    assert [r["observed_stat"] for r in out["rows"]] == [29, 18, None]
    assert all(r["chart_comparison_threshold"] == 22.5 for r in out["rows"])
    assert all(r["historical_closing_line"] is None for r in out["rows"])
    assert all(r["historical_market_timestamp"] is None for r in out["rows"])
    assert all(r["official_event_id"] is None for r in out["rows"])
    for field in ("source_rights_verified", "historical_market_line_verified",
                  "official_identity_verified", "historical_training_authorized",
                  "production_storage_authorized", "probability_publishable",
                  "can_execute"):
        assert out[field] is False
    assert out["model_consumption_receipts"] == []


def test_exactly_three_other_sports_and_four_stats_each():
    assert set(SPORT_STATS) == {"NBA", "MLB", "WNBA"}
    assert all(len(stats) == 4 for stats in SPORT_STATS.values())


@pytest.mark.parametrize("kwargs,code", [
    ({"sport": "NFL"}, "PM_MULTISPORT_SPORT_UNSUPPORTED"),
    ({"stat_type": "FANTASY_SCORE"}, "PM_MULTISPORT_MARKET_CONFLICT"),
    ({"account_ui_confirmed_by_operator": False}, "PM_MULTISPORT_OPERATOR_CONFIRMATION_MISSING"),
    ({"rendered_view_text": ""}, "PM_MULTISPORT_INPUT_INVALID"),
    ({"selected_player_name": "Wrong Player"}, "PM_MULTISPORT_PLAYER_UNRESOLVED"),
    ({"captured_at": "2026-10-29T15:00:00"}, "PM_ACCOUNT_VIEW_TIMESTAMP_INVALID"),
    ({"source_url": "https://other-site.test/nba?market=featured&player=219&match=171"}, "PM_MULTISPORT_SOURCE_INVALID"),
    ({"source_url": source() + "&session=123"}, "PM_MULTISPORT_SOURCE_INVALID"),
    ({"source_url": "https://propsmadness.com/nba?market=featured&player=219&player=222&match=171"}, "PM_MULTISPORT_SOURCE_INVALID"),
    ({"source_url": source("NBA", "player-rebounds")}, "PM_MULTISPORT_MARKET_CONFLICT"),
])
def test_identity_time_market_and_source_fail_closed(kwargs, code):
    with pytest.raises(AccountViewHold, match=code):
        normalized(**kwargs)


def test_no_cross_sport_history_reuse():
    with pytest.raises(AccountViewHold, match="PM_MULTISPORT_SECTION_MISSING"):
        normalized(rendered_view_text=page("WNBA"))


def test_no_silent_missing_rows():
    with pytest.raises(AccountViewHold, match="PM_MULTISPORT_HISTORY_TABLE_INVALID"):
        normalized(rendered_view_text="NBA player props\nSample Player\nPts\nNo game history")


def test_duplicate_game_hold():
    row = ("<tr><th>Oct 23, 2026</th><td>at Chicago Bulls</td>"
           "<td>18</td><td>22.5</td><td>Under</td></tr>")
    with pytest.raises(AccountViewHold, match="PM_MULTISPORT_DUPLICATE_GAME"):
        normalized(rendered_view_text=page(rows=row+row))


def test_side_mismatch_and_future_result_hold():
    bad = ("<tr><th>Oct 23, 2026</th><td>at Chicago Bulls</td>"
           "<td>29</td><td>22.5</td><td>Under</td></tr>")
    with pytest.raises(AccountViewHold, match="PM_MULTISPORT_ROW_INVALID"):
        normalized(rendered_view_text=page(rows=bad))
    bad_future = ("<tr><th>Nov 1, 2026</th><td>at Chicago Bulls</td>"
                  "<td>29</td><td>22.5</td><td>Over</td></tr>")
    with pytest.raises(AccountViewHold, match="PM_MULTISPORT_ROW_INVALID"):
        normalized(rendered_view_text=page(rows=bad_future))


def test_market_selected_in_player_block_not_navigation():
    with pytest.raises(AccountViewHold, match="PM_MULTISPORT_SELECTED_MARKET_UNRESOLVED"):
        normalized(stat_type="ASSISTS", label="Pts")


def test_unrelated_overlay_not_parsed_or_saving_credentials():
    res = normalized(rendered_view_text=page() + "\nCOOKIE=do_not_store")
    assert res["row_count"] == 3
    assert "COOKIE" not in repr(res)
    assert len(res["view_table_sha256"]) == 64
    assert res["account_authentication_independently_attested"] is False


def test_completed_chart_result_not_historical_book_market():
    res = normalized()
    assert res["rows"][0]["row_status"] == "HISTORICAL_OUTCOME_REQUIRES_OFFICIAL_VERIFICATION"
    assert all(r["historical_book"] is None and r["historical_odds"] is None
               for r in res["rows"])
