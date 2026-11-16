"""Fixture-only tests: no website login, API requests or persistent scraping."""
from datetime import datetime, timezone
import pytest

from v17.propsmadness_visible_page_research import VisiblePageHold, parse_user_visible_player_page

URL = "https://propsmadness.com/nba?market=player-points&player=101&match=303"
AT = datetime(2026, 11, 16, 20, 30, tzinfo=timezone.utc)
BODY = """<img src="101.webp" alt="Ada Example"/>
Ada Example
F
<img src="19.webp" alt="PrizePicks"/>
21.5
Pts
O
-137
U
-137
<button aria-label="Track a bet on Ada Example"></button>
<button>Hit Rate</button>
66.7% (8/12)
<button>Odds imply 57.8% Over</button>
Pts
24.4
Ast
8.3
Reb
6.8
3PM
1.4
Mins
36.8
Usage
29.4%
FGA
18.9
<button aria-label="Understanding stats"></button>
"""


def parsed(body=BODY, url=URL, player="Ada Example", at=AT):
    return parse_user_visible_player_page(body, url=url, expected_player=player, captured_at=at)


def test_user_visible_single_page_is_research_only_and_structured():
    d = parsed()
    assert d["schema"] == "WOW_PM_USER_VISIBLE_RESEARCH_V1"
    assert d["status"] == "ONE_VISIBLE_PLAYER_RESEARCH_ONLY"
    assert d["sport"] == "NBA" and d["stat_type"] == "POINTS"
    assert d["provider_player_alias"] == "101"
    assert d["provider_event_alias"] == "303"
    assert d["player_display_unverified"] == "Ada Example"
    assert d["display_line_unverified"] == 21.5
    assert d["book_display_unverified"] == "PrizePicks"
    assert d["over_odds_display_unverified"] == -137
    assert d["under_odds_display_unverified"] == -137
    assert d["hit_rate_display_pct"] == 66.7
    assert d["hit_count_display"] == 8 and d["games_count_display"] == 12
    assert d["implied_over_display_pct"] == 57.8
    assert d["stats_display_unverified"]["Mins"] == 36.8
    assert d["stats_display_unverified"]["Usage"] == 29.4
    assert d["captured_at"] == "2026-11-16T20:30:00Z"
    assert len(d["visible_page_sha256"]) == 64
    for field in ("canonical_player_id", "canonical_event_id", "event_start_utc",
                  "model_probability", "calibrated_lower_bound"):
        assert d[field] is None
    for field in ("pregame_verified", "market_quote_verified", "source_rights_verified",
                  "historical_training_authorized", "production_write_authorized",
                  "probability_publishable", "can_execute"):
        assert d[field] is False
    assert "raw_page" not in d and "html" not in d


@pytest.mark.parametrize("url", [
    "http://propsmadness.com/nba?market=player-points&player=101&match=303",
    "https://evil.test/nba?market=player-points&player=101&match=303",
    "https://propsmadness.com/nba?market=player-points&player=101&match=303&token=secret",
    "https://propsmadness.com/nba?market=player-points&player=101&player=102&match=303",
    "https://propsmadness.com/nba?market=player-points&player=../101&match=303",
    "https://propsmadness.com/nba?market=player-assists&player=101&match=303",
])
def test_untrusted_url_or_unsupported_market_holds(url):
    with pytest.raises(VisiblePageHold):
        parsed(url=url)


def test_player_mismatch_fails_not_guessing():
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_PLAYER_NOT_FOUND"):
        parsed(player="Other Player")


def test_page_duplicate_player_blocks_are_ambiguous():
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_PLAYER_AMBIGUOUS"):
        parsed(BODY + BODY)


def test_invalid_hit_count_does_not_become_a_model_probability():
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_HIT_RATE_INVALID"):
        parsed(BODY.replace("66.7% (8/12)", "96.7% (8/12)"))


def test_unreadable_or_broken_page_holds():
    with pytest.raises(VisiblePageHold):
        parsed("No player information visible")
    with pytest.raises(VisiblePageHold):
        parsed(BODY.replace("-137\nU", "not-a-price\nU"))
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_TEXT_TOO_LARGE"):
        parsed(BODY + "x" * 130_000)


def test_naive_capture_time_holds():
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_CAPTURE_TIME_INVALID"):
        parsed(at=datetime(2026, 11, 16, 14, 30))


def test_no_network_no_training_or_execution_boundaries_in_module():
    import ast
    import inspect
    import v17.propsmadness_visible_page_research as mod
    source = inspect.getsource(mod)
    tree = ast.parse(source)
    module_imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    module_imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(x and x.startswith(("requests", "urllib.request", "playwright", "selenium"))
                   for x in module_imports)
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in ("open", "eval", "exec") for n in ast.walk(tree))
    assert "can_execute" in source and "historical_training_authorized" in source


def test_malformed_query_string_gets_typed_hold():
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_URL_INVALID"):
        parsed(url=URL + "&broken")


def test_does_not_attribute_next_players_panel_to_expected_player():
    other_panel = BODY.replace("Ada Example", "Other Player")
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_LINE_OR_ODDS_MISSING"):
        parsed("Ada Example\n" + other_panel)


@pytest.mark.parametrize("bad_under", ["-100000", "-137.5", "-137X"])
def test_under_odds_must_be_complete_numeric_token(bad_under):
    with pytest.raises(VisiblePageHold, match="PM_VISIBLE_LINE_OR_ODDS_MISSING"):
        parsed(BODY.replace('\n-137\n<button aria-label="Track',
                            '\n' + bad_under + '\n<button aria-label="Track'))


def test_windows_crlf_page_text_extracts_same_visible_stats():
    result = parsed(BODY.replace("\n", "\r\n"))
    assert result["player_display_unverified"] == "Ada Example"
    assert result["display_line_unverified"] == 21.5
    assert result["stats_display_unverified"]["Usage"] == 29.4
