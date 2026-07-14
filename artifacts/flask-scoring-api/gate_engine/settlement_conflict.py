"""
settlement_conflict.py — WOW-PATCH-2026-07-10
Cross-platform settlement conflict detection.

When Kalshi and PrizePicks report different results for the same normalized
event (or any platform contradicts the official game result), the row is
labeled SETTLEMENT_SOURCE_CONFLICT with:
    bankroll_status    = PENDING_RECONCILIATION
    model_result       = null
    calibration_eligible = False

Resolution priority (highest wins):
  1. Official league result
  2. Platform transaction credit
  3. Platform settlement display
  4. Screenshot label  (NEVER overrides a conflict)

A green UI label alone cannot override a settlement conflict.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFLICT_LABEL        = "SETTLEMENT_SOURCE_CONFLICT"
BANKROLL_PENDING      = "PENDING_RECONCILIATION"
MODEL_RESULT_NULL     = None
CALIBRATION_ELIGIBLE  = False

# Source trust hierarchy (higher index = higher trust)
SOURCE_PRIORITY = [
    "screenshot_label",
    "platform_settlement_display",
    "platform_transaction_credit",
    "official_league_result",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_WIN_TOKENS  = {"win", "w", "hit", "yes", "correct", "covered", "over", "more"}
_LOSS_TOKENS = {"loss", "l", "miss", "no", "wrong", "not_covered", "under", "less"}
_PUSH_TOKENS = {"push", "void", "tie", "scratch", "no_action"}


def _normalize_result(result: Any) -> str | None:
    """
    Normalize a result value to 'WIN', 'LOSS', 'PUSH', or None (unknown).
    """
    if result is None:
        return None
    s = str(result).lower().strip().replace(" ", "_")
    if s in _WIN_TOKENS:
        return "WIN"
    if s in _LOSS_TOKENS:
        return "LOSS"
    if s in _PUSH_TOKENS:
        return "PUSH"
    # Already normalized token
    if s in ("win", "loss", "push"):
        return s.upper()
    return None


def _results_conflict(a: str | None, b: str | None) -> bool:
    """
    Return True if two known results disagree (PUSH is not a conflict).
    """
    if a is None or b is None:
        return False
    if "PUSH" in (a, b):
        return False
    return a != b


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_conflict(
    event_id:     str,
    platform_results: dict[str, Any],
) -> dict[str, Any]:
    """
    Scan platform results for one event and detect conflicts.

    Parameters
    ----------
    event_id          : canonical event ID from event_normalization
    platform_results  : {platform_name: result_value}
                        e.g. {"kalshi": "loss", "prizepicks": "win",
                               "official": "loss"}

    Returns
    -------
    {
      event_id             : str
      conflict_detected    : bool
      conflicting_pair     : list[str] | None     — e.g. ["kalshi","prizepicks"]
      authoritative_result : str | None           — resolved by priority
      authoritative_source : str | None
      conflict_label       : str | None
      bankroll_status      : str | None
      model_result         : None  (always null when conflict)
      calibration_eligible : bool
      detail               : str
    }
    """
    normalized: dict[str, str | None] = {
        platform: _normalize_result(result)
        for platform, result in (platform_results or {}).items()
    }

    # Determine authoritative result (highest-priority source that has a known result)
    authoritative_result: str | None = None
    authoritative_source: str | None = None

    # Sort sources by trust level (highest trust last)
    source_order = sorted(
        platform_results.keys(),
        key=lambda p: _source_rank(p),
    )
    for src in source_order:
        val = normalized.get(src)
        if val is not None:
            authoritative_result = val
            authoritative_source = src

    # Detect conflicts among known results
    known_results = {p: v for p, v in normalized.items() if v is not None}
    conflict_detected = False
    conflicting_pair: list[str] | None = None

    _SCREENSHOT = "screenshot_label"
    platforms = list(known_results.keys())
    for i in range(len(platforms)):
        for j in range(i + 1, len(platforms)):
            p1, p2 = platforms[i], platforms[j]
            if _results_conflict(known_results[p1], known_results[p2]):
                # Only the "screenshot_label" (green UI) cannot override a real
                # settlement source. Unknown/unrecognized board sources (e.g. "fd",
                # "dk") ARE legitimate platforms and CAN conflict with official results.
                if p1 == _SCREENSHOT:
                    continue
                if p2 == _SCREENSHOT:
                    continue
                conflict_detected = True
                conflicting_pair = [p1, p2]
                break
        if conflict_detected:
            break

    if conflict_detected:
        return {
            "event_id":            event_id,
            "conflict_detected":   True,
            "conflicting_pair":    conflicting_pair,
            "authoritative_result": authoritative_result,
            "authoritative_source": authoritative_source,
            "conflict_label":      CONFLICT_LABEL,
            "bankroll_status":     BANKROLL_PENDING,
            "model_result":        MODEL_RESULT_NULL,
            "calibration_eligible": CALIBRATION_ELIGIBLE,
            "detail":              (
                f"Platform result conflict for event {event_id}: "
                f"{conflicting_pair[0]}={known_results[conflicting_pair[0]]} vs "
                f"{conflicting_pair[1]}={known_results[conflicting_pair[1]]}. "
                "Row flagged SETTLEMENT_SOURCE_CONFLICT — "
                "green UI label cannot override."
            ),
        }

    return {
        "event_id":             event_id,
        "conflict_detected":    False,
        "conflicting_pair":     None,
        "authoritative_result": authoritative_result,
        "authoritative_source": authoritative_source,
        "conflict_label":       None,
        "bankroll_status":      None,
        "model_result":         authoritative_result,
        "calibration_eligible": True,
        "detail":               (
            f"No conflict detected for event {event_id}. "
            f"Authoritative result: {authoritative_result} "
            f"(source: {authoritative_source})."
        ),
    }


def _source_rank(source: str) -> int:
    """Higher = more authoritative."""
    s = (source or "").lower()
    for i, name in enumerate(SOURCE_PRIORITY):
        if name in s or s in name:
            return i
    # Best-effort keyword match
    if "official" in s or "league" in s:
        return 3
    if "transaction" in s or "credit" in s:
        return 2
    if "settlement" in s or "display" in s:
        return 1
    return 0  # screenshot / unknown


def apply_conflict_to_rows(
    grouped_rows: dict[str, list[dict[str, Any]]],
    platform_results_by_event: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    For each event group, scan rows for cross-platform result disagreement
    and apply SETTLEMENT_SOURCE_CONFLICT annotations.

    Parameters
    ----------
    grouped_rows : output of event_normalization.group_entries_by_event
    platform_results_by_event : optional dict {event_id: {platform: result}}
        If not provided, infers from each row's `platform_result` and `board_source`.

    Returns
    -------
    {event_id: conflict_result_dict}  — one entry per event that had rows.
    """
    conflict_map: dict[str, dict[str, Any]] = {}

    for event_id, rows in grouped_rows.items():
        # Build platform_results for this event
        if platform_results_by_event and event_id in platform_results_by_event:
            platform_results = platform_results_by_event[event_id]
        else:
            # Infer from row fields. Priority order for source keys:
            #  1. official_result / official_game_result → "official_league"
            #  2. board_source + platform_result
            #  3. result fallback
            platform_results = {}
            for row in rows:
                # Official result always wins — add with highest-priority key
                for official_field in ("official_result", "official_game_result"):
                    official_val = row.get(official_field)
                    if official_val is not None:
                        platform_results["official_league"] = official_val
                        break

                src    = (row.get("board_source") or "unknown").lower()
                result = row.get("platform_result") or row.get("result")
                if result is not None:
                    platform_results[src] = result

        conflict_result = detect_conflict(event_id, platform_results)
        conflict_map[event_id] = conflict_result

        if conflict_result["conflict_detected"]:
            for row in rows:
                row["settlement_conflict"] = True
                row["conflict_label"]      = CONFLICT_LABEL
                row["bankroll_status"]     = BANKROLL_PENDING
                row["model_result"]        = MODEL_RESULT_NULL
                row["calibration_eligible"] = CALIBRATION_ELIGIBLE
                row.setdefault("blockers", []).append(
                    f"SETTLEMENT_SOURCE_CONFLICT:event={event_id}"
                )
        else:
            for row in rows:
                # Preserve externally-set settlement_conflict=True (e.g. from
                # an upstream enrichment source or manual annotation). Only
                # clear it when the automated scanner found no conflict AND the
                # flag was not already asserted by a trusted upstream source.
                if not row.get("settlement_conflict"):
                    row["settlement_conflict"] = False
                row["authoritative_result"] = conflict_result.get("authoritative_result")

    return conflict_map
