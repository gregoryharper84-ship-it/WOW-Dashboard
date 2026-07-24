"""
gate_engine/event_identity.py
Stage 2 — Item 1: Canonical event_key builder

Produces a deterministic event_key from:
  league + official_event_id + scheduled_start_utc + participants + settlement_market

Also performs:
  - Slate-date matching against a target date
  - Duplicate detection (same event submitted twice)
  - Postponed / completed / in-progress detection

IMPORTANT: can_execute is always False.
  This module never produces or influences live orders.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

# ── Safety constants ──────────────────────────────────────────────────────────
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
CAN_EXECUTE    = False

# ── Event status values ───────────────────────────────────────────────────────
STATUS_SCHEDULED  = "SCHEDULED"
STATUS_POSTPONED  = "POSTPONED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED  = "COMPLETED"
STATUS_CANCELLED  = "CANCELLED"
STATUS_UNKNOWN    = "UNKNOWN"

# ESPN-style status_state strings
_IN_PROGRESS_STATES = frozenset({"in", "in_progress", "live"})
_COMPLETED_STATES   = frozenset({"post", "final", "completed", "ft"})
_POSTPONED_WORDS    = frozenset({"postponed", "ppd", "suspended"})
_CANCELLED_WORDS    = frozenset({"cancelled", "canceled"})


def _norm(v: Any) -> str:
    """
    Normalise a participant or field value for canonical event_key hashing.

    Steps (Item 3 — hardening):
      1. Coerce to string, lowercase, strip outer whitespace.
      2. Remove periods/dots (St. Louis → st louis).
      3. Strip all remaining punctuation and non-alphanumeric characters
         except spaces (handles hyphens, apostrophes, commas, etc.).
      4. Collapse consecutive whitespace to a single space.

    This ensures "St. Louis Blues", "St Louis Blues", and "st louis blues"
    all hash identically, preventing spurious key mismatches from minor
    formatting differences in team/player names.
    """
    s = (str(v) if v is not None else "").lower().strip()
    s = s.replace(".", " ")                     # St. → St
    s = re.sub(r"[^a-z0-9 ]", "", s)           # drop all punctuation
    s = re.sub(r"\s+", " ", s).strip()         # collapse whitespace
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Core key builder
# ─────────────────────────────────────────────────────────────────────────────

def build_event_key(
    league:               Optional[str],
    official_event_id:    Optional[str],
    scheduled_start_utc:  Optional[str],
    participants:         Optional[list[str]],
    settlement_market:    Optional[str],
) -> str:
    """
    Build a deterministic canonical event_key (SHA-256 hex[:16]).

    Participants are sorted so home/away order does not affect the key.
    All components are normalised to lowercase stripped strings before hashing.
    """
    sorted_p = sorted(_norm(p) for p in (participants or []))
    components = (
        _norm(league),
        _norm(official_event_id),
        _norm(scheduled_start_utc),
        json.dumps(sorted_p, separators=(",", ":")),
        _norm(settlement_market),
    )
    raw = json.dumps(components, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_event_key_from_row(row: dict[str, Any]) -> str:
    """
    Convenience wrapper: extract the five canonical fields from a row dict
    and delegate to build_event_key.

    Field resolution order (first non-None wins):
      league              → row["league"] | row["sport"]
      official_event_id   → row["official_event_id"] | row["event_id"] | row["espn_event_id"]
      scheduled_start_utc → row["scheduled_start_utc"] | row["game_start_time"] | row["slate_date"]
      participants        → row["participants"] |
                            [row["home_team"], row["away_team"]] |
                            [row["home"], row["away"]]
      settlement_market   → row["settlement_market"] | row["market"]
    """
    league = row.get("league") or row.get("sport")
    official_event_id = (
        row.get("official_event_id")
        or row.get("event_id")
        or row.get("espn_event_id")
    )
    scheduled_start_utc = (
        row.get("scheduled_start_utc")
        or row.get("game_start_time")
        or row.get("slate_date")
    )
    participants_raw = row.get("participants")
    if not participants_raw:
        home = row.get("home_team") or row.get("home") or ""
        away = row.get("away_team") or row.get("away") or ""
        participants_raw = [home, away]
    settlement_market = row.get("settlement_market") or row.get("market")

    return build_event_key(
        league=league,
        official_event_id=official_event_id,
        scheduled_start_utc=scheduled_start_utc,
        participants=participants_raw,
        settlement_market=settlement_market,
    )


def annotate_rows_with_event_key(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    In-place: populate row["event_key"] for any row that does not already have one.
    Returns the same list (mutated).
    """
    for row in rows:
        if not row.get("event_key"):
            row["event_key"] = build_event_key_from_row(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Status detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_event_status(event_meta: dict[str, Any]) -> dict[str, Any]:
    """
    Classify an event as SCHEDULED / POSTPONED / IN_PROGRESS / COMPLETED /
    CANCELLED / UNKNOWN from raw event metadata.

    Accepts metadata dicts from:
      - ESPN (validate_espn_event output): status_state, completed, status_desc
      - Kalshi events: settlement_status, status
      - Internal row dicts: postponed, is_postponed, status

    Returns:
      {
        status        str   — one of the STATUS_* constants
        can_score     bool  — False when status blocks pre-game scoring
        block_reason  str | None
        raw_state     str | None — original state string seen
      }
    """
    state       = _norm(event_meta.get("status_state") or event_meta.get("status") or "")
    completed   = bool(event_meta.get("completed") or event_meta.get("is_completed"))
    status_desc = _norm(event_meta.get("status_desc") or event_meta.get("status_description") or "")
    ppd_flag    = bool(event_meta.get("postponed") or event_meta.get("is_postponed"))

    # ── Completed / final ─────────────────────────────────────────────────────
    if completed or state in _COMPLETED_STATES:
        return {
            "status":       STATUS_COMPLETED,
            "can_score":    False,
            "block_reason": "EVENT_COMPLETED_NO_PRE_GAME_SCORING",
            "raw_state":    state or status_desc,
        }

    # ── In-progress ───────────────────────────────────────────────────────────
    if state in _IN_PROGRESS_STATES:
        return {
            "status":       STATUS_IN_PROGRESS,
            "can_score":    False,
            "block_reason": "EVENT_IN_PROGRESS_NO_LIVE_SCORING",
            "raw_state":    state,
        }

    # ── Postponed ─────────────────────────────────────────────────────────────
    desc_has_ppd = any(w in status_desc for w in _POSTPONED_WORDS)
    if ppd_flag or desc_has_ppd or state in _POSTPONED_WORDS:
        return {
            "status":       STATUS_POSTPONED,
            "can_score":    False,
            "block_reason": "EVENT_POSTPONED",
            "raw_state":    status_desc or state,
        }

    # ── Cancelled ─────────────────────────────────────────────────────────────
    desc_has_can = any(w in status_desc for w in _CANCELLED_WORDS)
    if desc_has_can or state in _CANCELLED_WORDS:
        return {
            "status":       STATUS_CANCELLED,
            "can_score":    False,
            "block_reason": "EVENT_CANCELLED",
            "raw_state":    status_desc or state,
        }

    # ── Scheduled / pre-game ──────────────────────────────────────────────────
    if state in ("pre", "scheduled", "upcoming", ""):
        return {
            "status":       STATUS_SCHEDULED,
            "can_score":    True,
            "block_reason": None,
            "raw_state":    state,
        }

    # ── Unknown ───────────────────────────────────────────────────────────────
    return {
        "status":       STATUS_UNKNOWN,
        "can_score":    True,   # conservative: unknown → allow, but flag
        "block_reason": None,
        "raw_state":    state,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Slate-date matching
# ─────────────────────────────────────────────────────────────────────────────

def validate_slate_date_utc(
    scheduled_start_utc: Optional[str],
    target_date:         Optional[date] = None,
) -> dict[str, Any]:
    """
    Confirm that scheduled_start_utc falls on target_date (UTC).

    Returns:
      {passed, reason, target, found}
    """
    td         = target_date or datetime.now(timezone.utc).date()
    target_iso = td.isoformat()

    if not scheduled_start_utc:
        return {
            "passed": False,
            "reason": "NO_SCHEDULED_START_UTC",
            "target": target_iso,
            "found":  None,
        }

    raw = str(scheduled_start_utc).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    found_date: Optional[date] = None

    # Try ISO datetime first
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        found_date = dt.astimezone(timezone.utc).date()
    except ValueError:
        pass

    # Fall back to date-only formats
    if found_date is None:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                found_date = datetime.strptime(raw[:10], fmt).date()
                break
            except ValueError:
                continue

    if found_date is None:
        return {
            "passed": False,
            "reason": f"UNPARSEABLE_SCHEDULED_START_UTC",
            "target": target_iso,
            "found":  str(scheduled_start_utc),
        }

    if found_date != td:
        return {
            "passed": False,
            "reason": "DATE_MISMATCH",
            "target": target_iso,
            "found":  found_date.isoformat(),
        }

    return {
        "passed": True,
        "reason": "DATE_CONFIRMED",
        "target": target_iso,
        "found":  found_date.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_duplicates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Scan rows for duplicate event_keys (same event submitted more than once).
    Rows must already have "event_key" populated.

    Returns:
      {
        has_duplicates  bool
        duplicate_keys  list[str]   — event_keys appearing more than once
        groups          dict        — event_key → [row_indices]
      }
    """
    groups: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        key = row.get("event_key")
        if key:
            groups.setdefault(key, []).append(idx)

    dup_keys = [k for k, idxs in groups.items() if len(idxs) > 1]

    return {
        "has_duplicates": bool(dup_keys),
        "duplicate_keys": dup_keys,
        "groups":         groups,
    }
