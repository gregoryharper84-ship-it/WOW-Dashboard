"""Opt-in, read-only discovery from an independently operated PrizePicks-board API.

This is NOT a PrizePicks or PropsMadness scraper, a fitted model, an official-ID
adapter, a licensed data grant, or a settlement/price authority. An operator must
separately establish an API account, permitted research use and provenance.
Every output is discovery-only until authoritative identity, exact current offer,
model inputs, fitted scoring, calibration, freshness and terminal review pass.

No persisted provider corpus, automatic wagers, retries, hidden requests, or
model-training feature promotion. The bounded fetcher never visits PrizePicks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROVIDER = "PRIZEPICKSAPI_INDEPENDENT"
BASE_URL = "https://prizepicksapi.com/api/v1/projections"
SUPPORTED_LEAGUES = {
    "NBA": "NBA", "NFL": "NFL", "NHL": "NHL", "NCAAF": "CFB",
    "WNBA": "WNBA", "SOCCER": "SOCCER", "TENNIS": "TENNIS",
    "NCAAB": "NCAAB", "MLB": "MLB",
}
SCHEMA = "WOW_V17_AUTHORIZED_PROP_DISCOVERY_V1"
CAN_EXECUTE = False
MAX_RESPONSE_BYTES = 2_000_000
MAX_AGE_SECONDS = 20 * 60
MAX_PAGES = 8


class PropFeedHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int,
                         msg: str, headers: Any, newurl: str) -> None:
        raise PropFeedHold("PROP_FEED_REDIRECT_DISALLOWED")


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise PropFeedHold("PROP_FEED_TIMESTAMP_UNRESOLVED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PropFeedHold("PROP_FEED_TIMESTAMP_UNRESOLVED") from exc
    if parsed.utcoffset() is None:
        raise PropFeedHold("PROP_FEED_TIMESTAMP_UNRESOLVED")
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> str:
    if isinstance(value, bool):
        raise PropFeedHold("PROP_FEED_EXACT_LINE_INVALID")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PropFeedHold("PROP_FEED_EXACT_LINE_INVALID") from exc
    if not number.is_finite() or number <= 0 or number > 100000:
        raise PropFeedHold("PROP_FEED_EXACT_LINE_INVALID")
    return format(number.normalize(), "f")


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _blocked_row(row: Any, reason: str) -> dict[str, Any]:
    return {
        "provider_projection_id": _text(row.get("id")) if isinstance(row, dict) else None,
        "disposition": "BLOCKED", "typed_blocker": reason,
        "can_execute": False,
    }


def normalize_projection(row: Any, *, league: str, now: datetime) -> list[dict[str, Any]]:
    """Return each actually available side, or one typed blocked record."""
    if not isinstance(row, dict):
        raise PropFeedHold("PROP_FEED_ROW_SCHEMA_INVALID")
    if row.get("is_demo") is not False:
        raise PropFeedHold("PROP_FEED_DEMO_OR_PROVENANCE_UNVERIFIED")
    if row.get("status") != "active":
        raise PropFeedHold("PROP_FEED_OFFER_NOT_ACTIVE")
    if _text(row.get("league")).upper() != league:
        raise PropFeedHold("PROP_FEED_LEAGUE_CONFLICT")
    identifier = _text(row.get("id"))
    player = row.get("player")
    market = row.get("market")
    event = row.get("event")
    if not identifier or not isinstance(player, dict) or not isinstance(market, dict) or not isinstance(event, dict):
        raise PropFeedHold("PROP_FEED_ROW_SCHEMA_INVALID")
    name = _text(player.get("name"))
    stat = _text(market.get("stat"))
    period = _text(market.get("period"))
    start = _utc(event.get("starts_at"))
    observed = _utc(row.get("observed_at"))
    if start <= now:
        raise PropFeedHold("EVENT_ALREADY_STARTED")
    if observed > now.replace(microsecond=now.microsecond) and (observed - now).total_seconds() > 120:
        raise PropFeedHold("PROP_FEED_CLOCK_SKEW")
    if (now - observed).total_seconds() > MAX_AGE_SECONDS:
        raise PropFeedHold("PROP_FEED_ROW_STALE")
    if not name or not stat or not period:
        raise PropFeedHold("PROP_FEED_ROW_IDENTITY_INCOMPLETE")
    line = _number(row.get("line"))
    selections = row.get("selections")
    if not isinstance(selections, list) or len(selections) > 8:
        raise PropFeedHold("PROP_FEED_DIRECTION_UNRESOLVED")
    results = []
    seen = set()
    for selection in selections:
        if not isinstance(selection, dict):
            raise PropFeedHold("PROP_FEED_DIRECTION_UNRESOLVED")
        raw_side = _text(selection.get("side")).lower()
        if raw_side not in ("more", "less") or raw_side in seen:
            raise PropFeedHold("PROP_FEED_DIRECTION_UNRESOLVED")
        seen.add(raw_side)
        if selection.get("available") is not True:
            continue
        offer_type = _text(selection.get("tier")).lower()
        if offer_type not in ("standard", "goblin", "demon", "unknown"):
            raise PropFeedHold("PROP_FEED_OFFER_TYPE_UNRESOLVED")
        # Provider event/player IDs are aliases, NEVER official league IDs.
        results.append({
            "source_provider": PROVIDER, "source_projection_id": identifier,
            "source_player_alias": str(player.get("id") or "") or None,
            "source_event_alias": str(event.get("id") or "") or None,
            "sport": "NCAAF" if league == "CFB" else league,
            "league": league, "player": name, "team_unverified": _text(player.get("team")) or None,
            "opponent_unverified": _text(event.get("opponent")) or None,
            "market_stat_display": stat, "period_display": period, "exact_display_line": line,
            "direction": raw_side.upper(), "offer_type": offer_type,
            "event_start_time": start.isoformat(), "source_observed_at": observed.isoformat(),
            "pricing_independently_verified": False,
            "exact_prizepicks_offer_verified": False,
            "official_player_id": None, "official_event_id": None,
            "official_identity_verified": False, "settlement_identity_verified": False,
            "research_status": "IDENTITY_AND_EXACT_OFFER_RECHECK_REQUIRED",
            "controlling_route": "WOW_PROP_LANE",
            "model_probability": None, "calibrated_lower_bound": None,
            "probability_publishable": False, "rank_eligible": False,
            "can_execute": False,
        })
    if not results:
        raise PropFeedHold("PROP_FEED_NO_AVAILABLE_DIRECTIONS")
    return results


def normalize_page(payload: Any, *, league: str, now: datetime) -> dict[str, Any]:
    """Validate source health and reconcile every received projection."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise PropFeedHold("PROP_FEED_PAGE_SCHEMA_INVALID")
    meta = payload.get("meta")
    if not isinstance(meta, dict) or meta.get("mode") != "live":
        raise PropFeedHold("PROP_FEED_SOURCE_MODE_UNVERIFIED")
    if meta.get("source_status") != "fresh":
        raise PropFeedHold("PROP_FEED_SOURCE_STALE_OR_UNHEALTHY")
    if len(payload["data"]) > 200:
        raise PropFeedHold("PROP_FEED_PAGE_SIZE_EXCEEDED")
    accepted, blocked = [], []
    for row in payload["data"]:
        try:
            accepted.extend(normalize_projection(row, league=league, now=now))
        except PropFeedHold as exc:
            blocked.append(_blocked_row(row, exc.code))
    cursor = meta.get("next_cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 200):
        raise PropFeedHold("PROP_FEED_CURSOR_INVALID")
    return {
        "source_rows": len(payload["data"]),
        "directional_candidates": accepted, "blocked_source_rows": blocked,
        "next_cursor": cursor, "can_execute": False,
    }


def _http_page(*, key: str, league: str, cursor: str | None) -> dict[str, Any]:
    params = {"league": league, "status": "active", "limit": "200"}
    if cursor:
        params["cursor"] = cursor
    req = Request(BASE_URL + "?" + urlencode(params), headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "User-Agent": "WOW-V17-Authorized-Research/1.0",
    })
    try:
        with build_opener(_NoRedirect()).open(req, timeout=8) as response:
            if response.status != 200:
                raise PropFeedHold("PROP_FEED_HTTP_UNAVAILABLE")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except PropFeedHold:
        raise
    except HTTPError as exc:
        code = "PROP_FEED_AUTH_OR_ENTITLEMENT_BLOCKED" if exc.code in (401, 403) else (
            "PROP_FEED_QUOTA_BLOCKED" if exc.code == 429 else "PROP_FEED_HTTP_UNAVAILABLE"
        )
        raise PropFeedHold(code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PropFeedHold("PROP_FEED_TRANSPORT_FAILED") from exc
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise PropFeedHold("PROP_FEED_PAYLOAD_SIZE_INVALID")
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PropFeedHold("PROP_FEED_JSON_INVALID") from exc


def discover_authorized_prop_board(
    *, sport: str, max_pages: int = 4, now: datetime | None = None,
    authorized: bool | None = None, api_key: str | None = None,
    fetch_page: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read only a bounded, explicitly authorized third-party board.

    Neither the opt-in flag nor an API key establishes PrizePicks/PropsMadness
    rights or official line provenance. No scoring, DB write or scheduling here.
    """
    sport = _text(sport).upper()
    if sport not in SUPPORTED_LEAGUES:
        raise PropFeedHold("PROP_FEED_SPORT_NOT_SUPPORTED")
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= MAX_PAGES:
        raise PropFeedHold("PROP_FEED_PAGE_BUDGET_INVALID")
    if authorized is None:
        authorized = os.getenv("WOW_V17_PROP_FEED_ACCOUNT_USE_AUTHORIZED") == "1"
    if not authorized:
        raise PropFeedHold("PROP_FEED_ACCOUNT_USE_NOT_AUTHORIZED")
    key = api_key if api_key is not None else os.getenv("WOW_V17_PROP_FEED_API_KEY", "")
    if not key or not key.strip():
        raise PropFeedHold("PROP_FEED_CREDENTIAL_MISSING")
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise PropFeedHold("PROP_FEED_NOW_UNAWARE")
    current = current.astimezone(timezone.utc)
    league = SUPPORTED_LEAGUES[sport]
    get_page = fetch_page or _http_page
    candidates, blocked, cursors = [], [], set()
    pages = 0
    cursor = None
    source_rows = 0
    previous_ids = set()
    blockers = []
    while pages < max_pages:
        try:
            payload = get_page(key=key, league=league, cursor=cursor)
            page = normalize_page(payload, league=league, now=current)
        except PropFeedHold as exc:
            blockers.append(exc.code)
            break
        except Exception:
            blockers.append("PROP_FEED_PROVIDER_CALL_FAILED")
            break
        pages += 1
        source_rows += page["source_rows"]
        # Duplicate projection IDs across pages indicate unstable pagination:
        # explicitly block, never pretend to have complete board coverage.
        ids = {
            r.get("id") for r in payload["data"] if isinstance(r, dict) and r.get("id")
        }
        if previous_ids.intersection(ids):
            blockers.append("PROP_FEED_PAGINATION_DUPLICATE")
            break
        previous_ids.update(ids)
        candidates.extend(page["directional_candidates"])
        blocked.extend(page["blocked_source_rows"])
        next_cursor = page["next_cursor"]
        if next_cursor is None:
            cursor = None
            break
        if next_cursor in cursors:
            blockers.append("PROP_FEED_CURSOR_CYCLE")
            cursor = next_cursor
            break
        cursors.add(next_cursor)
        cursor = next_cursor
    if cursor is not None and not blockers:
        blockers.append("PROP_FEED_PAGE_BUDGET_EXHAUSTED")
    # Source rows must be individually accounted for: sides expand afterward.
    # A duplicate page is a coverage failure, not a successful terminal scan.
    page_reconciled = source_rows == len(blocked) + len({
        row["source_projection_id"] for row in candidates
    })
    if not page_reconciled and not blockers:
        blockers.append("PROP_FEED_RECONCILIATION_FAILED")
    complete = not blockers and cursor is None and page_reconciled
    return {
        "schema": SCHEMA, "source_provider": PROVIDER, "sport": sport,
        "source_pages_read": pages, "source_rows_received": source_rows,
        "unique_base_props": len({r["source_projection_id"] for r in candidates}),
        "directional_candidates_count": len(candidates),
        "source_rows_blocked": len(blocked),
        "directional_candidates": candidates,
        "blocked_rows": blocked, "blockers": blockers,
        "source_coverage_complete_for_requested_sport": complete,
        "status": "RESEARCH_DISCOVERY_ONLY" if complete else "INCOMPLETE_OR_BLOCKED",
        "automated_score_attempts": 0, "governed_predictions": 0,
        "source_license_independently_verified": False,
        "official_identity_verified": False,
        "exact_prizepicks_offer_verified": False,
        "probability_publishable": False, "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE", "PROVIDER", "PropFeedHold",
    "normalize_projection", "normalize_page", "discover_authorized_prop_board",
]
