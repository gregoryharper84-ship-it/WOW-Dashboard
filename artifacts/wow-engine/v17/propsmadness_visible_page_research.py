"""Pure one-page PropsMadness UI research reader; no website requests.

Input is the one page a subscriber explicitly opened and shared. This is
NOT an automated collector, API, credential handler, database writer,
training source, market quote, or probability adapter. No vendor dataset is
persisted or copied here. Unsupported layouts fail closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import math
import re
from urllib.parse import parse_qs, urlsplit
from typing import Any

SCHEMA = "WOW_PM_USER_VISIBLE_RESEARCH_V1"
MAX_PAGE_CHARS = 100_000
MAX_PLAYER_BLOCK = 1700
# Start with one verified UI layout. Additional markets require fixture tests.
ROUTES = {("nba", "player-points"): ("NBA", "POINTS", "Pts")}
CAN_EXECUTE = False


class VisiblePageHold(ValueError):
    """Explicit, non-retryable failure to establish page research facts."""


def _validate_url(url: str) -> tuple[str, str, str, str]:
    if not isinstance(url, str) or len(url) > 500 or any(c.isspace() for c in url):
        raise VisiblePageHold("PM_VISIBLE_URL_INVALID")
    p = urlsplit(url)
    if (p.scheme != "https" or p.netloc != "propsmadness.com" or p.fragment
            or p.path not in ("/nba",)):
        raise VisiblePageHold("PM_VISIBLE_URL_INVALID")
    try:
        q = parse_qs(p.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise VisiblePageHold("PM_VISIBLE_URL_INVALID") from exc
    if set(q) != {"market", "player", "match"} or any(len(v) != 1 for v in q.values()):
        raise VisiblePageHold("PM_VISIBLE_URL_INVALID")
    route = (p.path.strip("/"), q["market"][0])
    if route not in ROUTES:
        raise VisiblePageHold("PM_VISIBLE_ROUTE_UNSUPPORTED")
    player, match = q["player"][0], q["match"][0]
    if not (re.fullmatch(r"[0-9]{1,12}", player)
            and re.fullmatch(r"[0-9]{1,12}", match)):
        raise VisiblePageHold("PM_VISIBLE_URL_INVALID")
    return (*ROUTES[route][:2], player, match)


def _valid_float(value: str, *, high: float, code: str) -> float:
    try:
        v = float(value)
    except (ValueError, TypeError) as exc:
        raise VisiblePageHold(code) from exc
    if not math.isfinite(v) or v < 0 or v > high:
        raise VisiblePageHold(code)
    return v


def parse_user_visible_player_page(
    page_text: str,
    *,
    url: str,
    expected_player: str,
    captured_at: datetime,
) -> dict[str, Any]:
    """Read one selected NBA points page. No fetch, login or file writes.

    Display-only values must be cross-checked independently before use in
    licensed models. This receipt is never a qualified pregame market quote.
    """
    sport, stat, player_alias, event_alias = _validate_url(url)
    if not isinstance(page_text, str) or len(page_text) > MAX_PAGE_CHARS:
        raise VisiblePageHold("PM_VISIBLE_TEXT_TOO_LARGE")
    if not isinstance(expected_player, str) or not 2 <= len(expected_player) <= 100 or any(
        x in expected_player for x in "\n\r<>"
    ):
        raise VisiblePageHold("PM_VISIBLE_PLAYER_INVALID")
    if not isinstance(captured_at, datetime) or captured_at.utcoffset() is None:
        raise VisiblePageHold("PM_VISIBLE_CAPTURE_TIME_INVALID")
    t = captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    headers = list(re.finditer(
        rf"(?m)^[ \t]*{re.escape(expected_player)}[ \t]*$", page_text,
    ))
    if not headers:
        raise VisiblePageHold("PM_VISIBLE_PLAYER_NOT_FOUND")
    if len(headers) != 1:
        raise VisiblePageHold("PM_VISIBLE_PLAYER_AMBIGUOUS")
    segment = page_text[headers[0].end():headers[0].end() + MAX_PLAYER_BLOCK]
    price = re.search(
        r'<img\b[^>]{0,500}\balt="(?P<book>[^"<>]{1,64})"[^>]*>'
        r'\s*(?P<line>\d{1,4}(?:\.\d{1,2})?)\s*Pts\s*O\s*'
        r'(?P<over>[+-]?\d{2,5})\s*U\s*(?P<under>[+-]?\d{2,5})',
        segment,
    )
    if price is None:
        raise VisiblePageHold("PM_VISIBLE_LINE_OR_ODDS_MISSING")
    line = _valid_float(price["line"], high=1000, code="PM_VISIBLE_LINE_INVALID")
    if line <= 0:
        raise VisiblePageHold("PM_VISIBLE_LINE_INVALID")
    odds = [int(price[k]) for k in ("over", "under")]
    if any(abs(o) < 100 or abs(o) > 10000 for o in odds):
        raise VisiblePageHold("PM_VISIBLE_ODDS_INVALID")
    hit = re.search(
        r'Hit Rate</button>\s*(\d{1,3}(?:\.\d+)?)%\s*\((\d{1,3})/(\d{1,3})\)',
        segment,
    )
    if hit is None:
        raise VisiblePageHold("PM_VISIBLE_HIT_RATE_MISSING")
    rate = _valid_float(hit[1], high=100, code="PM_VISIBLE_HIT_RATE_INVALID")
    overs, games = int(hit[2]), int(hit[3])
    if games <= 0 or overs > games or abs(rate - 100 * overs / games) > 0.15:
        raise VisiblePageHold("PM_VISIBLE_HIT_RATE_INVALID")
    implied = re.search(r'Odds imply\s*(\d{1,3}(?:\.\d+)?)% Over', segment)
    if implied is None:
        raise VisiblePageHold("PM_VISIBLE_IMPLIED_RATE_MISSING")
    implied_display = _valid_float(implied[1], high=100, code="PM_VISIBLE_IMPLIED_RATE_INVALID")

    # Keep a small display-only whitelist; raw page and account data never leave.
    stats: dict[str, float] = {}
    stat_text = segment[implied.end():]
    for label in ("Pts", "Ast", "Reb", "3PM", "Mins", "Usage", "FGA"):
        p = re.search(rf'(?m)^[ \t]*{re.escape(label)}[ \t]*$\s*'
                      r'(?P<value>\d{1,4}(?:\.\d{1,2})?)%?', stat_text)
        if p is not None:
            stats[label] = _valid_float(p["value"], high=10000, code="PM_VISIBLE_STAT_INVALID")
    return {
        "schema": SCHEMA,
        "status": "ONE_VISIBLE_PLAYER_RESEARCH_ONLY",
        "sport": sport,
        "stat_type": stat,
        "provider_player_alias": player_alias,
        "provider_event_alias": event_alias,
        "player_display_unverified": expected_player,
        "source_url": url,
        "visible_page_sha256": sha256(page_text.encode("utf-8")).hexdigest(),
        "captured_at": t,
        "book_display_unverified": price["book"],
        "display_line_unverified": line,
        "over_odds_display_unverified": odds[0],
        "under_odds_display_unverified": odds[1],
        "hit_rate_display_pct": rate,
        "hit_count_display": overs,
        "games_count_display": games,
        "implied_over_display_pct": implied_display,
        "stats_display_unverified": stats,
        "canonical_player_id": None,
        "canonical_event_id": None,
        "event_start_utc": None,
        "model_probability": None,
        "calibrated_lower_bound": None,
        "pregame_verified": False,
        "market_quote_verified": False,
        "source_rights_verified": False,
        "historical_training_authorized": False,
        "production_write_authorized": False,
        "probability_publishable": False,
        "can_execute": False,
    }
