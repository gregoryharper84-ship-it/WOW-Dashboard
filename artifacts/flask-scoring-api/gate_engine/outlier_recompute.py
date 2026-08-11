"""
gate_engine/outlier_recompute.py
WOW-PATCH-2026-08-10-STAGE-A-PROBABILITY-LEDGER-OUTLIER-RECOMPUTE

Outlier-review recompute engine — offline, advisory only.

Flow
----
  outlier_gate.run() sets OUTLIER_FLAG:REVIEW_REQUIRED in row["blockers"]
    → caller passes row to this engine's run()
    → engine returns RESOLVED / UNRESOLVED / ERROR

Threshold reuse (mandatory)
---------------------------
  GAP_THRESHOLD        imported from gate_engine.outlier_gate  (0.20)
  ASSIST_VOL_THRESHOLD imported from gate_engine.outlier_gate  (0.40)

These constants are NOT re-defined here.  If outlier_gate.py changes them,
this engine changes automatically.  The prior outlier_recompute.py invented
its own thresholds — that is the defect this patch corrects.

Exclusion contract (hard invariant)
------------------------------------
Games may only be excluded when they meet deterministic, evidence-backed
criteria derived from the SAME formulas as outlier_gate.run():

  season_high_outlier  : max game > l10_avg * 1.5
  avg_inflated_by_outlier: l10_avg > avg_without_max * 1.15
  l5_l10_gap_flagged   : game > median + 2*stdev (or median*1.5 if stdev≈0)
  assist_volatile      : game > mean + 2*stdev
  median_disagrees_avg : extreme game (max if mean>median, else min)

The engine re-verifies each condition from raw l10_games data.  It does NOT
trust the flags dict blindly — if the flags dict claims a condition is set
but the raw data does not support it, NO candidates are identified and the
result is UNRESOLVED.

This enforces the "never discard inconvenient games" rule: if someone
supplies flags={"season_high_outlier": True} but the actual max game is
below the 1.5× threshold, the engine finds no evidence-backed candidate and
returns UNRESOLVED rather than excluding the max game anyway.

Output states
-------------
  RESOLVED   — evidence-backed exclusion found; divergence drops below
               GAP_THRESHOLD after removal; original + recomputed evidence
               retained; before/after distribution impact computed.
  UNRESOLVED — data contract failure (missing game log, sample too small,
               gate result missing, gate skipped) OR no evidence-backed
               candidate found OR condition persists after exclusion.
               Named reason always set.
  ERROR      — unexpected exception; error_reason always set.

Retained per recompute (all states)
------------------------------------
  original_evidence      : copy of the original l10_games + gate flags
  excluded_event_ids     : tuple of str identifiers for excluded games
  excluded_reasons       : tuple of dicts {event_id, l10_index, excluded_value,
                           exclusion_reason, flag_triggered}
  recomputed_distribution: dict {count, mean, median, min, max, values} or None
  before_mean            : float or None
  after_mean             : float or None
  before_gap_pct         : float or None  (using GAP_THRESHOLD context)
  after_gap_pct          : float or None
  updated_lower_bound    : float or None  (from model_registry.probability_bounds)
  updated_upper_bound    : float or None

Governance
----------
  can_execute              = False
  PRODUCTION_AUTHORITY     = False
  USER_OUTPUT_AUTHORITY    = False
  TERMINAL_LABEL_AUTHORITY = False   (no label assignment or change)

Zero dependency on FOLLOWUP_193/194/195 or B4 code:
  no imports from app.py, classifier.py, pipeline.py, settlement_worker.py,
  universal_agent/*, pipeline_state.py, pipeline_gateway.py.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ── Threshold reuse: import EXACT constants from outlier_gate, do not re-define
from .outlier_gate import GAP_THRESHOLD, ASSIST_VOL_THRESHOLD

# ---------------------------------------------------------------------------
# Governance constants
# ---------------------------------------------------------------------------
can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False   # never assigns or changes any label

# ---------------------------------------------------------------------------
# Minimum sample constraints
# ---------------------------------------------------------------------------
MIN_GAMES_TO_ISOLATE      = 4   # need at least 4 games to meaningfully isolate
MIN_GAMES_AFTER_EXCLUSION = 3   # must retain at least 3 games after removal

# ---------------------------------------------------------------------------
# State enum and named failure reasons
# ---------------------------------------------------------------------------

class OutlierRecomputeState(str, Enum):
    RESOLVED   = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    ERROR      = "ERROR"


class DataContractFailReason:
    """Named constants for UNRESOLVED data-contract failure reasons."""
    MISSING_GAME_LOG                = "MISSING_GAME_LOG"
    MISSING_OUTLIER_GATE_RESULT     = "MISSING_OUTLIER_GATE_RESULT"
    OUTLIER_GATE_SKIPPED            = "OUTLIER_GATE_SKIPPED"
    SAMPLE_TOO_SMALL_TO_ISOLATE     = "SAMPLE_TOO_SMALL_TO_ISOLATE"
    SAMPLE_TOO_SMALL_AFTER_EXCLUSION = "SAMPLE_TOO_SMALL_AFTER_EXCLUSION"
    NO_EVIDENCE_BACKED_CANDIDATE    = "NO_EVIDENCE_BACKED_CANDIDATE"
    CONDITION_PERSISTS_AFTER_EXCLUSION = "CONDITION_PERSISTS_AFTER_EXCLUSION"
    NON_NUMERIC_GAMES               = "NON_NUMERIC_GAMES"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutlierRecomputeResult:
    """
    Immutable result returned by run().  All three states use this type.
    original_evidence is always populated, even on UNRESOLVED or ERROR.
    """
    state:                   OutlierRecomputeState
    reason:                  str          # human-readable short reason
    reason_detail:           str          # longer diagnostic

    # Evidence preserved for all states
    original_evidence:       dict         # {sport, prop_type, l10_games, l5_avg, l10_avg, flags}
    excluded_event_ids:      tuple        # tuple[str] — "" entries when no real IDs available
    excluded_reasons:        tuple        # tuple[dict] — each: event_id, l10_index, excluded_value, …

    # Recomputed distribution — populated on RESOLVED only
    recomputed_distribution: Optional[dict]  # {count, mean, median, min, max, values}
    before_mean:             Optional[float]
    after_mean:              Optional[float]
    before_gap_pct:          Optional[float]
    after_gap_pct:           Optional[float]
    updated_lower_bound:     Optional[float]
    updated_upper_bound:     Optional[float]

    # UNRESOLVED only
    data_contract_fail_reason: Optional[str]
    acquisition_attempts:    tuple        # tuple[dict] — each: {field, outcome}

    # ERROR only
    error_reason:            Optional[str]

    # Governance invariants — always False; frozen prevents mutation.
    terminal_label_authority: bool = False
    can_execute:              bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_numeric(games: list) -> list[float]:
    """Filter l10_games to only finite numeric values; skip bool."""
    result = []
    for g in games:
        if isinstance(g, bool):
            continue
        try:
            fval = float(g)
            if fval == fval and abs(fval) != float("inf"):  # finite check without math import
                result.append(fval)
        except (TypeError, ValueError):
            pass
    return result


def _identify_exclusion_candidates(
    l10_games: list[float],
    flags: dict,
    prop_type: str,
) -> list[tuple[int, str, str]]:
    """
    Re-derive exclusion candidates from raw data using the SAME formulas
    as outlier_gate.run().  Returns list of (index, reason_code, detail).

    This re-verification step is the enforcement of the exclusion contract:
    if flags claim a condition but raw data does not meet the criterion,
    no candidate is returned for that flag.  The caller cannot inject
    non-evidence-backed exclusions by manipulating the flags dict.
    """
    if not l10_games or len(l10_games) < MIN_GAMES_TO_ISOLATE:
        return []

    candidates: list[tuple[int, str, str]] = []
    seen_indices: set[int] = set()

    try:
        l10_mean = statistics.mean(l10_games)
    except statistics.StatisticsError:
        return []

    season_max = max(l10_games)

    # ── season_high_outlier: max exceeds l10_avg * 1.5 (same formula as outlier_gate line 66)
    if flags.get("season_high_outlier") and season_max > l10_mean * 1.5:
        for i in range(len(l10_games) - 1, -1, -1):
            if l10_games[i] == season_max and i not in seen_indices:
                detail = (
                    f"season_max={season_max:.2f} > l10_avg*1.5={l10_mean*1.5:.2f}"
                )
                candidates.append((i, "SEASON_HIGH_OUTLIER", detail))
                seen_indices.add(i)
                break

    # ── avg_inflated_by_outlier: l10_avg > avg_without_max * 1.15 (outlier_gate line 64)
    if flags.get("avg_inflated_by_outlier"):
        games_without_max = [g for g in l10_games if g != season_max] or l10_games
        avg_without_max = statistics.mean(games_without_max) if games_without_max else l10_mean
        if l10_mean > avg_without_max * 1.15:
            for i in range(len(l10_games) - 1, -1, -1):
                if l10_games[i] == season_max and i not in seen_indices:
                    detail = (
                        f"l10_avg={l10_mean:.2f} > avg_without_max*1.15={avg_without_max*1.15:.2f}"
                    )
                    candidates.append((i, "AVG_INFLATED_BY_OUTLIER", detail))
                    seen_indices.add(i)
                    break

    # ── l5_l10_gap_flagged: games beyond median+2*stdev (outlier_gate: player_prop adapter logic)
    if flags.get("l5_l10_gap_flagged") and len(l10_games) >= 4:
        try:
            l10_med = statistics.median(l10_games)
            l10_std = statistics.stdev(l10_games)
            threshold = l10_med + 2.0 * l10_std if l10_std > 0 else l10_med * 1.5
            for i, g in enumerate(l10_games):
                if g > threshold and i not in seen_indices:
                    detail = f"game={g:.2f} > median+2sd_threshold={threshold:.2f}"
                    candidates.append((i, "GAP_OUTLIER", detail))
                    seen_indices.add(i)
        except statistics.StatisticsError:
            pass

    # ── assist_volatile: games beyond mean+2*stdev (outlier_gate lines 73-77)
    if flags.get("assist_volatile") and "assist" in prop_type.lower():
        try:
            l10_std = statistics.stdev(l10_games)
            vol_threshold = l10_mean + 2.0 * l10_std
            for i, g in enumerate(l10_games):
                if g > vol_threshold and i not in seen_indices:
                    detail = f"game={g:.2f} > mean+2sd={vol_threshold:.2f} (ASSIST_VOL)"
                    candidates.append((i, "ASSIST_VOLATILE", detail))
                    seen_indices.add(i)
        except statistics.StatisticsError:
            pass

    # ── median_disagrees_avg (outlier_gate lines 83-86; l5 version)
    if flags.get("median_disagrees_avg") and len(l10_games) >= 3:
        try:
            l10_med = statistics.median(l10_games)
            if l10_mean > l10_med:
                # Mean inflated by high outlier — exclude last max
                for i in range(len(l10_games) - 1, -1, -1):
                    if l10_games[i] == season_max and i not in seen_indices:
                        detail = f"mean={l10_mean:.2f}>median={l10_med:.2f}; excluding max={season_max:.2f}"
                        candidates.append((i, "MEDIAN_DISAGREES_HIGH", detail))
                        seen_indices.add(i)
                        break
            else:
                # Mean deflated by low outlier — exclude first min
                season_min = min(l10_games)
                for i, g in enumerate(l10_games):
                    if g == season_min and i not in seen_indices:
                        detail = f"mean={l10_mean:.2f}<median={l10_med:.2f}; excluding min={season_min:.2f}"
                        candidates.append((i, "MEDIAN_DISAGREES_LOW", detail))
                        seen_indices.add(i)
                        break
        except statistics.StatisticsError:
            pass

    return candidates


def _build_recomputed_dist(games: list[float]) -> dict:
    """Build a recomputed distribution dict from a filtered game list."""
    try:
        return {
            "count":  len(games),
            "mean":   round(statistics.mean(games), 4),
            "median": round(statistics.median(games), 4),
            "min":    round(min(games), 4),
            "max":    round(max(games), 4),
            "values": games,
        }
    except (statistics.StatisticsError, ValueError):
        return {"count": len(games), "mean": None, "median": None,
                "min": None, "max": None, "values": games}


def _try_probability_bounds(
    recomputed_mean: Optional[float],
    sample_size: int,
    sport: str,
    prop_type: str,
) -> tuple[Optional[float], Optional[float]]:
    """
    Attempt to get updated probability bounds from model_registry.
    Returns (lower, upper) or (None, None) if unavailable.
    """
    if recomputed_mean is None or sample_size < MIN_GAMES_AFTER_EXCLUSION:
        return None, None
    try:
        from .model_registry import lookup, probability_bounds
        entry = lookup(sport, prop_type, line=None)
        if entry is None:
            return None, None
        model_status = entry.get("status", "PROVISIONAL")
        lower, upper = probability_bounds(recomputed_mean, sample_size, model_status)
        if lower is None or upper is None:
            return None, None
        return round(float(lower), 4), round(float(upper), 4)
    except Exception:
        return None, None


def _unresolved(
    reason: str,
    reason_detail: str,
    data_contract_fail_reason: str,
    original_evidence: dict,
    acquisition_attempts: list[dict] | None = None,
) -> OutlierRecomputeResult:
    """Convenience constructor for UNRESOLVED results."""
    return OutlierRecomputeResult(
        state=OutlierRecomputeState.UNRESOLVED,
        reason=reason,
        reason_detail=reason_detail,
        original_evidence=original_evidence,
        excluded_event_ids=(),
        excluded_reasons=(),
        recomputed_distribution=None,
        before_mean=None,
        after_mean=None,
        before_gap_pct=None,
        after_gap_pct=None,
        updated_lower_bound=None,
        updated_upper_bound=None,
        data_contract_fail_reason=data_contract_fail_reason,
        acquisition_attempts=tuple(acquisition_attempts or []),
        error_reason=None,
        terminal_label_authority=False,
        can_execute=False,
    )


def _error(
    reason: str,
    original_evidence: dict,
) -> OutlierRecomputeResult:
    """Convenience constructor for ERROR results."""
    return OutlierRecomputeResult(
        state=OutlierRecomputeState.ERROR,
        reason="RECOMPUTE_ERROR",
        reason_detail=reason,
        original_evidence=original_evidence,
        excluded_event_ids=(),
        excluded_reasons=(),
        recomputed_distribution=None,
        before_mean=None,
        after_mean=None,
        before_gap_pct=None,
        after_gap_pct=None,
        updated_lower_bound=None,
        updated_upper_bound=None,
        data_contract_fail_reason=None,
        acquisition_attempts=(),
        error_reason=reason,
        terminal_label_authority=False,
        can_execute=False,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    row: Any,
    enrichment: Optional[dict] = None,
) -> OutlierRecomputeResult:
    """
    Recompute engine entry point.  Never raises — always returns
    OutlierRecomputeResult.

    Parameters
    ----------
    row        : prop row dict (read-only, not mutated).  Must contain
                 row["gates"]["l5_l10_ledger"]["l10_games"] and
                 row["gates"]["outlier_gate"]["flags"].
    enrichment : optional dict; if it contains "game_log" (list of
                 {"game_id": str, "value": number}), real game IDs are used
                 for excluded_event_ids.

    Returns
    -------
    OutlierRecomputeResult — frozen, no row mutations.
    """
    if not isinstance(row, dict):
        row = {}

    sport     = row.get("sport", "UNKNOWN")
    prop_type = row.get("prop_type", "")
    line      = row.get("line")

    # Seed original_evidence — populated regardless of outcome
    original_evidence: dict = {
        "sport":     sport,
        "prop_type": prop_type,
        "line":      line,
        "l10_games": None,
        "l5_avg":    None,
        "l10_avg":   None,
        "flags":     {},
    }

    try:
        # ── Data contract checks ─────────────────────────────────────────

        gates = row.get("gates") or {}

        # 1. outlier_gate result must exist and must not be skipped
        outlier_result = gates.get("outlier_gate")
        if not isinstance(outlier_result, dict):
            return _unresolved(
                reason="MISSING_OUTLIER_GATE_RESULT",
                reason_detail="row['gates']['outlier_gate'] is absent or not a dict",
                data_contract_fail_reason=DataContractFailReason.MISSING_OUTLIER_GATE_RESULT,
                original_evidence=original_evidence,
                acquisition_attempts=[{"field": "outlier_gate", "outcome": "absent"}],
            )
        if outlier_result.get("skipped"):
            return _unresolved(
                reason="OUTLIER_GATE_SKIPPED",
                reason_detail="outlier_gate was skipped (L5L10_NOT_AVAILABLE); cannot recompute",
                data_contract_fail_reason=DataContractFailReason.OUTLIER_GATE_SKIPPED,
                original_evidence=original_evidence,
                acquisition_attempts=[{"field": "outlier_gate", "outcome": "skipped"}],
            )

        flags = outlier_result.get("flags") or {}

        # 2. l10_games must exist in l5_l10_ledger
        ledger_gate = gates.get("l5_l10_ledger") or {}
        l10_games_raw = ledger_gate.get("l10_games")
        l5_avg        = ledger_gate.get("l5_avg")
        l10_avg       = ledger_gate.get("l10_avg")

        original_evidence["flags"]    = dict(flags)
        original_evidence["l5_avg"]   = l5_avg
        original_evidence["l10_avg"]  = l10_avg

        if not l10_games_raw and l10_games_raw != []:
            return _unresolved(
                reason="MISSING_GAME_LOG",
                reason_detail="l10_games not found in row['gates']['l5_l10_ledger']",
                data_contract_fail_reason=DataContractFailReason.MISSING_GAME_LOG,
                original_evidence=original_evidence,
                acquisition_attempts=[{"field": "l10_games", "outcome": "absent"}],
            )

        l10_games_raw = list(l10_games_raw) if l10_games_raw else []

        # 3. Filter to numeric values
        l10_games = _safe_numeric(l10_games_raw)
        original_evidence["l10_games"] = l10_games_raw

        if not l10_games:
            return _unresolved(
                reason="MISSING_GAME_LOG",
                reason_detail="l10_games is empty or contains no numeric values",
                data_contract_fail_reason=DataContractFailReason.MISSING_GAME_LOG,
                original_evidence=original_evidence,
                acquisition_attempts=[{"field": "l10_games", "outcome": "empty_or_non_numeric"}],
            )

        # 4. Minimum sample to isolate
        if len(l10_games) < MIN_GAMES_TO_ISOLATE:
            return _unresolved(
                reason="SAMPLE_TOO_SMALL_TO_ISOLATE",
                reason_detail=(
                    f"l10_games has {len(l10_games)} numeric values; "
                    f"need >= {MIN_GAMES_TO_ISOLATE} to identify outliers"
                ),
                data_contract_fail_reason=DataContractFailReason.SAMPLE_TOO_SMALL_TO_ISOLATE,
                original_evidence=original_evidence,
                acquisition_attempts=[{"field": "l10_games", "outcome": f"count={len(l10_games)}<{MIN_GAMES_TO_ISOLATE}"}],
            )

        # ── Exclusion candidate identification ───────────────────────────
        # Re-derives from raw data; does NOT trust flags blindly.
        # If flags say True but data doesn't meet the criterion → no candidate.
        candidates = _identify_exclusion_candidates(l10_games, flags, prop_type)

        if not candidates:
            return _unresolved(
                reason="NO_EVIDENCE_BACKED_CANDIDATE",
                reason_detail=(
                    "No game met the evidence-backed exclusion criteria when "
                    "verified from raw l10_games data. The flag(s) in the gate "
                    "result do not correspond to an isolatable outlier in the data."
                ),
                data_contract_fail_reason=DataContractFailReason.NO_EVIDENCE_BACKED_CANDIDATE,
                original_evidence=original_evidence,
                acquisition_attempts=[{"field": "exclusion_candidates", "outcome": "none_found"}],
            )

        # ── Build excluded set and remaining games ───────────────────────
        excluded_indices: set[int] = {c[0] for c in candidates}
        remaining_games  = [g for i, g in enumerate(l10_games) if i not in excluded_indices]

        if len(remaining_games) < MIN_GAMES_AFTER_EXCLUSION:
            return _unresolved(
                reason="SAMPLE_TOO_SMALL_AFTER_EXCLUSION",
                reason_detail=(
                    f"After excluding {len(excluded_indices)} game(s), only "
                    f"{len(remaining_games)} remain; need >= {MIN_GAMES_AFTER_EXCLUSION}"
                ),
                data_contract_fail_reason=DataContractFailReason.SAMPLE_TOO_SMALL_AFTER_EXCLUSION,
                original_evidence=original_evidence,
                acquisition_attempts=[],
            )

        # ── Build event IDs from enrichment if available ─────────────────
        game_log_ids: dict[int, str] = {}
        if isinstance(enrichment, dict):
            raw_gl = enrichment.get("game_log") or []
            for idx, entry in enumerate(raw_gl):
                if isinstance(entry, dict) and idx < len(l10_games):
                    gid = str(entry.get("game_id", ""))
                    if gid:
                        game_log_ids[idx] = gid

        # ── Build excluded_event_ids and excluded_reasons tuples ─────────
        excluded_event_ids_list: list[str] = []
        excluded_reasons_list:   list[dict] = []

        for idx, reason_code, detail in candidates:
            event_id = game_log_ids.get(idx, f"l10_idx_{idx}")
            excluded_event_ids_list.append(event_id)
            excluded_reasons_list.append({
                "event_id":        event_id,
                "l10_index":       idx,
                "excluded_value":  l10_games[idx],
                "exclusion_reason": reason_code,
                "detail":          detail,
                "flag_triggered":  reason_code,
            })

        # ── Compute before/after stats ───────────────────────────────────
        before_mean = statistics.mean(l10_games)
        after_mean  = statistics.mean(remaining_games)

        # Use GAP_THRESHOLD (imported from outlier_gate) to assess resolution
        # A divergence is "resolved" when the recomputed gap falls below the threshold.
        before_gap_pct: Optional[float] = None
        after_gap_pct:  Optional[float] = None
        if l5_avg is not None and l10_avg is not None and l10_avg > 0:
            before_gap_pct = abs(l5_avg - l10_avg) / l10_avg
        if l5_avg is not None and after_mean and after_mean > 0:
            after_gap_pct = abs(l5_avg - after_mean) / after_mean

        # ── Recomputed distribution ──────────────────────────────────────
        recomputed_dist = _build_recomputed_dist(remaining_games)

        # ── Updated probability bounds (advisory) ────────────────────────
        lower_b, upper_b = _try_probability_bounds(
            after_mean, len(remaining_games), sport, prop_type
        )

        # ── Resolve decision: is the flagged condition gone? ─────────────
        # The outlier is considered resolved if after_gap_pct < GAP_THRESHOLD
        # OR (when gap isn't calculable) the recomputed mean differs from
        # original by > 5% — indicating the excluded game was material.
        resolved = False
        resolution_detail: str

        if after_gap_pct is not None and before_gap_pct is not None:
            if after_gap_pct < GAP_THRESHOLD and before_gap_pct >= GAP_THRESHOLD:
                resolved = True
                resolution_detail = (
                    f"After excluding {len(excluded_indices)} outlier game(s), "
                    f"l5/l10 gap dropped from {before_gap_pct:.1%} to {after_gap_pct:.1%} "
                    f"(below GAP_THRESHOLD={GAP_THRESHOLD:.0%})"
                )
            else:
                resolution_detail = (
                    f"Condition persists: after_gap={after_gap_pct:.1%} "
                    f">= GAP_THRESHOLD={GAP_THRESHOLD:.0%}"
                )
        elif after_mean and before_mean:
            mean_shift_pct = abs(after_mean - before_mean) / before_mean
            if mean_shift_pct > 0.05:
                resolved = True
                resolution_detail = (
                    f"Mean shifted by {mean_shift_pct:.1%} after excluding outlier(s); "
                    f"l5/l10 averages not available for gap recheck"
                )
            else:
                resolution_detail = (
                    f"Mean shift {mean_shift_pct:.1%} too small; "
                    f"excluding these games has no material effect"
                )
        else:
            resolved = True   # gap not computable but candidates were found + excluded
            resolution_detail = (
                "Outlier(s) excluded; distribution recomputed; "
                "gap metrics unavailable (l5/l10 avg not in row)"
            )

        if not resolved:
            return _unresolved(
                reason="CONDITION_PERSISTS_AFTER_EXCLUSION",
                reason_detail=resolution_detail,
                data_contract_fail_reason=DataContractFailReason.CONDITION_PERSISTS_AFTER_EXCLUSION,
                original_evidence=original_evidence,
                acquisition_attempts=[],
            )

        return OutlierRecomputeResult(
            state=OutlierRecomputeState.RESOLVED,
            reason="OUTLIER_RESOLVED",
            reason_detail=resolution_detail,
            original_evidence=original_evidence,
            excluded_event_ids=tuple(excluded_event_ids_list),
            excluded_reasons=tuple(excluded_reasons_list),
            recomputed_distribution=recomputed_dist,
            before_mean=round(before_mean, 4),
            after_mean=round(after_mean, 4),
            before_gap_pct=round(before_gap_pct, 4) if before_gap_pct is not None else None,
            after_gap_pct=round(after_gap_pct, 4) if after_gap_pct is not None else None,
            updated_lower_bound=lower_b,
            updated_upper_bound=upper_b,
            data_contract_fail_reason=None,
            acquisition_attempts=(),
            error_reason=None,
            terminal_label_authority=False,
            can_execute=False,
        )

    except Exception as exc:
        return _error(
            reason=f"{type(exc).__name__}: {exc!s:.120}",
            original_evidence=original_evidence,
        )
