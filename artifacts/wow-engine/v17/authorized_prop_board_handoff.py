"""Fail-closed third-party prop board -> governed V17 prop scoring *proposal*.

This module is a pure compiler. It does not fetch, write, score, or publish.
The caller must supply trusted **server-side** official identity and current
offer verifier callbacks, plus source-use permission. Callers/user uploads may
never self-assert verification. Incomplete rows remain visible as typed holds.
A READY row is a validated /score-pick-request input proposal, NOT a fitted
probability or terminal result. Existing Action, evidence, model, calibration,
freshness, and V17_TERMINAL_REDUCER gates remain authoritative.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Callable, Mapping

from v17.authorized_prop_board_discovery import PROVIDER, SCHEMA as DISCOVERY_SCHEMA

CAN_EXECUTE = False
HANDOFF_SCHEMA = "WOW_V17_AUTHORIZED_PROP_HANDOFF_V1"
MAX_RECEIVED_CANDIDATES = 3200
ACTION_BATCH_SIZE = 50


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(timezone.utc) if t.utcoffset() is not None else None


def _line(value: Any) -> str | None:
    try:
        x = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return format(x.normalize(), "f") if x.is_finite() and x > 0 else None


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _hold(row: Mapping[str, Any], code: str) -> dict[str, Any]:
    return {
        "source_projection_id": row.get("source_projection_id"),
        "direction": row.get("direction"),
        "status": "BLOCKED",
        "typed_blocker": code,
        "probability_publishable": False,
        "can_execute": False,
    }


def _validate_official(row: Mapping[str, Any], resolved: Any, now: datetime) -> str | None:
    if not isinstance(resolved, Mapping):
        return "PROP_IDENTITY_OFFICIAL_RESOLUTION_MISSING"
    required = (
        "official_event_id", "official_player_id", "player", "sport",
        "stat_type", "stat_display", "period", "event_start_time",
        "event_status", "team", "official_source", "verified_at",
    )
    if any(not _text(resolved.get(k)) for k in required):
        return "PROP_IDENTITY_OFFICIAL_RESOLUTION_INCOMPLETE"
    if _text(resolved["official_source"]).upper() in (
        "PRIZEPICKSAPI_INDEPENDENT", "PROPSMADNESS", "SCOUT", "THIRD_PARTY_BOARD",
    ):
        return "PROP_IDENTITY_AUTHORITY_INVALID"
    if _text(resolved["event_status"]).upper() not in ("SCHEDULED", "PRE", "PREGAME"):
        return "EVENT_NOT_PREGAME"
    if _text(resolved["sport"]).upper() != _text(row.get("sport")).upper():
        return "PROP_IDENTITY_SPORT_CONFLICT"
    if _text(resolved["player"]).casefold() != _text(row.get("player")).casefold():
        return "PROP_IDENTITY_PLAYER_CONFLICT"
    if _text(resolved["stat_display"]).casefold() != _text(row.get("market_stat_display")).casefold():
        return "PROP_IDENTITY_STAT_CONFLICT"
    if _text(resolved["period"]).casefold() != _text(row.get("period_display")).casefold():
        return "PROP_IDENTITY_PERIOD_CONFLICT"
    if (_text(row.get("team_unverified"))
            and _text(resolved["team"]).casefold() != _text(row.get("team_unverified")).casefold()):
        return "PROP_IDENTITY_TEAM_CONFLICT"
    board_opponent = _text(row.get("opponent_unverified")).casefold()
    if board_opponent:
        known_opponents = {_text(resolved.get("opponent")).casefold()}
        # Alias proof can only come from the trusted official resolver, never
        # from the third-party offer row that is being reconciled.
        aliases = resolved.get("opponent_aliases", ())
        if isinstance(aliases, (list, tuple)) and all(isinstance(a, str) for a in aliases):
            known_opponents.update(_text(a).casefold() for a in aliases)
        if board_opponent not in known_opponents:
            return "PROP_IDENTITY_OPPONENT_CONFLICT"
    event_start = _utc(resolved["event_start_time"])
    board_start = _utc(row.get("event_start_time"))
    checked = _utc(resolved["verified_at"])
    if not event_start or not board_start or event_start != board_start:
        return "PROP_IDENTITY_EVENT_START_CONFLICT"
    if event_start <= now:
        return "EVENT_ALREADY_STARTED"
    if checked is None or checked > now or checked < now - timedelta(hours=4):
        return "PROP_IDENTITY_OFFICIAL_EVIDENCE_STALE"
    return None


def _validate_offer(row: Mapping[str, Any], refreshed: Any, now: datetime) -> str | None:
    if not isinstance(refreshed, Mapping):
        return "PROP_OFFER_REFRESH_MISSING"
    if refreshed.get("source_provider") != PROVIDER:
        return "PROP_OFFER_SOURCE_CONFLICT"
    if refreshed.get("source_projection_id") != row.get("source_projection_id"):
        return "PROP_OFFER_IDENTITY_CONFLICT"
    if refreshed.get("direction") != row.get("direction"):
        return "PROP_OFFER_DIRECTION_CHANGED"
    if _line(refreshed.get("exact_display_line")) != _line(row.get("exact_display_line")):
        return "PROP_OFFER_LINE_CHANGED"
    if refreshed.get("offer_type") != row.get("offer_type"):
        return "PROP_OFFER_TYPE_CHANGED"
    if refreshed.get("available") is not True:
        return "PROP_OFFER_NOT_AVAILABLE"
    observed = _utc(refreshed.get("source_observed_at"))
    original = _utc(row.get("source_observed_at"))
    if observed is None or original is None or observed < original or observed > now:
        return "PROP_OFFER_REFRESH_INVALID"
    if (now - observed).total_seconds() > 20 * 60:
        return "PROP_OFFER_REFRESH_STALE"
    return None


def compile_authorized_prop_handoff(
    discovery: Mapping[str, Any],
    *,
    source_use_permitted: bool,
    official_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
    exact_offer_refresh: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
    scorer_compatibility: Callable[[str, str], bool] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compile exact-once scoring proposals, never attempt /score-pick-request.

    `source_use_permitted` is only meaningful if it originates from a trusted
    server-side entitlement check, never an end-user request field.
    """
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("PROP_HANDOFF_NOW_UNAWARE")
    current = current.astimezone(timezone.utc)
    if not isinstance(discovery, Mapping) or discovery.get("schema") != DISCOVERY_SCHEMA:
        raise ValueError("PROP_HANDOFF_DISCOVERY_SCHEMA_INVALID")
    if discovery.get("source_provider") != PROVIDER:
        raise ValueError("PROP_HANDOFF_SOURCE_INVALID")
    candidates = discovery.get("directional_candidates")
    if not isinstance(candidates, list) or len(candidates) > MAX_RECEIVED_CANDIDATES:
        raise ValueError("PROP_HANDOFF_CANDIDATES_INVALID")
    source_blockers = discovery.get("blockers", [])
    if not isinstance(source_blockers, list) or len(source_blockers) > 100 or any(
        not isinstance(b, str) or not b.strip() or len(b) > 160 for b in source_blockers
    ):
        raise ValueError("PROP_HANDOFF_DISCOVERY_BLOCKERS_INVALID")
    source_coverage_complete = discovery.get("source_coverage_complete_for_requested_sport") is True

    ready: list[dict[str, Any]] = []
    proposal_provenance: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    duplicate: list[dict[str, Any]] = []
    accepted_keys: set[tuple[str, ...]] = set()
    for row in candidates:
        if not isinstance(row, Mapping):
            blocked.append(_hold({}, "PROP_HANDOFF_ROW_SCHEMA_INVALID"))
            continue
        if row.get("source_provider") != PROVIDER or row.get("controlling_route") != "WOW_PROP_LANE":
            blocked.append(_hold(row, "PROP_HANDOFF_ROW_SOURCE_CONFLICT"))
            continue
        if row.get("can_execute") is not False or row.get("probability_publishable") is not False:
            blocked.append(_hold(row, "PROP_HANDOFF_RESEARCH_AUTHORITY_CONFLICT"))
            continue
        if source_use_permitted is not True:
            blocked.append(_hold(row, "PROP_HANDOFF_SOURCE_USE_NOT_PERMITTED"))
            continue
        # The canonical score Action does not yet express partial-game
        # periods or tier-specific payouts. Never project those markets onto
        # a standard full-game stat merely because the stat name matches.
        if (_text(row.get("period_display")).casefold() != "full game"
                or _text(row.get("offer_type")).casefold() != "standard"):
            blocked.append(_hold(row, "PROP_HANDOFF_EXACT_ROUTE_UNRESOLVED"))
            continue
        if official_resolver is None:
            blocked.append(_hold(row, "PROP_IDENTITY_OFFICIAL_RESOLVER_UNAVAILABLE"))
            continue
        try:
            official = official_resolver(row)
        except Exception:
            blocked.append(_hold(row, "PROP_IDENTITY_OFFICIAL_RESOLVER_FAILED"))
            continue
        reason = _validate_official(row, official, current)
        if reason:
            blocked.append(_hold(row, reason))
            continue
        if exact_offer_refresh is None:
            blocked.append(_hold(row, "PROP_OFFER_REFRESH_UNAVAILABLE"))
            continue
        try:
            latest = exact_offer_refresh(row)
        except Exception:
            blocked.append(_hold(row, "PROP_OFFER_REFRESH_FAILED"))
            continue
        reason = _validate_offer(row, latest, current)
        if reason:
            blocked.append(_hold(row, reason))
            continue
        stat = _text(official["stat_type"]).upper()
        if not stat or not _line(row.get("exact_display_line")):
            blocked.append(_hold(row, "PROP_HANDOFF_EXACT_ROUTE_UNRESOLVED"))
            continue
        # The canonical Action accepts a free-form stat string. Only a trusted
        # server-side lookup against the actual scorer's sport/stat registry
        # can prove this proposal is representable; research rows cannot.
        if scorer_compatibility is None:
            blocked.append(_hold(row, "PROP_HANDOFF_SCORER_COMPATIBILITY_UNVERIFIED"))
            continue
        try:
            compatible = scorer_compatibility(_text(row.get("sport")).upper(), stat)
        except Exception:
            blocked.append(_hold(row, "PROP_HANDOFF_SCORER_COMPATIBILITY_UNVERIFIED"))
            continue
        if compatible is not True:
            blocked.append(_hold(row, "PROP_HANDOFF_SCORER_COMPATIBILITY_UNVERIFIED"))
            continue
        key = (
            _text(row.get("sport")), _text(official["official_event_id"]),
            _text(official["official_player_id"]), stat,
            _text(row.get("period_display")), _line(row["exact_display_line"]),
            _text(row.get("direction")), _text(row.get("offer_type")),
        )
        if key in accepted_keys:
            duplicate.append({
                "source_projection_id": row.get("source_projection_id"),
                "direction": row.get("direction"),
                "status": "DUPLICATE", "canonical_key": "|".join(key),
                "can_execute": False,
            })
            continue
        accepted_keys.add(key)
        token = sha256("|".join(key).encode("utf-8")).hexdigest()[:20]
        proposal_provenance.append({
            "row_key": "authorized-prop:" + token,
            "source_provider": PROVIDER,
            "source_projection_id": row.get("source_projection_id"),
            "source_player_alias": row.get("source_player_alias"),
            "source_event_alias": row.get("source_event_alias"),
            "official_event_id": official["official_event_id"],
            "official_player_id": official["official_player_id"],
            "official_source": official["official_source"],
            "official_verified_at": official["verified_at"],
            "source_market_stat_display": row.get("market_stat_display"),
            "exact_display_line": row["exact_display_line"],
            "period": row.get("period_display"),
            "direction": row["direction"],
            "offer_type": row["offer_type"],
            "source_observed_at": latest["source_observed_at"],
            "payout_terms_verified": False,
            "official_prizepicks_offer_independently_verified": False,
            "can_execute": False,
        })
        ready.append({
            "row_key": "authorized-prop:" + token,
            "event_id": official["official_event_id"],
            "event_start_time": official["event_start_time"],
            "sport": row["sport"], "player": official["player"],
            "stat_type": stat, "line": float(row["exact_display_line"]),
            "direction": row["direction"], "source_type": "NORMALIZED",
            "platform": "PRIZEPICKS", "league": row["league"],
            "opponent": official.get("opponent"),
            "source_capture_timestamp": latest["source_observed_at"],
            "money_lane_status": "PAYOUT_UNRESOLVED",
        })

    reconciled = len(candidates) == len(ready) + len(blocked) + len(duplicate)
    batches = [ready[i:i + ACTION_BATCH_SIZE] for i in range(0, len(ready), ACTION_BATCH_SIZE)]
    return {
        "schema": HANDOFF_SCHEMA,
        "status": ("INPUT_PROPOSALS_READY" if ready and reconciled
                   and source_coverage_complete and not source_blockers
                   else "BLOCKED_OR_EMPTY"),
        "source_provenance": PROVIDER, "received_directional_candidates": len(candidates),
        "scoring_input_proposals": ready, "batches_for_future_action": batches,
        "proposal_provenance": proposal_provenance,
        "blocked_rows": blocked, "duplicate_rows": duplicate,
        "source_discovery_blockers": list(source_blockers),
        "source_coverage_complete_for_requested_sport": source_coverage_complete,
        "reconciliation_valid": reconciled,
        "source_full_board_coverage_established": False,
        "scoring_attempted": False, "action_receipts": [],
        "model_probability": None, "calibrated_lower_bound": None,
        "probability_publishable": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "HANDOFF_SCHEMA", "compile_authorized_prop_handoff"]
