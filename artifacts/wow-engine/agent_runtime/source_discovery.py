"""Bounded, evidence-only V17 daily-snapshot intake for Sports Agent Runtime.

This adapts current server-owned daily event identities; it does not supply
sporting probabilities, market lines, settlement certification or model inputs.
Only the existing agent registry/model/reducer may progress a candidate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

MAX_DISCOVERY_ROWS = 256
MAX_CANDIDATES = 32
FRESHNESS = timedelta(hours=8)
FUTURE_HORIZON = timedelta(hours=48)
# FULL_GAME is not an interchangeable identity for soccer 1X2, tennis,
# combat-sport or field-event markets. Leave those for a certified adapter.
FULL_GAME_SPORTS = frozenset({"MLB", "NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "NHL"})


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None


def select_current_daily_candidates(
    source_rows: list[dict[str, Any]], *, now: datetime, limit: int = MAX_CANDIDATES,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize independently verified pregame identities, with skip audit.

    Source rows are supporting discovery evidence only. A pre-existing
    HELD/REJECTED model result never promotes or blocks the fresh candidate:
    the controlling model must run again and issue its own typed verdict.
    """
    if now.tzinfo is None:
        raise ValueError("DISCOVERY_CLOCK_NOT_AWARE")
    now = now.astimezone(timezone.utc)
    if not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("DISCOVERY_LIMIT_INVALID")
    if not isinstance(source_rows, list) or len(source_rows) > MAX_DISCOVERY_ROWS:
        raise ValueError("DISCOVERY_SOURCE_BATCH_INVALID")
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    skipped: dict[str, int] = {}
    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for row in source_rows:
        if not isinstance(row, dict) or row.get("lane") != "MONEYLINE":
            skip("NON_MONEYLINE_SOURCE")
            continue
        captured = _aware(row.get("captured_at"))
        if captured is None or not now - FRESHNESS <= captured <= now + timedelta(minutes=5):
            skip("STALE_SOURCE")
            continue
        identity = row.get("identity")
        detail = row.get("detail")
        result = detail.get("result") if isinstance(detail, dict) else None
        if not isinstance(identity, dict) or not isinstance(result, dict):
            skip("SOURCE_IDENTITY_UNRESOLVED")
            continue
        if result.get("event_status") != "PREGAME":
            skip("EVENT_STATUS_NOT_PREGAME")
            continue
        sport = str(identity.get("sport") or "").upper()
        if sport not in FULL_GAME_SPORTS:
            skip("SETTLEMENT_ADAPTER_NOT_VERIFIED")
            continue
        event_id = identity.get("official_event_id")
        home, away = identity.get("home_team"), identity.get("away_team")
        start = _aware(identity.get("commence_time_utc"))
        if not all(isinstance(x, str) and x.strip() for x in (event_id, home, away)):
            skip("SOURCE_IDENTITY_UNRESOLVED")
            continue
        if start is None or not now + timedelta(minutes=2) < start <= now + FUTURE_HORIZON:
            skip("NOT_CURRENT_PREGAME")
            continue
        key = (sport, event_id)
        if key in seen:
            skip("DUPLICATE_SOURCE_INSTANCE")
            continue
        seen.add(key)
        if len(selected) == limit:
            skip("DISCOVERY_CANDIDATE_LIMIT")
            continue
        selected.append({
            "sport": sport,
            "league": str(result.get("league") or ""),
            "official_event_id": event_id,
            "participant": home,
            "opponent": away,
            "market_family": "OUTRIGHT_WINNER",
            "period": "FULL_GAME",
            "event_start_utc": start.isoformat(),
            # The last snapshot was pregame; current status and settlement
            # still require fresh specialist verification. Never smuggle a
            # cached status into the V17 final-refresh/publication gate.
            "source_event_status_at_capture": "PREGAME",
            "event_status": "CURRENT_STATUS_UNVERIFIED",
            "settlement_identity_verified": False,
            "discovery_source": "WOW_V17_DAILY_SNAPSHOT",
            "discovery_authority": "RESEARCH_INTEREST",
            "sources": [{
                "source_family": "WOW_V17_DAILY_SNAPSHOT",
                "name": "wow_v17_daily_run_row_detail",
                "captured_at": captured.isoformat(),
                "run_id": str(row.get("run_id") or ""),
            }],
            "can_execute": False,
        })
    return selected, skipped


def discover_from_daily_ledger(client: Any, *, now: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read the existing service-role ledger. No fake candidates, no new DB."""
    if now.tzinfo is None:
        raise ValueError("DISCOVERY_CLOCK_NOT_AWARE")
    cutoff = (now.astimezone(timezone.utc) - FRESHNESS).isoformat()
    response = (
        client.table("wow_v17_daily_run_row_detail")
        .select("run_id,row_index,lane,row_status,identity,detail,captured_at")
        .eq("lane", "MONEYLINE")
        .gte("captured_at", cutoff)
        .order("captured_at", desc=True)
        .limit(MAX_DISCOVERY_ROWS)
        .execute()
    )
    rows = getattr(response, "data", None)
    if not isinstance(rows, list):
        raise ValueError("DISCOVERY_PROVIDER_RESPONSE_INVALID")
    return select_current_daily_candidates(rows, now=now)
