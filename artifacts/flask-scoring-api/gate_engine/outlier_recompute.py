"""
gate_engine/outlier_recompute.py
Outlier Recompute Engine — Stage A (offline module only)

When the outlier gate appends OUTLIER_FLAG:REVIEW_REQUIRED, this module
performs the governed outlier review before terminal disposition.

RECOMPUTE PROCEDURE:
  1. Read the outlier flags and the L10 game-value window.
  2. Identify and isolate outlier game(s) using the specific flag that fired.
  3. Recompute the L9 / post-exclusion distribution (mean, median, std, count).
  4. Record excluded event IDs (index-based within the L10 window) and reasons.
  5. Update uncertainty bounds via model_registry.probability_bounds.
  6. Return an explicit RESOLVED / UNRESOLVED / ERROR state.

RETURN STATES:
  RESOLVED   — outlier(s) isolated, L9 distribution recomputed, bounds updated.
               At least MIN_GAMES_AFTER_EXCLUSION games remain.
  UNRESOLVED — evidence insufficient for recomputation; a named
               data_contract_fail_reason is always set.
  ERROR      — unexpected exception during recomputation; a named error_reason
               is always set.

DATA_CONTRACT_FAIL reasons (UNRESOLVED state):
  MISSING_GAME_LOG           — l10_games list is absent or empty
  SAMPLE_TOO_SMALL_AFTER_EXCLUSION — fewer than MIN_GAMES_AFTER_EXCLUSION
                               games would remain after removing outliers
  SAMPLE_TOO_SMALL_TO_ISOLATE — initial l10 window has fewer games than
                               MIN_GAMES_TO_ISOLATE
  NO_OUTLIER_GAMES_IDENTIFIED — flags fired but isolation produced no candidates
  MISSING_OUTLIER_GATE_RESULT — row has no outlier_gate result to read

OFFLINE INVARIANTS:
  - This module has no terminal-label authority.
  - This module does not write a qualifying label or modify gate outputs.
  - This module does not import from app.py, classifier.py, pipeline.py,
    or any settlement / B4 / universal_agent module.
  - Original input evidence is always preserved in OutlierRecomputeResult.

can_execute             = False
PRODUCTION_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False
USER_OUTPUT_AUTHORITY   = False
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .model_registry import probability_bounds

# ---------------------------------------------------------------------------
# Module-level governance invariants
# ---------------------------------------------------------------------------

can_execute              = False   # offline/advisory only — never change
PRODUCTION_AUTHORITY     = False
TERMINAL_LABEL_AUTHORITY = False
USER_OUTPUT_AUTHORITY    = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_GAMES_AFTER_EXCLUSION = 3   # minimum games that must remain after removal
MIN_GAMES_TO_ISOLATE      = 4   # minimum initial window size to attempt isolation

# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

class OutlierRecomputeState(str, Enum):
    """Explicit three-state outcome — no narrative-only or implicit resolution."""
    RESOLVED   = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    ERROR      = "ERROR"


# ---------------------------------------------------------------------------
# DATA_CONTRACT_FAIL reason constants
# ---------------------------------------------------------------------------

class DataContractFailReason:
    """Named constants for UNRESOLVED data-contract failures."""
    MISSING_GAME_LOG                  = "MISSING_GAME_LOG"
    SAMPLE_TOO_SMALL_AFTER_EXCLUSION  = "SAMPLE_TOO_SMALL_AFTER_EXCLUSION"
    SAMPLE_TOO_SMALL_TO_ISOLATE       = "SAMPLE_TOO_SMALL_TO_ISOLATE"
    NO_OUTLIER_GAMES_IDENTIFIED       = "NO_OUTLIER_GAMES_IDENTIFIED"
    MISSING_OUTLIER_GATE_RESULT       = "MISSING_OUTLIER_GATE_RESULT"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutlierRecomputeResult:
    """
    Structured result from outlier_recompute.run().

    state               — RESOLVED, UNRESOLVED, or ERROR.
    reason              — named constant describing the outcome.
    reason_detail       — human-readable detail (always set).
    excluded_event_ids  — game indices (within L10 window) that were removed.
    excluded_reasons    — structured reason per excluded game.
    recomputed_distribution — post-exclusion stats; None if not RESOLVED.
    original_evidence   — snapshot of all inputs (always preserved).
    updated_lower_bound — new lower bound from model_registry; None if not RESOLVED.
    updated_upper_bound — new upper bound from model_registry; None if not RESOLVED.
    acquisition_attempts — what was tried when evidence was insufficient.
    data_contract_fail_reason — named constant from DataContractFailReason;
                         set for UNRESOLVED state.
    error_reason        — exception info; set for ERROR state.

    Governance:
      terminal_label_authority = False
      can_execute              = False
    """
    state:                    str
    reason:                   str
    reason_detail:            str
    excluded_event_ids:       tuple[str, ...]
    excluded_reasons:         tuple[dict, ...]
    recomputed_distribution:  dict | None
    original_evidence:        dict
    updated_lower_bound:      float | None
    updated_upper_bound:      float | None
    acquisition_attempts:     tuple[dict, ...]
    data_contract_fail_reason: str | None
    error_reason:             str | None
    # Governance invariants — always False; frozen dataclass prevents mutation.
    terminal_label_authority: bool = False
    can_execute:              bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_outlier_gate(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the outlier_gate result dict, or None if not present."""
    gates = row.get("gates") or {}
    og = gates.get("outlier_gate") or {}
    if og.get("skipped"):
        return None
    return og if og else None


def _extract_l10_games(row: dict[str, Any]) -> list[float] | None:
    """
    Return the L10 game-value list.  Prefer the outlier_gate result (which
    stores the l10_games it read); fall back to l5_l10_ledger.
    """
    gates = row.get("gates") or {}
    ledger = gates.get("l5_l10_ledger") or {}
    games = ledger.get("l10_games")
    if games and isinstance(games, list) and len(games) > 0:
        try:
            return [float(g) for g in games if g is not None]
        except (TypeError, ValueError):
            return None
    return None


def _extract_l5_games(row: dict[str, Any]) -> list[float] | None:
    """Return the L5 game-value list from the l5_l10_ledger result."""
    gates = row.get("gates") or {}
    ledger = gates.get("l5_l10_ledger") or {}
    games = ledger.get("l5_games")
    if games and isinstance(games, list) and len(games) > 0:
        try:
            return [float(g) for g in games if g is not None]
        except (TypeError, ValueError):
            return None
    return None


def _identify_outlier_indices(
    l10_games: list[float],
    flags: dict[str, Any],
) -> list[tuple[int, str]]:
    """
    Identify which game indices are outliers based on the fired flags.

    Returns a list of (index, reason_string) tuples.
    Duplicate indices are deduplicated (keeping the first reason seen).
    """
    seen:    set[int]                  = set()
    outliers: list[tuple[int, str]] = []

    def _add(idx: int, reason: str) -> None:
        if idx not in seen:
            seen.add(idx)
            outliers.append((idx, reason))

    # season_high_outlier / avg_inflated_by_outlier: the max-value game
    # is the primary candidate.
    if flags.get("season_high_outlier") or flags.get("avg_inflated_by_outlier"):
        if l10_games:
            max_val = max(l10_games)
            # Use the LAST occurrence of the max to prefer recent outliers.
            for i in range(len(l10_games) - 1, -1, -1):
                if l10_games[i] == max_val:
                    reason = (
                        "season_high_outlier" if flags.get("season_high_outlier")
                        else "avg_inflated_by_outlier"
                    )
                    _add(i, f"{reason}:value={max_val}")
                    break

    # l5_l10_gap_flagged: games with values significantly above/below the
    # inter-quartile range are candidates.  Use median as the reference.
    if flags.get("l5_l10_gap_flagged") and len(l10_games) >= 4:
        try:
            med = statistics.median(l10_games)
            stdev = statistics.stdev(l10_games) if len(l10_games) >= 2 else 0.0
            threshold = med + 2.0 * stdev if stdev > 0 else med * 1.5
            for i, v in enumerate(l10_games):
                if v > threshold:
                    _add(i, f"l5_l10_gap_flagged:value={v}>threshold={threshold:.2f}")
        except statistics.StatisticsError:
            pass

    # assist_volatile: any game > mean + 2 * std is a high-variance outlier
    if flags.get("assist_volatile") and len(l10_games) >= 3:
        try:
            avg   = statistics.mean(l10_games)
            stdev = statistics.stdev(l10_games)
            if stdev > 0:
                for i, v in enumerate(l10_games):
                    if v > avg + 2.0 * stdev:
                        _add(i, f"assist_volatile:value={v}>mean+2sd={avg + 2 * stdev:.2f}")
        except statistics.StatisticsError:
            pass

    # median_disagrees_avg: games that are pulling mean away from median.
    # Remove the extreme value (max or min depending on direction of divergence).
    if flags.get("median_disagrees_avg") and len(l10_games) >= 4:
        try:
            avg = statistics.mean(l10_games)
            med = statistics.median(l10_games)
            if avg > med:  # high outlier pulling mean up
                max_val = max(l10_games)
                for i in range(len(l10_games) - 1, -1, -1):
                    if l10_games[i] == max_val:
                        _add(i, f"median_disagrees_avg:mean={avg:.2f}>median={med:.2f};high_outlier={max_val}")
                        break
            else:  # low outlier pulling mean down
                min_val = min(l10_games)
                for i, v in enumerate(l10_games):
                    if v == min_val:
                        _add(i, f"median_disagrees_avg:mean={avg:.2f}<median={med:.2f};low_outlier={min_val}")
                        break
        except statistics.StatisticsError:
            pass

    return outliers


def _safe_compute_distribution(remaining: list[float]) -> dict[str, Any]:
    """Compute descriptive stats for the post-exclusion game list."""
    n = len(remaining)
    mean_val = statistics.mean(remaining) if n > 0 else None
    median_val = statistics.median(remaining) if n > 0 else None
    stdev_val  = statistics.stdev(remaining) if n >= 2 else None
    return {
        "count":  n,
        "mean":   round(mean_val, 4) if mean_val is not None else None,
        "median": round(median_val, 4) if median_val is not None else None,
        "stdev":  round(stdev_val, 4) if stdev_val is not None else None,
        "min":    min(remaining) if remaining else None,
        "max":    max(remaining) if remaining else None,
        "values": remaining,
    }


def _build_original_evidence(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    l10_games: list[float] | None,
    flags: dict[str, Any],
) -> dict[str, Any]:
    """
    Build a snapshot of original input evidence.
    Always preserved in the result regardless of outcome.
    """
    gates  = row.get("gates") or {}
    ledger = gates.get("l5_l10_ledger") or {}
    return {
        "sport":            row.get("sport"),
        "prop_type":        row.get("prop_type"),
        "player":           row.get("player"),
        "line":             row.get("line"),
        "l10_games":        list(l10_games) if l10_games else [],
        "l5_avg":           ledger.get("l5_avg"),
        "l10_avg":          ledger.get("l10_avg"),
        "l5_median":        ledger.get("l5_median"),
        "l10_median":       ledger.get("l10_median"),
        "outlier_flags":    dict(flags),
        "enrichment_keys":  sorted(enrichment.keys()) if enrichment else [],
    }


def _result_unresolved(
    reason: str,
    reason_detail: str,
    original_evidence: dict,
    acquisition_attempts: list[dict] | None = None,
) -> OutlierRecomputeResult:
    return OutlierRecomputeResult(
        state=OutlierRecomputeState.UNRESOLVED,
        reason=reason,
        reason_detail=reason_detail,
        excluded_event_ids=(),
        excluded_reasons=(),
        recomputed_distribution=None,
        original_evidence=original_evidence,
        updated_lower_bound=None,
        updated_upper_bound=None,
        acquisition_attempts=tuple(acquisition_attempts or []),
        data_contract_fail_reason=reason,
        error_reason=None,
        terminal_label_authority=False,
        can_execute=False,
    )


def _result_error(
    error_reason: str,
    reason_detail: str,
    original_evidence: dict,
) -> OutlierRecomputeResult:
    return OutlierRecomputeResult(
        state=OutlierRecomputeState.ERROR,
        reason="RECOMPUTE_ERROR",
        reason_detail=reason_detail,
        excluded_event_ids=(),
        excluded_reasons=(),
        recomputed_distribution=None,
        original_evidence=original_evidence,
        updated_lower_bound=None,
        updated_upper_bound=None,
        acquisition_attempts=(),
        data_contract_fail_reason=None,
        error_reason=error_reason,
        terminal_label_authority=False,
        can_execute=False,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> OutlierRecomputeResult:
    """
    Perform the governed outlier review for a row that carries
    OUTLIER_FLAG:REVIEW_REQUIRED.

    Args:
        row        — the prop row dict; must have row["gates"]["outlier_gate"]
                     populated with any_flag=True.
        enrichment — enrichment dict (used for game_log event IDs and model
                     lookup context).

    Returns:
        OutlierRecomputeResult with state RESOLVED, UNRESOLVED, or ERROR.

    This function never raises.  All exceptions are caught and returned
    as an ERROR result with a named error_reason.

    Governance:
        No terminal label is written.  can_execute = False.
        Original evidence is always preserved in the result.
    """
    enrichment = enrichment or {}

    # Build original evidence snapshot before any computation.
    _raw_og: dict[str, Any] = {}  # placeholder; built below after extractions

    try:
        # ── Step 1: Extract outlier gate result ───────────────────────────
        og = _extract_outlier_gate(row)
        l10_games = _extract_l10_games(row)
        flags: dict[str, Any] = (og.get("flags") or {}) if og else {}

        _raw_og = _build_original_evidence(row, enrichment, l10_games, flags)

        if og is None:
            return _result_unresolved(
                reason=DataContractFailReason.MISSING_OUTLIER_GATE_RESULT,
                reason_detail=(
                    "row['gates']['outlier_gate'] is absent or was skipped; "
                    "cannot perform outlier review without gate result."
                ),
                original_evidence=_raw_og,
                acquisition_attempts=[{
                    "field": "outlier_gate_result",
                    "attempted": "row[gates][outlier_gate]",
                    "outcome": "ABSENT_OR_SKIPPED",
                }],
            )

        # ── Step 2: Validate game data availability ────────────────────────
        if not l10_games:
            return _result_unresolved(
                reason=DataContractFailReason.MISSING_GAME_LOG,
                reason_detail=(
                    "l10_games list is absent or empty in l5_l10_ledger result; "
                    "cannot perform outlier isolation without game-level data."
                ),
                original_evidence=_raw_og,
                acquisition_attempts=[
                    {"field": "l10_games", "attempted": "row[gates][l5_l10_ledger][l10_games]",
                     "outcome": "ABSENT_OR_EMPTY"},
                    {"field": "game_log",  "attempted": "enrichment[game_log]",
                     "outcome": "NOT_TRIED" if not enrichment.get("game_log") else "PRESENT"},
                ],
            )

        if len(l10_games) < MIN_GAMES_TO_ISOLATE:
            return _result_unresolved(
                reason=DataContractFailReason.SAMPLE_TOO_SMALL_TO_ISOLATE,
                reason_detail=(
                    f"l10_games has only {len(l10_games)} entries; "
                    f"minimum {MIN_GAMES_TO_ISOLATE} required to attempt isolation."
                ),
                original_evidence=_raw_og,
                acquisition_attempts=[{
                    "field": "l10_games", "attempted": "row[gates][l5_l10_ledger][l10_games]",
                    "outcome": f"INSUFFICIENT_SAMPLE:count={len(l10_games)}",
                }],
            )

        # ── Step 3: Identify outlier games ────────────────────────────────
        outlier_pairs = _identify_outlier_indices(l10_games, flags)

        if not outlier_pairs:
            return _result_unresolved(
                reason=DataContractFailReason.NO_OUTLIER_GAMES_IDENTIFIED,
                reason_detail=(
                    "Outlier gate fired but isolation algorithm produced no "
                    "candidate games from the L10 window.  "
                    f"Flags: {[k for k, v in flags.items() if v is True]}"
                ),
                original_evidence=_raw_og,
                acquisition_attempts=[],
            )

        outlier_indices: set[int] = {idx for idx, _ in outlier_pairs}

        # ── Step 4: Compute remaining games ───────────────────────────────
        remaining = [v for i, v in enumerate(l10_games) if i not in outlier_indices]

        if len(remaining) < MIN_GAMES_AFTER_EXCLUSION:
            return _result_unresolved(
                reason=DataContractFailReason.SAMPLE_TOO_SMALL_AFTER_EXCLUSION,
                reason_detail=(
                    f"Only {len(remaining)} game(s) would remain after removing "
                    f"{len(outlier_indices)} outlier(s); minimum "
                    f"{MIN_GAMES_AFTER_EXCLUSION} required."
                ),
                original_evidence=_raw_og,
                acquisition_attempts=[{
                    "field": "l10_games_post_exclusion",
                    "attempted": "compute_remaining",
                    "outcome": f"INSUFFICIENT:remaining={len(remaining)},removed={len(outlier_indices)}",
                }],
            )

        # ── Step 5: Recompute distribution ────────────────────────────────
        recomputed = _safe_compute_distribution(remaining)

        # ── Step 6: Update bounds via model_registry ─────────────────────
        sport    = (row.get("sport") or "").upper()
        stat_key = (row.get("prop_type") or row.get("stat_key") or "").upper()
        line     = row.get("line")

        new_mean = recomputed.get("mean")
        sample_n = recomputed.get("count") or len(remaining)

        # Determine model status for bounds calculation.
        try:
            from .model_registry import lookup as _registry_lookup
            reg_entry   = _registry_lookup(sport, stat_key, line)
            model_status = reg_entry.get("status", "PROVISIONAL")
        except Exception:
            model_status = "PROVISIONAL"

        # Convert recomputed mean to a probability using a simple hit-rate
        # (fraction of games above the line), capped to (0, 1) open interval.
        hit_rate: float | None = None
        if new_mean is not None and line is not None:
            try:
                line_f   = float(line)
                hits     = sum(1 for v in remaining if v > line_f)
                hit_rate = hits / len(remaining) if remaining else None
                # Clamp to open interval (avoids degenerate 0.0 or 1.0 bounds).
                if hit_rate is not None:
                    hit_rate = max(0.001, min(0.999, hit_rate))
            except (TypeError, ValueError):
                hit_rate = None

        lb, ub = probability_bounds(hit_rate, sample_n, model_status)

        # ── Step 7: Build excluded event IDs ─────────────────────────────
        # Use index-based IDs (position in L10 window).  Attempt to cross-
        # reference with game_log enrichment for real game IDs if available.
        game_log = enrichment.get("game_log") or []
        excluded_event_ids: list[str] = []
        excluded_reasons:   list[dict] = []

        for idx, reason_str in outlier_pairs:
            # Try to get a real event ID from the enrichment game log.
            if isinstance(game_log, list) and idx < len(game_log):
                glog_entry = game_log[idx]
                if isinstance(glog_entry, dict):
                    real_id = (
                        glog_entry.get("game_id")
                        or glog_entry.get("gameId")
                        or glog_entry.get("id")
                        or f"l10_index_{idx}"
                    )
                else:
                    real_id = f"l10_index_{idx}"
            else:
                real_id = f"l10_index_{idx}"

            excluded_event_ids.append(str(real_id))
            excluded_reasons.append({
                "event_id":        str(real_id),
                "l10_index":       idx,
                "excluded_value":  l10_games[idx],
                "exclusion_reason": reason_str,
            })

        # ── Step 8: Return RESOLVED result ────────────────────────────────
        return OutlierRecomputeResult(
            state=OutlierRecomputeState.RESOLVED,
            reason="OUTLIER_RECOMPUTE_COMPLETE",
            reason_detail=(
                f"Isolated {len(outlier_indices)} outlier game(s) from L10 window; "
                f"recomputed L{len(remaining)} distribution "
                f"(mean={recomputed.get('mean')}, median={recomputed.get('median')})."
            ),
            excluded_event_ids=tuple(excluded_event_ids),
            excluded_reasons=tuple(excluded_reasons),
            recomputed_distribution=recomputed,
            original_evidence=_raw_og,
            updated_lower_bound=lb,
            updated_upper_bound=ub,
            acquisition_attempts=(),
            data_contract_fail_reason=None,
            error_reason=None,
            terminal_label_authority=False,
            can_execute=False,
        )

    except Exception as exc:
        return _result_error(
            error_reason=f"{type(exc).__name__}:{str(exc)[:200]}",
            reason_detail=(
                f"Unexpected error during outlier recomputation: "
                f"{type(exc).__name__}: {exc!s:.200}"
            ),
            original_evidence=_raw_og if _raw_og else {},
        )
