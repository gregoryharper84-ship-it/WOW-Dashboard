"""
audit_closure.py
WOW v16 Claude Audit Closure — required fields and gate validators.

Every prop that reaches the approval layer must pass all closure validators.
Validators never approve. They block, flag, or cap the label ceiling.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Required closure fields
# ---------------------------------------------------------------------------
CLOSURE_FIELDS = [
    "l5_line_used",
    "approval_timestamp",
    "approved_line",
    "current_line",
    "model_prob",
    "slip_type",
    "edge_vs_friction",
    "market_edge_confirmed",
    "data_provenance",
    "matchup_grade_source",
    "primary_signal",
    "structural_failure_count",
    "unresolved_conflict_flags",
    "board_timestamp",
    "market_timestamp",
]

# ---------------------------------------------------------------------------
# Slip-type break-even thresholds (no-vig implied probability floor)
# ---------------------------------------------------------------------------
BREAK_EVEN = {
    "POWER":  0.556,
    "FLEX":   0.500,
    "NONE":   0.500,
}
SAFETY_BUFFER = 0.020
APPROVAL_STALE_HOURS = 3
LINE_MOVEMENT_THRESHOLD = 0.5
L5_LINE_TOLERANCE = 0.5
MAX_STRUCTURAL_FAILURES = 3

# DES = Data-Edge-Status conflict (persists across sessions)
DES_CONFLICT_TAG = "DES_CONFLICT"


# ---------------------------------------------------------------------------
# Closure result helper
# ---------------------------------------------------------------------------
def _result(passed: bool, code: str, detail: str = "",
            ceiling: str | None = None) -> dict[str, Any]:
    return {
        "passed":  passed,
        "code":    code,
        "detail":  detail,
        "ceiling": ceiling,
    }


# ---------------------------------------------------------------------------
# 1. validate_required_fields
# ---------------------------------------------------------------------------
def validate_required_fields(closure: dict[str, Any]) -> dict[str, Any]:
    """Every closure field must be present (value may be None for timestamps,
    but the key must exist). Fields that must not be None are enforced here."""
    missing = [f for f in CLOSURE_FIELDS if f not in closure]
    if missing:
        return _result(False, "MISSING_CLOSURE_FIELDS",
                       f"Missing: {', '.join(missing)}")
    return _result(True, "REQUIRED_FIELDS_OK")


# ---------------------------------------------------------------------------
# 2. validate_l5_line_used
# ---------------------------------------------------------------------------
def validate_l5_line_used(closure: dict[str, Any]) -> dict[str, Any]:
    """l5_line_used must match current_line within 0.5. Mismatch kills prop."""
    l5  = closure.get("l5_line_used")
    cur = closure.get("current_line")
    if l5 is None or cur is None:
        return _result(False, "L5_LINE_MISSING",
                       "l5_line_used or current_line is None")
    try:
        delta = abs(float(l5) - float(cur))
    except (TypeError, ValueError):
        return _result(False, "L5_LINE_UNPARSEABLE",
                       f"l5={l5} cur={cur}")
    if delta >= L5_LINE_TOLERANCE:
        return _result(False, "L5_LINE_MISMATCH",
                       f"|l5_line_used({l5}) - current_line({cur})| = {delta:.3f} >= {L5_LINE_TOLERANCE}")
    return _result(True, "L5_LINE_OK",
                   f"delta={delta:.3f}")


# ---------------------------------------------------------------------------
# 3. validate_approval_staleness
# ---------------------------------------------------------------------------
def validate_approval_staleness(closure: dict[str, Any]) -> dict[str, Any]:
    """
    approval_timestamp older than 3 hours → RERUN_REQUIRED.
    Line moved 0.5+ since approval → RERUN_REQUIRED.
    """
    ts_raw = closure.get("approval_timestamp")
    if not ts_raw:
        return _result(False, "NO_APPROVAL_TIMESTAMP",
                       "approval_timestamp missing — treat as stale")

    try:
        ts = _parse_ts(ts_raw)
    except ValueError:
        return _result(False, "UNPARSEABLE_APPROVAL_TIMESTAMP",
                       f"Cannot parse: {ts_raw}")

    now = datetime.now(timezone.utc)
    age = now - ts
    if age > timedelta(hours=APPROVAL_STALE_HOURS):
        return _result(False, "APPROVAL_STALE",
                       f"Approved {_fmt_age(age)} ago — rerun required (>{APPROVAL_STALE_HOURS}h)")

    approved_line = closure.get("approved_line")
    current_line  = closure.get("current_line")
    if approved_line is not None and current_line is not None:
        try:
            move = abs(float(current_line) - float(approved_line))
        except (TypeError, ValueError):
            move = None
        if move is not None and move >= LINE_MOVEMENT_THRESHOLD:
            return _result(False, "LINE_MOVED_SINCE_APPROVAL",
                           f"Line moved {move:.2f} since approval (>={LINE_MOVEMENT_THRESHOLD}) — rerun required")

    return _result(True, "APPROVAL_FRESH",
                   f"Age: {_fmt_age(age)}")


# ---------------------------------------------------------------------------
# 4. validate_edge_vs_friction
# ---------------------------------------------------------------------------
def validate_edge_vs_friction(closure: dict[str, Any]) -> dict[str, Any]:
    """
    edge_vs_friction must be POSITIVE for Power Play.
    UNKNOWN caps label ceiling at WATCH.
    NEGATIVE blocks FINAL_APPROVED.
    """
    evf       = closure.get("edge_vs_friction")
    slip_type = (closure.get("slip_type") or "NONE").upper()

    if evf is None or str(evf).upper() == "UNKNOWN":
        return _result(False, "EVF_UNKNOWN",
                       "edge_vs_friction UNKNOWN — capped at WATCH",
                       ceiling="WATCH")

    try:
        evf_float = float(evf)
    except (TypeError, ValueError):
        return _result(False, "EVF_UNPARSEABLE",
                       f"Cannot parse edge_vs_friction: {evf}",
                       ceiling="WATCH")

    if evf_float <= 0:
        msg = "edge_vs_friction is not POSITIVE"
        if slip_type == "POWER":
            return _result(False, "EVF_NOT_POSITIVE_POWER",
                           f"{msg} — Power Play requires positive edge_vs_friction")
        return _result(False, "EVF_NOT_POSITIVE",
                       msg, ceiling="MODEL_QUALIFIED_HOLD")

    return _result(True, "EVF_POSITIVE", f"edge_vs_friction={evf_float:.4f}")


# ---------------------------------------------------------------------------
# 5. validate_market_edge_confirmed
# ---------------------------------------------------------------------------
def validate_market_edge_confirmed(closure: dict[str, Any]) -> dict[str, Any]:
    """
    Model Qualified without market_edge_confirmed=True is not Power Play eligible.
    """
    confirmed = closure.get("market_edge_confirmed")
    slip_type = (closure.get("slip_type") or "NONE").upper()

    if slip_type == "POWER" and not confirmed:
        return _result(False, "MARKET_EDGE_NOT_CONFIRMED_POWER",
                       "Power Play requires market_edge_confirmed=True",
                       ceiling="MODEL_QUALIFIED_HOLD")

    if not confirmed:
        return _result(True, "MARKET_EDGE_UNCONFIRMED_FLEX",
                       "market_edge_confirmed=False — eligible for Flex only",
                       ceiling="MODEL_QUALIFIED_HOLD")

    return _result(True, "MARKET_EDGE_CONFIRMED")


# ---------------------------------------------------------------------------
# 6. validate_source_conflict
# ---------------------------------------------------------------------------
def validate_source_conflict(closure: dict[str, Any]) -> dict[str, Any]:
    """Source conflict blocks FINAL_APPROVED unconditionally."""
    conflicts = closure.get("unresolved_conflict_flags") or []
    provenance = str(closure.get("data_provenance") or "")

    source_conflict = any("SOURCE_CONFLICT" in str(f).upper() for f in conflicts)
    manual_engine   = any("MANUAL_ENGINE" in str(f).upper() for f in conflicts)
    provenance_conflict = "CONFLICT" in provenance.upper()

    if source_conflict or manual_engine or provenance_conflict:
        tags = [f for f in conflicts if f]
        if provenance_conflict and not tags:
            tags = [f"PROVENANCE:{provenance}"]
        return _result(False, "SOURCE_CONFLICT_BLOCKS_APPROVAL",
                       f"Unresolved conflicts: {tags}")

    return _result(True, "NO_SOURCE_CONFLICT")


# ---------------------------------------------------------------------------
# 7. validate_des_conflict_persistence
# ---------------------------------------------------------------------------
def validate_des_conflict_persistence(closure: dict[str, Any]) -> dict[str, Any]:
    """
    DES (Data-Edge-Status) conflicts persist across sessions.
    If a DES_CONFLICT tag is in unresolved_conflict_flags, it cannot be
    cleared without explicit resolution — treat as blocking.
    """
    flags = closure.get("unresolved_conflict_flags") or []
    des_flags = [f for f in flags if DES_CONFLICT_TAG in str(f).upper()]

    if des_flags:
        return _result(False, "DES_CONFLICT_PERSISTS",
                       f"Unresolved DES conflict(s): {des_flags} — "
                       "must be explicitly resolved before resubmission")

    return _result(True, "NO_DES_CONFLICT")


# ---------------------------------------------------------------------------
# 8. validate_power_play_eligibility
# ---------------------------------------------------------------------------
def validate_power_play_eligibility(closure: dict[str, Any]) -> dict[str, Any]:
    """
    All conditions must be met for Power Play eligibility:
      - slip_type == POWER
      - market_edge_confirmed == True
      - edge_vs_friction > 0
      - model_prob >= break_even(POWER) + safety_buffer
      - no source conflicts
      - structural_failure_count < MAX
    """
    slip_type = (closure.get("slip_type") or "NONE").upper()
    if slip_type != "POWER":
        return _result(True, "NOT_POWER_PLAY_SKIP",
                       "slip_type is not POWER — eligibility check skipped")

    blockers: list[str] = []

    if not closure.get("market_edge_confirmed"):
        blockers.append("MARKET_EDGE_NOT_CONFIRMED")

    evf = closure.get("edge_vs_friction")
    try:
        if evf is None or float(evf) <= 0:
            blockers.append("EVF_NOT_POSITIVE")
    except (TypeError, ValueError):
        blockers.append("EVF_UNPARSEABLE")

    model_prob = closure.get("model_prob")
    threshold  = BREAK_EVEN["POWER"] + SAFETY_BUFFER
    try:
        if model_prob is None or float(model_prob) < threshold:
            blockers.append(f"MODEL_PROB_BELOW_THRESHOLD:{model_prob}<{threshold:.3f}")
    except (TypeError, ValueError):
        blockers.append(f"MODEL_PROB_UNPARSEABLE:{model_prob}")

    failures = int(closure.get("structural_failure_count") or 0)
    if failures >= MAX_STRUCTURAL_FAILURES:
        blockers.append(f"STRUCTURAL_FAILURES:{failures}>={MAX_STRUCTURAL_FAILURES}")

    conflicts = closure.get("unresolved_conflict_flags") or []
    if conflicts:
        blockers.append(f"UNRESOLVED_CONFLICTS:{len(conflicts)}")

    if blockers:
        return _result(False, "POWER_PLAY_INELIGIBLE",
                       f"Blockers: {blockers}")

    return _result(True, "POWER_PLAY_ELIGIBLE")


# ---------------------------------------------------------------------------
# 9. validate_flex_eligibility
# ---------------------------------------------------------------------------
def validate_flex_eligibility(closure: dict[str, Any]) -> dict[str, Any]:
    """
    Flex requires:
      - model_prob >= break_even(FLEX) + safety_buffer
      - structural_failure_count < MAX
      - no DES conflict
    """
    slip_type = (closure.get("slip_type") or "NONE").upper()
    if slip_type not in ("FLEX", "NONE"):
        return _result(True, "NOT_FLEX_SKIP",
                       "slip_type is POWER — flex check skipped")

    blockers: list[str] = []

    model_prob = closure.get("model_prob")
    threshold  = BREAK_EVEN["FLEX"] + SAFETY_BUFFER
    try:
        if model_prob is None or float(model_prob) < threshold:
            blockers.append(f"MODEL_PROB_BELOW_FLEX_THRESHOLD:{model_prob}<{threshold:.3f}")
    except (TypeError, ValueError):
        blockers.append(f"MODEL_PROB_UNPARSEABLE:{model_prob}")

    failures = int(closure.get("structural_failure_count") or 0)
    if failures >= MAX_STRUCTURAL_FAILURES:
        blockers.append(f"STRUCTURAL_FAILURES:{failures}>={MAX_STRUCTURAL_FAILURES}")

    flags = closure.get("unresolved_conflict_flags") or []
    des = [f for f in flags if DES_CONFLICT_TAG in str(f).upper()]
    if des:
        blockers.append(f"DES_CONFLICT:{des}")

    if blockers:
        return _result(False, "FLEX_INELIGIBLE", f"Blockers: {blockers}")

    return _result(True, "FLEX_ELIGIBLE")


# ---------------------------------------------------------------------------
# 10. validate_narrative_first_flag
# ---------------------------------------------------------------------------
def validate_narrative_first_flag(closure: dict[str, Any]) -> dict[str, Any]:
    """
    Narrative-first props (primary_signal contains NARRATIVE) cannot pass
    without market_edge_confirmed=True. Without it, cap at MODEL_QUALIFIED_HOLD.
    """
    primary  = str(closure.get("primary_signal") or "").upper()
    is_narr  = "NARRATIVE" in primary or "STORY" in primary or "CONTEXT" in primary
    confirmed = bool(closure.get("market_edge_confirmed"))

    if is_narr and not confirmed:
        return _result(False, "NARRATIVE_FIRST_NO_MARKET_VERIFY",
                       "Narrative-first prop requires market_edge_confirmed=True",
                       ceiling="MODEL_QUALIFIED_HOLD")

    return _result(True, "NARRATIVE_GATE_OK",
                   f"narrative={is_narr} market_confirmed={confirmed}")


# ---------------------------------------------------------------------------
# 11. validate_structural_failure_count
# ---------------------------------------------------------------------------
def validate_structural_failure_count(closure: dict[str, Any]) -> dict[str, Any]:
    """3 or more structural failure paths kill the prop unconditionally."""
    try:
        count = int(closure.get("structural_failure_count") or 0)
    except (TypeError, ValueError):
        return _result(False, "STRUCTURAL_COUNT_UNPARSEABLE",
                       f"Cannot parse structural_failure_count: {closure.get('structural_failure_count')}")

    if count >= MAX_STRUCTURAL_FAILURES:
        return _result(False, "STRUCTURAL_FAILURES_KILL",
                       f"{count} structural failure paths >= {MAX_STRUCTURAL_FAILURES} — prop killed")

    return _result(True, "STRUCTURAL_COUNT_OK", f"failures={count}")


# ---------------------------------------------------------------------------
# Coin-flip kill guard
# ---------------------------------------------------------------------------
def validate_coin_flip_kill(closure: dict[str, Any],
                             prior_closure: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    If a prop was killed after a coin-flip (direction reversal on same player/prop),
    the opposite side must restart the full gate stack from scratch.
    Checks coin_flip_killed flag and compares direction vs prior direction.
    """
    killed = bool(closure.get("coin_flip_killed", False))
    if killed:
        return _result(False, "COIN_FLIP_KILLED_RESTART_REQUIRED",
                       "Prop killed after coin-flip direction conflict — "
                       "opposite side must restart full gate stack")

    if prior_closure:
        prior_dir   = str(prior_closure.get("direction") or "").upper()
        current_dir = str(closure.get("direction") or "").upper()
        prior_killed = bool(prior_closure.get("coin_flip_killed", False))

        if prior_killed and prior_dir and current_dir and prior_dir != current_dir:
            return _result(False, "OPPOSITE_SIDE_AFTER_COIN_FLIP_KILL",
                           f"Prior direction={prior_dir} was coin-flip killed — "
                           f"opposite side ({current_dir}) must restart full gate stack")

    return _result(True, "COIN_FLIP_OK")


# ---------------------------------------------------------------------------
# Full audit closure runner
# ---------------------------------------------------------------------------
ALL_VALIDATORS = [
    ("required_fields",         validate_required_fields),
    ("l5_line_used",            validate_l5_line_used),
    ("approval_staleness",      validate_approval_staleness),
    ("edge_vs_friction",        validate_edge_vs_friction),
    ("market_edge_confirmed",   validate_market_edge_confirmed),
    ("source_conflict",         validate_source_conflict),
    ("des_conflict_persistence",validate_des_conflict_persistence),
    ("power_play_eligibility",  validate_power_play_eligibility),
    ("flex_eligibility",        validate_flex_eligibility),
    ("narrative_first_flag",    validate_narrative_first_flag),
    ("structural_failure_count",validate_structural_failure_count),
]


def run_audit_closure(closure: dict[str, Any],
                      prior_closure: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Run all audit closure validators.
    Returns:
      {
        passed           bool
        results          dict  — per-validator result
        blockers         list[str]
        label_ceiling    str | None  — most restrictive ceiling across validators
        rerun_required   bool
      }
    """
    results: dict[str, Any] = {}
    blockers: list[str]     = []
    ceilings: list[str]     = []
    rerun_required = False

    for name, fn in ALL_VALIDATORS:
        r = fn(closure)
        results[name] = r
        if not r["passed"]:
            blockers.append(f"{name.upper()}:{r['code']}")
        if r.get("ceiling"):
            ceilings.append(r["ceiling"])
        if r.get("code") in ("APPROVAL_STALE", "LINE_MOVED_SINCE_APPROVAL"):
            rerun_required = True

    coin = validate_coin_flip_kill(closure, prior_closure)
    results["coin_flip_kill"] = coin
    if not coin["passed"]:
        blockers.append(f"COIN_FLIP_KILL:{coin['code']}")

    ceiling = _most_restrictive(ceilings)
    passed  = len(blockers) == 0

    return {
        "passed":        passed,
        "results":       results,
        "blockers":      blockers,
        "label_ceiling": ceiling,
        "rerun_required": rerun_required,
    }


def make_closure(**kwargs) -> dict[str, Any]:
    """
    Convenience constructor. Ensures all CLOSURE_FIELDS are present
    (defaulting missing optional ones to None).
    Extra keys are passed through.
    """
    out = {f: None for f in CLOSURE_FIELDS}
    out.update(kwargs)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_CEILING_ORDER = [
    "NO_PLAY", "RESEARCH_INTEREST", "WATCH",
    "MODEL_QUALIFIED_HOLD", "MARKET_VERIFIED_HOLD",
    "MONEY_QUALIFIED", "FINAL_APPROVED",
]


def _most_restrictive(ceilings: list[str]) -> str | None:
    if not ceilings:
        return None
    def rank(c: str) -> int:
        try:
            return _CEILING_ORDER.index(c)
        except ValueError:
            return len(_CEILING_ORDER)
    return min(ceilings, key=rank)


def _parse_ts(raw: str) -> datetime:
    raw = raw.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _fmt_age(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m = rem // 60
    return f"{h}h{m:02d}m"
