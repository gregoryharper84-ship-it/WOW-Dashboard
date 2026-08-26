"""
gate_engine/moneyline/slate_integrity.py
WOW v16 — Slate integrity and event-lock helpers for moneyline rows.

Migrated from moneyline_probability.py without behavior change.
Adds participant/status lock check.

can_execute=False unconditional.
"""
from __future__ import annotations

from typing import Any

can_execute: bool = False


# ---------------------------------------------------------------------------
# Event deduplication (unchanged from moneyline_probability.py)
# ---------------------------------------------------------------------------

def _event_dedup_key(row: dict[str, Any]) -> str:
    sport  = (row.get("sport") or "").upper().strip()
    team   = (row.get("team") or row.get("player") or "").strip().lower()
    opp    = (row.get("opponent") or "").strip().lower()
    date   = (row.get("slate_date") or "").strip()[:10]
    participants = "|".join(sorted([team, opp]))
    return f"{sport}:{participants}:{date}"


def _record_platform_appearance(canonical: dict[str, Any], row: dict[str, Any]) -> None:
    appearance = {
        "platform":      row.get("board_source") or row.get("platform") or "unknown",
        "odds":          row.get("odds") or row.get("sportsbook_odds"),
        "row_id":        row.get("row_id"),
        "event_id":      row.get("event_id"),
        "settlement_id": row.get("settlement_id"),
    }
    appearances: list = canonical.setdefault("platform_appearances", [])
    if appearance not in appearances:
        appearances.append(appearance)


def deduplicate_events(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    seen: dict[str, dict[str, Any]] = {}
    dedup_map: dict[str, list[str]] = {}
    for row in rows:
        key    = _event_dedup_key(row)
        row_id = row.get("row_id") or row.get("event_id") or "unknown"
        if key not in seen:
            canonical = dict(row)
            canonical.setdefault("platform_appearances", [])
            _record_platform_appearance(canonical, row)
            seen[key] = canonical
            dedup_map[key] = [str(row_id)]
        else:
            _record_platform_appearance(seen[key], row)
            dedup_map[key].append(str(row_id))
    return list(seen.values()), dedup_map


# ---------------------------------------------------------------------------
# Participant / status lock
# ---------------------------------------------------------------------------

def check_participant_status(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    """
    Verify that the primary participant(s) are confirmed active.

    Returns:
        {
          "locked": bool,
          "blockers": [str],
          "participant_status": str,
        }
    """
    blockers: list[str] = []

    # Check primary team/player status
    player_status = (enrichment.get("player_status") or
                     enrichment.get("team_status") or "UNKNOWN").upper()

    if player_status in ("OUT", "SCRATCHED", "INACTIVE", "DNP"):
        blockers.append(
            f"PARTICIPANT_LOCK_FAILED:primary_participant_status={player_status}"
        )

    # MLB: only block if a pitcher is explicitly marked scratched/out — not when absent.
    # Missing SP data means the model falls back to team-level data, which is fine.
    # The stale-model check (check_stale_model) already handles pitcher changes.
    sport = (row.get("sport") or "").upper()
    if sport == "MLB":
        for side, status_key in [("home", "sp_home_status"), ("away", "sp_away_status")]:
            sp_status = (enrichment.get(status_key) or "").upper()
            if sp_status in ("SCRATCHED", "OUT"):
                blockers.append(
                    f"PARTICIPANT_LOCK_FAILED:sp_{side}_status={sp_status}"
                )

    return {
        "locked":             len(blockers) == 0,
        "blockers":           blockers,
        "participant_status": player_status,
    }


# ---------------------------------------------------------------------------
# Stale model invalidation (unchanged from moneyline_probability.py)
# ---------------------------------------------------------------------------

def check_stale_model(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    prior_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if prior_snapshot is None:
        return {"stale": False, "reason": None, "disposition": "NO_PRIOR"}

    sport = (row.get("sport") or "").upper()

    if sport == "MLB":
        for side in ("home", "away"):
            k = f"starting_pitcher_{side}"
            prior_sp = prior_snapshot.get(k)
            current_sp = enrichment.get(k)
            if prior_sp and current_sp and prior_sp != current_sp:
                return {
                    "stale": True,
                    "reason": f"Starting pitcher changed: {side} {prior_sp!r} → {current_sp!r}",
                    "disposition": "STALE_MODEL_INVALIDATED",
                }

    prior_active = set(prior_snapshot.get("active_key_players") or [])
    current_out  = set(enrichment.get("out_players") or [])
    newly_out = prior_active & current_out
    if newly_out:
        return {
            "stale": True,
            "reason": f"Key player(s) status changed to OUT/DNP: {sorted(newly_out)}",
            "disposition": "STALE_MODEL_INVALIDATED",
        }

    return {"stale": False, "reason": None, "disposition": "VALID"}


# ---------------------------------------------------------------------------
# Final refresh
# ---------------------------------------------------------------------------

def check_final_refresh(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    """
    Re-verify event and participant status immediately before output.
    Any material change triggers needs_rerun=True.
    """
    refresh_flags: list[str] = []

    event_status = (enrichment.get("event_status") or "SCHEDULED").upper()
    if event_status in ("IN_PROGRESS", "FINAL", "CANCELLED", "POSTPONED"):
        refresh_flags.append(f"EVENT_STARTED_OR_CONCLUDED:status={event_status}")

    freshness_h = enrichment.get("status_freshness_hours")
    if freshness_h is not None:
        try:
            if float(freshness_h) > 2.0:
                refresh_flags.append(f"STATUS_STALE:age={freshness_h}h")
        except (TypeError, ValueError):
            pass

    board_line_confirmed = enrichment.get("board_line_confirmed")
    scored_line = enrichment.get("_scored_line") or row.get("line")
    if (board_line_confirmed is not None and scored_line is not None):
        try:
            if abs(float(board_line_confirmed) - float(scored_line)) > 0.5:
                refresh_flags.append(
                    f"BOARD_LINE_CHANGED:scored={scored_line} current={board_line_confirmed}"
                )
        except (TypeError, ValueError):
            pass

    return {
        "refresh_required": len(refresh_flags) > 0,
        "refresh_flags":    refresh_flags,
    }
