"""Offline NFL player-history adapter for a user-supplied authenticated browser view.

NO login automation, cookies, private endpoints, raw DOM storage, scheduler, training,
production feature writes, or betting execution. The account owner must provide only
a sanitized rendered-view TEXT excerpt in an independently permitted workflow.

A chart's displayed 'Line' can be today's comparison threshold applied to old games.
It is NEVER a verified historical market/closing line.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from typing import Any
from urllib.parse import urlsplit, parse_qs
import re

SCHEMA = "WOW_PM_NFL_ACCOUNT_VIEW_RESEARCH_V1"
ALLOWED_STATS = {
    "PASSING_YARDS": "passing-yards",
    "RECEIVING_YARDS": "receiving-yards",
    "RUSHING_YARDS": "rushing-yards",
    "RECEPTIONS": "receptions",
}
MAX_VIEW_CHARS = 250_000
MAX_GAME_ROWS = 40
TABLE_TITLE = "Game-by-game detail for the chart above"
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh](?:\s[^>]*)?>(.*?)</t[dh]>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>", re.S)


class AccountViewHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(value: bool, code: str) -> None:
    if not value:
        raise AccountViewHold(code)


def _clean(value: str) -> str:
    return " ".join(unescape(TAG_RE.sub("", value)).split())


def _utc(value: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AccountViewHold("PM_ACCOUNT_VIEW_TIMESTAMP_INVALID") from exc
    _require(stamp.utcoffset() is not None, "PM_ACCOUNT_VIEW_TIMESTAMP_INVALID")
    return stamp.astimezone(timezone.utc)


def _url_ids(url: str, stat_type: str) -> tuple[str, str, str]:
    _require(isinstance(url, str), "PM_ACCOUNT_VIEW_SOURCE_INVALID")
    parsed = urlsplit(url)
    _require(
        parsed.scheme == "https"
        and parsed.hostname in ("propsmadness.com", "www.propsmadness.com")
        and parsed.username is None and parsed.password is None
        and parsed.path.rstrip("/") == "/nfl"
        and parsed.fragment == "",
        "PM_ACCOUNT_VIEW_SOURCE_INVALID",
    )
    params = parse_qs(parsed.query, strict_parsing=True)
    _require(set(params) == {"player", "match", "market"},
             "PM_ACCOUNT_VIEW_SOURCE_INVALID")
    ids = (params["player"], params["match"], params["market"])
    _require(all(len(x) == 1 for x in ids)
             and all(re.fullmatch(r"[0-9]{1,12}", x[0]) for x in ids[:2]),
             "PM_ACCOUNT_VIEW_SOURCE_INVALID")
    market = ids[2][0]
    _require(market == "featured" or market == "player-" + ALLOWED_STATS[stat_type],
             "PM_ACCOUNT_VIEW_MARKET_CONFLICT")
    safe_url = f"https://propsmadness.com/nfl?market={market}&player={ids[0][0]}&match={ids[1][0]}"
    return ids[0][0], ids[1][0], safe_url


def normalize_nfl_history_view(
    rendered_view_text: str, *, source_url: str, captured_at: str,
    selected_player_name: str, stat_type: str,
    account_ui_confirmed_by_operator: bool,
) -> dict[str, Any]:
    """Extract dated outcomes only from the visible selected-player history table.

    Input is a *caller-supplied* browser accessibility/text view, not a credential,
    authenticated HTTP session, or source-rights receipt. No external I/O occurs.
    """
    _require(stat_type in ALLOWED_STATS, "PM_ACCOUNT_VIEW_MARKET_CONFLICT")
    _require(account_ui_confirmed_by_operator is True,
             "PM_ACCOUNT_VIEW_OPERATOR_CONFIRMATION_MISSING")
    _require(isinstance(rendered_view_text, str) and
             0 < len(rendered_view_text) <= MAX_VIEW_CHARS,
             "PM_ACCOUNT_VIEW_INPUT_INVALID")
    _require(isinstance(selected_player_name, str) and
             2 <= len(selected_player_name.strip()) <= 120,
             "PM_ACCOUNT_VIEW_IDENTITY_UNRESOLVED")
    capture = _utc(captured_at)
    provider_player, provider_match, safe_url = _url_ids(source_url, stat_type)

    # The accessibility view can include other sports' navigation or overlays.
    # Bound extraction to the selected NFL breakdown, never an NBA overlay.
    start = rendered_view_text.find("NFL player props")
    _require(start >= 0, "PM_ACCOUNT_VIEW_SECTION_MISSING")
    end = rendered_view_text.find("NBA player props", start)
    section = rendered_view_text[start:end if end >= 0 else None]
    _require(selected_player_name.strip() in section,
             "PM_ACCOUNT_VIEW_IDENTITY_UNRESOLVED")
    tables = re.findall(
        re.escape("<table>" + TABLE_TITLE) + r"(.*?)</table>",
        section, flags=re.S | re.I,
    )
    _require(len(tables) == 1, "PM_ACCOUNT_VIEW_HISTORY_TABLE_INVALID")
    table = tables[0]
    table_rows = [
        [_clean(cell) for cell in CELL_RE.findall(row)]
        for row in ROW_RE.findall(table)
    ]
    _require(
        bool(table_rows)
        and [x.lower() for x in table_rows[0]]
        == ["date", "opponent", "result", "line", "over or under"],
        "PM_ACCOUNT_VIEW_HISTORY_TABLE_INVALID",
    )
    raw_rows = table_rows[1:]
    _require(0 < len(raw_rows) <= MAX_GAME_ROWS,
             "PM_ACCOUNT_VIEW_HISTORY_TABLE_INVALID")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_rows:
        _require(len(raw) == 5, "PM_ACCOUNT_VIEW_ROW_INVALID")
        date_text, opponent, result_text, comparison_text, side_text = raw
        try:
            game_date = datetime.strptime(date_text, "%b %d, %Y").date()
        except ValueError as exc:
            raise AccountViewHold("PM_ACCOUNT_VIEW_ROW_INVALID") from exc
        _require(
            bool(re.fullmatch(r"(vs|at)\s+\S.{1,90}", opponent))
            and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", comparison_text)),
            "PM_ACCOUNT_VIEW_ROW_INVALID",
        )
        comparison_line = float(comparison_text)
        _require(0 < comparison_line < 2000, "PM_ACCOUNT_VIEW_ROW_INVALID")
        key = (game_date.isoformat(), opponent.casefold())
        _require(key not in seen, "PM_ACCOUNT_VIEW_DUPLICATE_GAME")
        seen.add(key)
        upcoming = side_text.lower() == "upcoming" and result_text in {"—", "-", ""}
        if upcoming:
            actual = None
            disposition = "UPCOMING_NOT_HISTORICAL"
        else:
            _require(game_date <= capture.date(), "PM_ACCOUNT_VIEW_ROW_INVALID")
            _require(side_text in {"Over", "Under", "Push"}
                     and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", result_text)),
                     "PM_ACCOUNT_VIEW_ROW_INVALID")
            actual = float(result_text)
            _require(0 <= actual < 2000, "PM_ACCOUNT_VIEW_ROW_INVALID")
            disposition = "HISTORICAL_OUTCOME_REQUIRES_OFFICIAL_VERIFICATION"
        rows.append({
            "game_date": game_date.isoformat(),
            "opponent_display": opponent,
            "observed_stat": actual,
            "chart_comparison_threshold": comparison_line,
            "chart_comparison_direction": side_text,
            "row_status": disposition,
            # Never infer historical book, line, price, time, or official game ID.
            "historical_closing_line": None,
            "historical_book": None,
            "historical_odds": None,
            "historical_market_timestamp": None,
            "official_event_id": None,
        })
    completed = sum(r["observed_stat"] is not None for r in rows)
    return {
        "schema": SCHEMA,
        "status": "OFFLINE_ACCOUNT_VIEW_OUTCOME_RESEARCH_ONLY",
        "source_provider": "PROPSMADNESS",
        "source_page_without_credentials": safe_url,
        "captured_at": capture.isoformat().replace("+00:00", "Z"),
        "provider_player_alias": provider_player,
        "provider_match_alias": provider_match,
        "player_display_unverified": selected_player_name.strip(),
        "sport": "NFL", "stat_type": stat_type, "period": "FULL_GAME",
        "view_table_sha256": sha256(table.encode("utf-8")).hexdigest(),
        "row_count": len(rows),
        "completed_game_rows": completed,
        "upcoming_rows": len(rows) - completed,
        "rows": rows,
        "account_authentication_independently_attested": False,
        "source_rights_verified": False,
        "historical_market_line_verified": False,
        "historical_training_authorized": False,
        "official_identity_verified": False,
        "production_storage_authorized": False,
        "model_consumption_receipts": [],
        "probability_publishable": False,
        "can_execute": False,
    }
