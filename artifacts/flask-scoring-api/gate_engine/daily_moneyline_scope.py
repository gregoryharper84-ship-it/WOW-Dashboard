"""
gate_engine/daily_moneyline_scope.py — Narrow remaining-today moneyline scope
for canonical WOW Daily runs (Task #277).

Scope contract
--------------
A Daily run persisted with scope MONEYLINE_REMAINING_TODAY researches ONLY
OUTRIGHT_WINNER / OUTRIGHT_WIN_PROBABILITY_ONLY candidates for events that:
  1. fall on the requested local run date (timezone-aware, using the run's
     persisted IANA timezone), and
  2. start strictly AFTER the request-time instant captured when the run was
     acknowledged (the persisted ``scope_requested_at``).

The broader prop board is never acquired: this module only calls the h2h
(moneyline) odds boundary and every candidate row it emits is classified,
validated, and scored through the EXISTING LLP moneyline lane
(gate_engine.market_family + gate_engine.moneyline_probability).  No
probability formula, calibration, threshold, gate decision, or terminal-label
taxonomy is defined here.

Governance invariants
---------------------
- can_execute = False (unconditional; research only)
- Events without a parseable start time are excluded fail-closed (with an
  explicit exclusion reason), never scored speculatively.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

can_execute = False
MODULE_ID = "WOW-DAILY-MONEYLINE-SCOPE-v1.0"

SCOPE_FULL_BOARD = "FULL_BOARD"
SCOPE_MONEYLINE_REMAINING_TODAY = "MONEYLINE_REMAINING_TODAY"

# The scoped lane serializes each candidate with the canonical prop-board
# identity fields (player/prop/side/line) so the orchestrator's existing
# canonical-selection machinery applies unchanged.
_SCOPED_PROP_NAME = "outright_winner"
_SCOPED_SIDE = "WIN"
_SCOPED_LINE = 0.0


def _parse_instant(value: str | None) -> datetime | None:
    """Parse an ISO instant; returns None on absence or malformation."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def filter_remaining_today_events(
    events: list[dict[str, Any]],
    *,
    run_date: str,
    run_timezone: str,
    scope_requested_at: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep only events on the requested local date that have not started.

    Comparison basis is the persisted request instant — NOT the executor's
    current wall clock — so retries and delayed executors see the identical
    event boundary the caller's request established.

    Returns (kept_events, exclusion_notes).  Events with a missing or
    unparseable commence time are excluded fail-closed with a note.
    """
    request_instant = _parse_instant(scope_requested_at)
    if request_instant is None:
        raise ValueError("SCOPE_REQUEST_INSTANT_REQUIRED")
    tz = ZoneInfo(run_timezone)

    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    for event in events or []:
        event_id = str(event.get("id") or "UNKNOWN_EVENT")
        commence = _parse_instant(event.get("commence_time"))
        if commence is None:
            notes.append(f"{event_id}:EXCLUDED_NO_COMMENCE_TIME")
            continue
        local_date = commence.astimezone(tz).date().isoformat()
        if local_date != run_date:
            notes.append(f"{event_id}:EXCLUDED_DATE_MISMATCH:{local_date}")
            continue
        if commence <= request_instant:
            notes.append(f"{event_id}:EXCLUDED_ALREADY_STARTED")
            continue
        kept.append(event)
    return kept, notes


def _rows_for_event(sport: str, event: dict[str, Any], run_date: str) -> list[dict]:
    """Build one canonical Daily candidate row per event participant.

    ``line`` / ``prop`` / ``side`` exist only as canonical Daily manifest
    identity fields.  ``score_scoped_moneyline_rows`` deliberately removes
    those fields before handing a copy to the MONEYLINE_V1 validator, where a
    prop line is correctly prohibited.
    """
    home = event.get("home_team")
    away = event.get("away_team")
    if not home or not away:
        return []
    source_snapshot: dict[str, Any] | None = None
    try:
        from gate_engine.moneyline.market_snapshot import build_snapshot_from_odds_event

        source_snapshot = build_snapshot_from_odds_event(
            event,
            sport,
            market_key="h2h",
        ).to_dict()
    except Exception:
        # Do not invent a market.  Absence is intentionally handed to the
        # existing moneyline pipeline, whose market-stage contract fails
        # closed when it cannot verify a snapshot.
        logger.debug(
            "daily scoped moneyline snapshot unavailable event=%s",
            event.get("id"),
            exc_info=True,
        )
    rows = []
    for team, opponent, home_away in ((home, away, "HOME"), (away, home, "AWAY")):
        rows.append({
            "row_id":       str(uuid.uuid4()),
            "sport":        sport,
            "team":         team,
            "opponent":     opponent,
            "event_id":     event.get("id"),
            "slate_date":   run_date,
            "market_type":  "h2h",
            "home_team":    home,
            "away_team":    away,
            "home_away":    home_away,
            "commence_time": event.get("commence_time"),
            # Canonical prop-board identity fields (orchestrator machinery)
            "player":       team,
            "prop":         _SCOPED_PROP_NAME,
            "side":         _SCOPED_SIDE,
            "line":         _SCOPED_LINE,
            "scope":        SCOPE_MONEYLINE_REMAINING_TODAY,
            "objective":    "OUTRIGHT_WIN_PROBABILITY_ONLY",
            "_daily_scope_market_snapshot": source_snapshot,
        })
    return rows


def discover_remaining_today_moneyline(
    sport: str,
    *,
    run_date: str,
    run_timezone: str,
    scope_requested_at: str,
) -> tuple[list[dict], dict[str, str]]:
    """Narrow discovery: h2h events only, remaining-today only.

    Returns (candidate_rows, source_status) matching the orchestrator's
    per-sport union contract.  Non-moneyline props are structurally never
    requested — the only odds boundary consulted is the h2h market.
    """
    from services.odds_api import SPORT_KEYS, get_h2h_odds

    status: dict[str, str] = {}
    sport_key = SPORT_KEYS.get(sport) or SPORT_KEYS.get(sport.upper())
    if not sport_key:
        status[f"{sport}_odds"] = "UNAVAILABLE:SPORT_NOT_SUPPORTED_FOR_MONEYLINE_SCOPE"
        return [], status

    try:
        events, odds_status = get_h2h_odds(sport_key)
    except Exception as exc:
        status[f"{sport}_odds"] = f"FAILED:{exc}"
        return [], status
    raw_status = str(odds_status)
    # The narrow scope has a single required source boundary.  Normalize every
    # documented h2h failure form to the orchestrator's coverage-failure
    # vocabulary so an unavailable board never terminalizes as a successful
    # empty discovery.  Available primary and fallback responses remain
    # unchanged.
    if any(token in raw_status.upper() for token in (
        "FAILED", "NOT_CALLED", "UNAVAILABLE", "ERROR",
        "PROACTIVE_SKIP", "QUOTA_EXHAUSTED",
    )):
        status[f"{sport}_odds"] = f"FAILED:{raw_status}"
    else:
        status[f"{sport}_odds"] = raw_status

    kept, exclusion_notes = filter_remaining_today_events(
        list(events or []),
        run_date=run_date,
        run_timezone=run_timezone,
        scope_requested_at=scope_requested_at,
    )
    if exclusion_notes:
        status[f"{sport}_scope_exclusions"] = ";".join(exclusion_notes[:50])

    rows: list[dict] = []
    for event in kept:
        rows.extend(_rows_for_event(sport, event, run_date))
    return rows, status


def _bucket_for_terminal_label(terminal_label: str | None) -> str:
    """Map an EXISTING moneyline terminal label onto a manifest bucket.

    This is manifest bookkeeping only — the label itself is preserved
    verbatim on the card.  The scoped lane is research-only, so nothing ever
    maps to an approved bucket.
    """
    label = str(terminal_label or "").upper()
    if "DATA_CONTRACT" in label or "UNAVAILABLE" in label or "INSUFFICIENT" in label:
        return "data_insufficient"
    if "REJECT" in label:
        return "reject"
    if "NO_PLAY" in label:
        return "no_play"
    return "watch"


def _lane_copy(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the clean MONEYLINE_V1 payload for one persisted Daily row.

    Daily's generic reconciliation layer requires player/prop/side/line to
    form a stable selection ID.  They must not leak into the specialist:
    MONEYLINE_V1 correctly rejects a prop ``line`` and the moneyline lane has
    no prop-side semantics.  The manifest card is reconstructed from the
    original candidate after scoring.
    """
    lane_row = dict(candidate)
    for field in ("line", "prop", "side"):
        lane_row.pop(field, None)
    return lane_row


def _card_with_identity(
    candidate_by_row_id: dict[str, dict[str, Any]],
    lane_row: dict[str, Any],
) -> dict[str, Any]:
    """Restore canonical Daily identity fields onto a moneyline lane result."""
    row_id = str(lane_row.get("row_id") or "")
    card = dict(candidate_by_row_id.get(row_id) or lane_row)
    card.update({
        key: value
        for key, value in lane_row.items()
        if key not in {"line", "prop", "side"}
    })
    return card


def score_scoped_moneyline_rows(
    rows: list[dict],
    *,
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Route scoped candidates through the existing LLP moneyline lane.

    Uses the same classification, MONEYLINE_V1 contract validation, event
    deduplication, and specialist scorer that /gate-engine/run applies to
    OUTRIGHT_WINNER rows.  Invalid rows fail per-row (never silently drop);
    every candidate lands in exactly one terminal bucket.
    """
    from gate_engine.market_family import (
        classify_row,
        guard_route_config,
        partition_and_validate_outright_rows,
        MarketFamily,
    )
    from gate_engine.moneyline_probability import (
        deduplicate_events,
        score_outright_winner_row,
    )

    enrichment = enrichment or {}
    result: dict[str, Any] = {
        "market_verified": [], "final_approved_internal": [],
        "model_qualified": [], "conditional": [], "watch": [],
        "reject": [], "data_insufficient": [], "no_play": [],
        "failed_modules": [], "execution_notes": [], "run_status": "COMPLETE",
    }

    candidate_by_row_id = {
        str(row.get("row_id") or ""): dict(row)
        for row in rows
    }
    outright_rows: list[dict] = []
    for candidate in rows:
        row = _lane_copy(candidate)
        classify_row(row)
        if row.get("market_family") == MarketFamily.OUTRIGHT_WINNER:
            outright_rows.append(row)
        else:
            # Structural exclusion: a non-moneyline candidate can never be
            # routed by the scoped lane.
            card = _card_with_identity(candidate_by_row_id, row)
            card["terminal_bucket"] = "no_play"
            # Preserve the existing terminal-label taxonomy.  The blocker
            # names the narrow-scope failure without inventing a new label.
            card["terminal_label"] = "DATA_CONTRACT_FAIL"
            card["blockers"] = ["SCOPED_NON_MONEYLINE_EXCLUDED"]
            card["can_execute"] = False
            result["no_play"].append(card)
            result["execution_notes"].append(
                f"SCOPED_NON_MONEYLINE_EXCLUDED:{row.get('row_id')}"
            )

    route_guard = guard_route_config(
        outright_rows,
        body_input_contract="MONEYLINE_V1",
    )
    if route_guard:
        result["failed_modules"].append("daily_moneyline_scope:ROUTE_GUARD_FAIL")
        for row in outright_rows:
            card = _card_with_identity(candidate_by_row_id, row)
            card.update({
                "terminal_bucket": "data_insufficient",
                "terminal_label": "DATA_CONTRACT_FAIL",
                "blockers": [str(route_guard.get("error") or "RUN_INVALID_ROUTE_CONFIGURATION")],
                "can_execute": False,
                "can_approve_bets": False,
            })
            result["data_insufficient"].append(card)
        return result

    partition = partition_and_validate_outright_rows(outright_rows)
    for invalid in partition["invalid"]:
        inv_row = invalid["row"]
        card = _card_with_identity(candidate_by_row_id, inv_row)
        card.update({
            "terminal_bucket":     "data_insufficient",
            "terminal_label":      "DATA_CONTRACT_FAIL",
            "blockers": [
                f"MONEYLINE_V1_CONTRACT_VIOLATION:{v}"
                for v in invalid["violations"]
            ],
            "contract_violations": invalid["violations"],
            "failure_isolation":   "MONEYLINE_LANE_ONLY",
            "can_execute":         False,
            "can_approve_bets":    False,
        })
        result["data_insufficient"].append(card)

    # Deduplicate only repeated appearances of the same TEAM selection.  A
    # h2h event naturally has two valid OUTRIGHT_WINNER candidates (home and
    # away); collapsing the entire event would silently drop one side.
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in partition["valid"]:
        key = (
            str(row.get("sport") or "").upper(),
            str(row.get("event_id") or ""),
            str(row.get("team") or row.get("player") or "").lower(),
        )
        grouped.setdefault(key, []).append(row)
    deduped: list[dict] = []
    canonical_row_ids: set[str] = set()
    for group in grouped.values():
        group_deduped, group_map = deduplicate_events(group)
        deduped.extend(group_deduped)
        for row_ids in group_map.values():
            if row_ids:
                canonical_row_ids.add(str(row_ids[0]))
    for row in partition["valid"]:
        if str(row.get("row_id") or "") not in canonical_row_ids:
            # Duplicate-event rows collapse onto the canonical scored entry;
            # account for them explicitly so reconciliation stays exact.
            card = _card_with_identity(candidate_by_row_id, row)
            card["terminal_bucket"] = "no_play"
            card["terminal_label"] = "NO_PLAY"
            card["blockers"] = ["DUPLICATE_EVENT_COLLAPSED"]
            card["can_execute"] = False
            result["no_play"].append(card)

    for row in deduped:
        row_enrichment = dict(enrichment.get(row.get("row_id") or "") or {})
        source_snapshot = row.get("_daily_scope_market_snapshot")
        if "market_snapshot" not in row_enrichment and source_snapshot is not None:
            # Preserve the established handoff's key-presence semantics: even
            # an empty supplied snapshot must be judged fail-closed by stage 0
            # instead of triggering a second acquisition attempt.
            row_enrichment["market_snapshot"] = source_snapshot

        sport = (row.get("sport") or "").upper()
        if sport in ("NBA", "MLB") and not any(
            row_enrichment.get(key)
            for key in ("home_win_pct", "away_win_pct", "home_power", "away_power")
        ):
            try:
                from gate_engine.moneyline.team_acquisition import acquire_team_data

                team_data = acquire_team_data(row, sport)
                if team_data:
                    row_enrichment.update(team_data)
            except Exception:
                logger.debug(
                    "daily scoped team acquisition unavailable row=%s",
                    row.get("row_id"),
                    exc_info=True,
                )
        try:
            scored = score_outright_winner_row(row, enrichment=row_enrichment)
        except Exception as exc:
            logger.exception("scoped moneyline scoring failed row=%s", row.get("row_id"))
            card = _card_with_identity(candidate_by_row_id, row)
            card.update({
                "terminal_bucket": "data_insufficient",
                # Do not invent a terminal label for an infrastructure
                # exception.  Preserve the existing label taxonomy and expose
                # the technical detail only in the blocker/failure metadata.
                "terminal_label":  "DATA_CONTRACT_FAIL",
                "blockers":        [f"MONEYLINE_SCORING_EXCEPTION:{type(exc).__name__}"],
                "can_execute":     False,
                "can_approve_bets": False,
            })
            result["data_insufficient"].append(card)
            result["failed_modules"].append(
                f"daily_moneyline_scope:SCORING_FAILED:{row.get('row_id')}"
            )
            continue
        card = _card_with_identity(candidate_by_row_id, row)
        card.update({
            "market_family":          "OUTRIGHT_WINNER",
            "objective":              "OUTRIGHT_WIN_PROBABILITY_ONLY",
            "controlling_skill":      "wow.llp-moneyline-probability-expert",
            "input_contract_version": "MONEYLINE_V1",
            "terminal_label":         scored.get("terminal_label"),
            "blockers":               scored.get("blockers"),
            "model_id":               scored.get("model_id"),
            "model_status":           scored.get("model_status"),
            "probability_snapshot":   scored.get("probability_snapshot"),
            "specialist_probability": scored.get("specialist_probability"),
            "route_compatibility":    scored.get("route_compatibility"),
            "can_execute":            False,
            "can_approve_bets":       False,
        })
        card["terminal_bucket"] = _bucket_for_terminal_label(card["terminal_label"])
        result[card["terminal_bucket"]].append(card)

    # Mandatory event-level mutual exclusion.  The two discovery lanes may
    # evaluate both sides, but only one side or no side may survive as the
    # event selection.  Re-bucket any side whose local MONEY_QUALIFIED label is
    # capped by the governor.
    from gate_engine.moneyline.event_decision_governor import govern_event_cards
    _bucket_names = (
        "model_scored", "market_verified", "final_approved_internal",
        "no_play", "data_insufficient",
    )
    _scored_cards = [
        card
        for name in _bucket_names
        for card in result[name]
        if card.get("market_family") == "OUTRIGHT_WINNER"
    ]
    govern_event_cards(_scored_cards)
    for name in _bucket_names:
        retained = []
        for card in result[name]:
            if card.get("event_decision"):
                destination = _bucket_for_terminal_label(card.get("terminal_label"))
                card["terminal_bucket"] = destination
                if destination != name:
                    result[destination].append(card)
                    continue
            retained.append(card)
        result[name] = retained

    return result
