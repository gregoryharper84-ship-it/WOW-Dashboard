"""Fail-closed structured Discord research gate, without message ingestion.

For use only AFTER authorized external ingestion has transformed a single permitted
message into limited structured fields. Never handles raw text, attachments, tokens,
model fitting, betting execution or automatic channel collection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Mapping


SPORTS = frozenset({
    "NFL", "NBA", "NHL", "NCAAF", "NCAAB", "MLB", "WNBA",
    "SOCCER", "TENNIS", "GOLF", "MMA", "BOXING", "CRICKET",
})
MARKETS = frozenset({"MONEYLINE", "SPREAD", "TOTAL", "PLAYER_PROP"})
KINDS = frozenset({"PRE_GAME_PICK", "SHARP_ALERT", "POST_GAME_RECAP"})
RECAP_OUTCOMES = frozenset({"WIN", "LOSS", "PUSH", "VOID", "UNKNOWN"})
FIELDS = frozenset({
    "guild_id", "channel_id", "message_id", "claim_index",
    "source_created_at", "source_edited_at", "event_start_at",
    "sport", "event_id", "kind", "market", "selection",
    "line", "player", "stat", "book", "american_odds", "reported_outcome",
})
_ID = re.compile(r"^[0-9]{1,22}$")
_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_STAT = re.compile(r"^[a-z0-9_+/-]{1,40}$")


class SignalRejected(ValueError):
    """Typed rejection with no private content in messages."""


def _required_str(data: Mapping, key: str, limit: int = 100) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or len(value) > limit or value != value.strip():
        raise SignalRejected("DISCORD_STRUCTURED_FIELD_INVALID:" + key)
    return value


def _date(value: str, key: str) -> datetime:
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        raise SignalRejected("DISCORD_STRUCTURED_TIME_INVALID:" + key) from None


def _numeric_line(data: Mapping, *, required: bool) -> str | None:
    raw = data.get("line")
    if raw is None:
        if required:
            raise SignalRejected("DISCORD_STRUCTURED_LINE_REQUIRED")
        return None
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise SignalRejected("DISCORD_STRUCTURED_LINE_INVALID")
    try:
        val = Decimal(str(raw))
        if not val.is_finite() or abs(val) > 100000 or val.as_tuple().exponent < -3:
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise SignalRejected("DISCORD_STRUCTURED_LINE_INVALID") from None
    return str(val.normalize())


def accept_structured_claim(
    claim: Mapping,
    *,
    observed_at: datetime,
    allowed_channels: frozenset[tuple[str, str]],
    source_use_authorized: bool,
) -> dict:
    """Return minimal unverified research metadata, never a certified prediction.

    source_use_authorized and allowed_channels MUST originate in trusted server
    configuration, never the client or the Discord message. observed_at MUST be
    the server-side receipt timestamp, never a source-supplied timestamp.
    """
    if not source_use_authorized or not allowed_channels:
        raise SignalRejected("DISCORD_SOURCE_PERMISSION_REQUIRED")
    if not isinstance(claim, Mapping) or set(claim) - FIELDS:
        raise SignalRejected("DISCORD_STRUCTURED_UNEXPECTED_FIELDS")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise SignalRejected("DISCORD_SERVER_CLOCK_INVALID")
    received = observed_at.astimezone(timezone.utc)

    ids = {k: _required_str(claim, k, 22) for k in ("guild_id", "channel_id", "message_id")}
    if not all(_ID.fullmatch(v) for v in ids.values()):
        raise SignalRejected("DISCORD_STRUCTURED_ID_INVALID")
    if (ids["guild_id"], ids["channel_id"]) not in allowed_channels:
        raise SignalRejected("DISCORD_CHANNEL_NOT_ALLOWLISTED")
    index = claim.get("claim_index")
    if type(index) is not int or not 0 <= index <= 31:
        raise SignalRejected("DISCORD_CLAIM_INDEX_INVALID")

    sport = _required_str(claim, "sport", 20)
    market = _required_str(claim, "market", 20)
    kind = _required_str(claim, "kind", 25)
    if sport not in SPORTS or market not in MARKETS or kind not in KINDS:
        raise SignalRejected("DISCORD_STRUCTURED_ROUTE_UNSUPPORTED")
    event_id = _required_str(claim, "event_id", 120)
    if not _KEY.fullmatch(event_id):
        raise SignalRejected("DISCORD_EVENT_ID_INVALID")
    selection = _required_str(claim, "selection", 100)

    source_created = _date(claim.get("source_created_at"), "source_created_at")
    event_start = _date(claim.get("event_start_at"), "event_start_at")
    if source_created > received + timedelta(minutes=2):
        raise SignalRejected("DISCORD_SOURCE_TIME_IN_FUTURE")
    edited = None
    if claim.get("source_edited_at") is not None:
        edited = _date(claim["source_edited_at"], "source_edited_at")
        if edited < source_created or edited > received + timedelta(minutes=2):
            raise SignalRejected("DISCORD_SOURCE_REVISION_TIME_INVALID")
    if kind != "POST_GAME_RECAP" and (received >= event_start or source_created >= event_start):
        raise SignalRejected("DISCORD_POST_START_NOT_PROSPECTIVE")
    if kind == "POST_GAME_RECAP" and received < event_start:
        raise SignalRejected("DISCORD_RECAP_BEFORE_EVENT")

    line = _numeric_line(claim, required=market != "MONEYLINE")
    if market == "MONEYLINE" and line is not None:
        raise SignalRejected("DISCORD_MONEYLINE_HAS_LINE")
    player = claim.get("player")
    stat = claim.get("stat")
    if market == "PLAYER_PROP":
        player = _required_str(claim, "player", 100)
        stat = _required_str(claim, "stat", 40)
        if not _STAT.fullmatch(stat):
            raise SignalRejected("DISCORD_STAT_INVALID")
        if selection not in {"OVER", "UNDER"}:
            raise SignalRejected("DISCORD_PROP_DIRECTION_INVALID")
    elif player is not None or stat is not None:
        raise SignalRejected("DISCORD_NONPROP_PLAYER_FIELDS")
    if market == "TOTAL" and selection not in {"OVER", "UNDER"}:
        raise SignalRejected("DISCORD_TOTAL_DIRECTION_INVALID")

    odds = claim.get("american_odds")
    if odds is not None and (type(odds) is not int or odds == 0 or abs(odds) < 100 or abs(odds) > 10000):
        raise SignalRejected("DISCORD_ODDS_INVALID")
    book = claim.get("book")
    if book is not None:
        book = _required_str(claim, "book", 60)
    reported = claim.get("reported_outcome")
    if kind != "POST_GAME_RECAP" and reported is not None:
        raise SignalRejected("DISCORD_FUTURE_RESULT_INVALID")
    if kind == "POST_GAME_RECAP" and reported not in RECAP_OUTCOMES:
        raise SignalRejected("DISCORD_RECAP_OUTCOME_REQUIRED")

    record = {
        "source": "DISCORD_AUTHORIZED_STRUCTURED_RESEARCH",
        "guild_id": ids["guild_id"],
        "channel_id": ids["channel_id"],
        "message_id": ids["message_id"],
        "claim_index": index,
        "source_created_at": source_created.isoformat(),
        "source_edited_at": edited.isoformat() if edited else None,
        "captured_at": received.isoformat(),
        "event_start_at": event_start.isoformat(),
        "sport": sport,
        "event_id": event_id,
        "kind": kind,
        "market": market,
        "selection": selection,
        "line": line,
        "player": player,
        "stat": stat,
        "book": book,
        "american_odds": odds,
        "reported_outcome_unverified": reported,
        "verified_result": None,
        "research_status": "UNVERIFIED_RESEARCH_ONLY",
        "probability_authority": False,
        "certified_model": False,
        "can_execute": False,
    }
    stable = {k: v for k, v in record.items() if k != "captured_at"}
    record["claim_fingerprint"] = sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return record
