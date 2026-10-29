"""WOW V17: NBA, MLB, WNBA *offline* account-view player outcome research.

Accepts a sanitized text/accessibility view supplied by a signed-in operator;
never logs in, calls hidden APIs, stores a source page, schedules a scrape,
writes to a model/DB, certifies sportsbook closing lines, or places orders.

This is a deliberately strict contract for exploratory compatibility. It must
be live-validated sport-by-sport before a layout is promoted to supported.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from html import unescape
from typing import Any
from urllib.parse import urlsplit, parse_qs
import re

from v17.propsmadness_nfl_account_view_adapter import (
    AccountViewHold, _require, _utc,
)

SCHEMA = "WOW_PM_MULTISPORT_ACCOUNT_VIEW_RESEARCH_V1"
# Labels and URL suffixes are PROVISIONAL until a live signed-in page per sport
# has been inspected; the extractor fails closed on layout/market disagreement.
SPORT_STATS = {
    "NBA": {
        "POINTS": (("points",), ("Pts", "Points")),
        "REBOUNDS": (("rebounds",), ("Reb", "Rebounds")),
        "ASSISTS": (("assists",), ("Ast", "Assists")),
        "THREE_POINTERS_MADE": (("made-threes", "threes"), ("3PM", "Threes")),
    },
    "WNBA": {
        "POINTS": (("points",), ("Pts", "Points")),
        "REBOUNDS": (("rebounds",), ("Reb", "Rebounds")),
        "ASSISTS": (("assists",), ("Ast", "Assists")),
        "THREE_POINTERS_MADE": (("made-threes", "threes"), ("3PM", "Threes")),
    },
    "MLB": {
        "PITCHER_STRIKEOUTS": (("strikeouts",), ("Strikeouts", "Ks")),
        "PITCHING_OUTS": (("pitcher-outs",), ("Pitcher Outs", "Outs")),
        "HITS": (("hits",), ("Hits",)),
        "TOTAL_BASES": (("total-bases",), ("Total Bases",)),
    },
}
MAX_TEXT_LENGTH = 250_000
MAX_ROWS = 100
TABLE_TITLE = "Game-by-game detail for the chart above"
TABLE_RE = re.compile(r"<table>" + re.escape(TABLE_TITLE) + r"(.*?)</table>", re.S | re.I)
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh](?:\s[^>]*)?>(.*?)</t[dh]>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>", re.S)


def _cell(s: str) -> str:
    return " ".join(unescape(TAG_RE.sub("", s)).split())


def _scope_url(source_url: str, sport: str, stat_type: str) -> tuple[str, str, str]:
    _require(isinstance(source_url, str), "PM_MULTISPORT_SOURCE_INVALID")
    p = urlsplit(source_url)
    _require(
        p.scheme == "https"
        and p.hostname in ("propsmadness.com", "www.propsmadness.com")
        and p.path.rstrip("/") == "/" + sport.lower()
        and p.username is None and p.password is None and not p.fragment,
        "PM_MULTISPORT_SOURCE_INVALID",
    )
    try:
        q = parse_qs(p.query, strict_parsing=True)
    except ValueError as exc:
        raise AccountViewHold("PM_MULTISPORT_SOURCE_INVALID") from exc
    _require(set(q) == {"player", "match", "market"} and
             all(len(x) == 1 for x in q.values()),
             "PM_MULTISPORT_SOURCE_INVALID")
    for v in (q["player"][0], q["match"][0]):
        _require(bool(re.fullmatch(r"[0-9]{1,12}", v)),
                 "PM_MULTISPORT_SOURCE_INVALID")
    allowed_suffixes, _ = SPORT_STATS[sport][stat_type]
    market = q["market"][0]
    _require(market == "featured" or market in {
        "player-" + suffix for suffix in allowed_suffixes
    }, "PM_MULTISPORT_MARKET_CONFLICT")
    url = (f"https://propsmadness.com/{sport.lower()}"
           f"?market={market}&player={q['player'][0]}&match={q['match'][0]}")
    return q["player"][0], q["match"][0], url


def normalize_account_history_other_sport(
    rendered_view_text: str, *, source_url: str, captured_at: str,
    sport: str, stat_type: str, selected_player_name: str,
    account_ui_confirmed_by_operator: bool,
) -> dict[str, Any]:
    """Normalize visible per-game sport results, never historical market quotes.

    No I/O and no authentication handling. NBA/MLB/WNBA views must each match
    this exact observed-table contract; otherwise return typed failure.
    """
    _require(sport in SPORT_STATS, "PM_MULTISPORT_SPORT_UNSUPPORTED")
    _require(stat_type in SPORT_STATS[sport], "PM_MULTISPORT_MARKET_CONFLICT")
    _require(account_ui_confirmed_by_operator is True,
             "PM_MULTISPORT_OPERATOR_CONFIRMATION_MISSING")
    _require(isinstance(rendered_view_text, str) and
             0 < len(rendered_view_text) <= MAX_TEXT_LENGTH,
             "PM_MULTISPORT_INPUT_INVALID")
    _require(isinstance(selected_player_name, str) and
             2 <= len(selected_player_name.strip()) <= 120,
             "PM_MULTISPORT_PLAYER_UNRESOLVED")
    capture = _utc(captured_at)
    provider_player, provider_match, url = _scope_url(source_url, sport, stat_type)

    # Limit the table to the selected sport's breakdown, not a foreign-sport
    # overlay or hidden second chart included in the accessibility dump.
    header = sport + " player props"
    start = rendered_view_text.find(header)
    _require(start >= 0, "PM_MULTISPORT_SECTION_MISSING")
    next_other = min(
        [i for other in ("NBA", "NFL", "MLB", "WNBA") if other != sport
         for i in [rendered_view_text.find(other + " player props", start + len(header))]
         if i >= 0] or [len(rendered_view_text)]
    )
    section = rendered_view_text[start:next_other]
    tables = list(TABLE_RE.finditer(section))
    _require(len(tables) == 1, "PM_MULTISPORT_HISTORY_TABLE_INVALID")
    before_table = section[:tables[0].start()]
    headers = list(re.finditer(
        r"(?:^|\n)\s*" + re.escape(selected_player_name.strip()) + r"\s*\n",
        before_table,
    ))
    _require(bool(headers), "PM_MULTISPORT_PLAYER_UNRESOLVED")
    after_player = before_table[headers[-1].end():]
    # This is an observation of the *selected* stat, not arbitrary navigation
    # text. One of the provisional labels must occur near player details.
    labels = SPORT_STATS[sport][stat_type][1]
    _require(any(re.search(r"(?<![A-Za-z])" + re.escape(label) + r"(?![A-Za-z])",
                           after_player[:320], flags=re.I)
                 for label in labels),
             "PM_MULTISPORT_SELECTED_MARKET_UNRESOLVED")
    raw_table = tables[0].group(1)
    cells = [[_cell(s) for s in CELL_RE.findall(row)]
             for row in ROW_RE.findall(raw_table)]
    _require(bool(cells) and [v.casefold() for v in cells[0]] ==
             ["date", "opponent", "result", "line", "over or under"],
             "PM_MULTISPORT_HISTORY_TABLE_INVALID")
    games = cells[1:]
    _require(0 < len(games) <= MAX_ROWS, "PM_MULTISPORT_HISTORY_TABLE_INVALID")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for game in games:
        _require(len(game) == 5, "PM_MULTISPORT_ROW_INVALID")
        day, opponent, result, line, side = game
        try:
            date = datetime.strptime(day, "%b %d, %Y").date()
        except ValueError as exc:
            raise AccountViewHold("PM_MULTISPORT_ROW_INVALID") from exc
        _require(
            bool(re.fullmatch(r"(vs|at)\s+\S.{1,90}", opponent))
            and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", line)),
            "PM_MULTISPORT_ROW_INVALID",
        )
        threshold = float(line)
        _require(0 < threshold < 2000, "PM_MULTISPORT_ROW_INVALID")
        key = date.isoformat(), opponent.casefold()
        _require(key not in seen, "PM_MULTISPORT_DUPLICATE_GAME")
        seen.add(key)
        upcoming = side.casefold() == "upcoming" and result in ("—", "-", "")
        if upcoming:
            actual, status = None, "UPCOMING_NOT_HISTORICAL"
        else:
            _require(date <= capture.date()
                     and side in ("Over", "Under", "Push")
                     and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", result)),
                     "PM_MULTISPORT_ROW_INVALID")
            actual = float(result)
            _require(0 <= actual < 2000, "PM_MULTISPORT_ROW_INVALID")
            expected = "Over" if actual > threshold else "Under" if actual < threshold else "Push"
            _require(side == expected, "PM_MULTISPORT_ROW_INVALID")
            status = "HISTORICAL_OUTCOME_REQUIRES_OFFICIAL_VERIFICATION"
        out.append({
            "game_date": date.isoformat(),
            "opponent_display": opponent,
            "observed_stat": actual,
            "chart_comparison_threshold": threshold,
            "chart_comparison_direction": side,
            "row_status": status,
            "historical_closing_line": None,
            "historical_book": None,
            "historical_odds": None,
            "historical_market_timestamp": None,
            "official_event_id": None,
            "official_player_id": None,
        })
    completed = sum(r["observed_stat"] is not None for r in out)
    return {
        "schema": SCHEMA,
        "status": "OFFLINE_ACCOUNT_VIEW_OUTCOME_RESEARCH_ONLY",
        "source_provider": "PROPSMADNESS",
        "source_page_without_credentials": url,
        "captured_at": capture.isoformat().replace("+00:00", "Z"),
        "sport": sport, "stat_type": stat_type, "period": "FULL_GAME",
        "provider_player_alias": provider_player,
        "provider_match_alias": provider_match,
        "player_display_unverified": selected_player_name.strip(),
        "view_table_sha256": sha256(raw_table.encode("utf-8")).hexdigest(),
        "row_count": len(out),
        "completed_game_rows": completed,
        "upcoming_rows": len(out) - completed,
        "rows": out,
        "source_rights_verified": False,
        "account_authentication_independently_attested": False,
        "historical_market_line_verified": False,
        "official_identity_verified": False,
        "historical_training_authorized": False,
        "production_storage_authorized": False,
        "model_consumption_receipts": [],
        "probability_publishable": False, "can_execute": False,
    }
