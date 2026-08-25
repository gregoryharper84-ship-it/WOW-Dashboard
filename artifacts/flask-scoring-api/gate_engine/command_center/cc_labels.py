"""
gate_engine/command_center/cc_labels.py
WOW Sports Intelligence Command Center — Phase 1

Namespaced CC labels, canonical market families, and monotonic ceiling order.

ALL labels prefixed CC: belong exclusively to the orchestration layer.
They are never emitted by downstream engines and are never erased by them.
can_execute = False (unconditional, permanent).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Governance constants
# ---------------------------------------------------------------------------

CAN_EXECUTE: bool = False          # unconditional — never changes
DRY_RUN_ONLY: bool = True
KALSHI_RECOVERY_MODE: str = "ACTIVE"

# ---------------------------------------------------------------------------
# Market families — each candidate routes to exactly ONE
# ---------------------------------------------------------------------------

FAMILY_PROP          = "PROP"
FAMILY_LLP           = "LLP"
FAMILY_KALSHI_SPORTS = "KALSHI_SPORTS"
FAMILY_KALSHI_WEATHER = "KALSHI_WEATHER"

ALL_FAMILIES: frozenset[str] = frozenset({
    FAMILY_PROP,
    FAMILY_LLP,
    FAMILY_KALSHI_SPORTS,
    FAMILY_KALSHI_WEATHER,
})

# ---------------------------------------------------------------------------
# Namespaced CC labels (orchestration layer — never emitted by engines)
# ---------------------------------------------------------------------------

# Intake
CC_INTAKE_INVALID            = "CC:INTAKE_INVALID"
CC_INTAKE_MISSING_FAMILY     = "CC:INTAKE_MISSING_FAMILY"
CC_INTAKE_MISSING_DATE       = "CC:INTAKE_MISSING_DATE"
CC_INTAKE_MISSING_IDENTITY   = "CC:INTAKE_MISSING_IDENTITY"

# Routing
CC_ROUTING_CONFLICT          = "CC:ROUTING_CONFLICT"
CC_ROUTING_UNRESOLVABLE      = "CC:ROUTING_UNRESOLVABLE"
CC_ROUTING_ASSIGNED          = "CC:ROUTING_ASSIGNED"

# Engine dispatch
CC_ENGINE_UNAVAILABLE        = "CC:ENGINE_UNAVAILABLE"
CC_ENGINE_RESULT_MISSING     = "CC:ENGINE_RESULT_MISSING"
CC_ENGINE_LABEL_INVALID      = "CC:ENGINE_LABEL_INVALID"

# Shared services
CC_SLATE_INTEGRITY_FAILED    = "CC:SLATE_INTEGRITY_FAILED"
CC_EXPOSURE_CONFLICT         = "CC:CROSS_PLATFORM_EXPOSURE_CONFLICT"
CC_FINAL_REFRESH_REQUIRED    = "CC:FINAL_REFRESH_REQUIRED"
CC_EXACT_LINE_MISMATCH       = "CC:EXACT_LINE_MISMATCH"
CC_SHARED_SERVICE_FAILED     = "CC:SHARED_SERVICE_FAILED"

# Ceiling enforcement
CC_CEILING_ENFORCED          = "CC:CEILING_ENFORCED"
CC_UPSTREAM_BLOCKER_PRESERVED = "CC:UPSTREAM_BLOCKER_PRESERVED"

# Kalshi isolation
CC_KALSHI_RECOVERY_CAP       = "CC:KALSHI_RECOVERY_MODE_CAP"
CC_KALSHI_CONTAMINATION_BLOCK = "CC:KALSHI_CROSS_ENGINE_CONTAMINATION_BLOCK"

# Reconciliation
CC_RECONCILIATION_PASSED     = "CC:RECONCILIATION_PASSED"
CC_RECONCILIATION_FAILED     = "CC:RECONCILIATION_FAILED"
CC_MISSING_FINAL_LABEL       = "CC:MISSING_FINAL_LABEL"
CC_MISSING_CAN_EXECUTE       = "CC:MISSING_CAN_EXECUTE_FIELD"
CC_CAN_EXECUTE_VIOLATION     = "CC:CAN_EXECUTE_VIOLATION"

# ---------------------------------------------------------------------------
# Monotonic ceiling order
# Index 0 = most permissive; higher index = more restrictive.
# The ceiling can only move RIGHT (to a higher index); never left.
# A downstream pass that would move the ceiling left is silently ignored
# and the upstream ceiling is preserved + CC:UPSTREAM_BLOCKER_PRESERVED
# is appended to the row's cc_blockers.
# ---------------------------------------------------------------------------

CEILING_ORDER: list[str] = [
    # ── Approval tier ─────────────────────────────────────────────────────
    "FINAL_APPROVED",
    "MONEY_QUALIFIED",
    # ── Verified / Hold tier ───────────────────────────────────────────────
    "MARKET_VERIFIED_HOLD",
    "MODEL_QUALIFIED_HOLD",
    "CALIBRATION_STALE_HOLD",
    "MARKET_QUALIFIED_BUT_SLIP_NEGATIVE",
    # ── Research / Watch tier ──────────────────────────────────────────────
    "RESEARCH_INTEREST",
    "FLIP_CANDIDATE",
    "HOUSE_RULES_CAUTION",
    "SERIES_STATE_CAUTION",
    "VARIANCE_INCREASE",
    # ── Sport-specific watch labels ────────────────────────────────────────
    "MLB_K_LESS_WATCH",
    "MLB_OUTS_MORE_HOLD",
    "WNBA_COMPOSITE_WATCH",
    "WNBA_COMPOSITE_SCOUT",
    "WNBA_COMPOSITE_MODEL_READY",
    # ── LLP labels ────────────────────────────────────────────────────────
    "LLP_SCOUT",
    "LLP_WATCH",
    "LLP_QUALIFIED_HOLD",
    "LLP_FINAL_APPROVED",
    # ── Kalshi labels ─────────────────────────────────────────────────────
    "KALSHI_SCOUT",
    "KALSHI_WATCH",
    "KALSHI_QUALIFIED_HOLD",
    "PORTFOLIO_QUALIFIED_HOLD",
    # ── Soft reject / Conflict tier ───────────────────────────────────────
    "SOURCE_CONFLICT",
    "COMPONENT_COMPOSITE_CONFLICT",
    "CROSS_BOOK_PARLAY_ILLUSION",
    "SAME_GAME_CORRELATED_STACK",
    "SETTLEMENT_SOURCE_CONFLICT",
    "PREDICTION_MARKET_SOURCE_CEILING",
    "RECONCILIATION_REQUIRED",
    "EXACT_LINE_AUDIT_REQUIRED",
    "LINE_ACTIVE_UNCONFIRMED",
    # ── Reject tier ───────────────────────────────────────────────────────
    "REJECT_NO_EDGE",
    "REJECT_BAD_STRUCTURE",
    "REJECT_DATA_QUALITY",
    "REJECT_SHARP_CONFLICT",
    "REJECT_FALLING_KNIFE",
    "REJECT_HOUSE_RULES_VULNERABILITY",
    "REJECT_EXECUTION_STALE",
    "REJECT_PAYOUT_CHANGED",
    "REJECT_LOW_LIQUIDITY",
    "REJECT_LINE_MOVED_AGAINST_SIDE",
    "REJECT_POWER_CORRELATED",
    "REJECT_MARKET_ADVERSE_THRESHOLD",
    "REJECT_MARKET_ADVERSE_PUSH_LOSS",
    "REJECT_CONTRADICTORY_ROLE_STATE",
    "REJECT_OPPORTUNITY_SUM_MISMATCH",
    "REJECT_EXACT_DUPLICATE",
    "REJECT_ALTERNATE_THRESHOLD_DUPLICATE",
    "REJECT_DUPLICATE_STRUCTURE",
    "REJECT_DUPLICATE_PITCHER_THESIS",
    "REJECT_COINFLIP",
    # ── Exposure / Duplicate block tier ───────────────────────────────────
    "DIRECTIONAL_EXPOSURE_BLOCK",
    "SESSION_DIRECTIONAL_EXPOSURE_BLOCK",
    "DUPLICATE_EXPOSURE_BLOCK",
    "REJECT_DUPLICATE_PLAYER_EXPOSURE",
    "REJECT_DUPLICATE_THESIS",
    "REJECT_CROSS_SLIP_CONCENTRATION",
    # ── Purge tier ────────────────────────────────────────────────────────
    "SLATE_PURGE",
    "WNBA_SLATE_PURGE",
    "MLB_WINNER_PREFLIGHT_BLOCK",
    # ── Hard reject tier ──────────────────────────────────────────────────
    "HARD_REJECT_COMBO_MULTIPLICATION",
    "HIGH_CONFIDENCE_SUSPENDED_CALIBRATION_FAILURE",
    "DEGRADED_ENGINE_RUN",
    "PIPELINE_INTEGRITY_FAILURE",
    "DATA_CONTRACT_FAIL",
    # ── Run-invalid tier ──────────────────────────────────────────────────
    "RUN_INVALID_GOVERNANCE_MISMATCH",
    "RUN_INVALID — ACQUISITION_INCOMPLETE",
    "INPUT_FAILURE — ACQUISITION_NOT_COMPLETED",
    # ── NO_PLAY ───────────────────────────────────────────────────────────
    "NO_PLAY",
    # ── CC-namespaced labels (always most restrictive) ─────────────────────
    CC_ENGINE_LABEL_INVALID,
    CC_ENGINE_RESULT_MISSING,
    CC_ENGINE_UNAVAILABLE,
    CC_SHARED_SERVICE_FAILED,
    CC_SLATE_INTEGRITY_FAILED,
    CC_EXPOSURE_CONFLICT,
    CC_FINAL_REFRESH_REQUIRED,
    CC_EXACT_LINE_MISMATCH,
    CC_KALSHI_RECOVERY_CAP,
    CC_KALSHI_CONTAMINATION_BLOCK,
    CC_ROUTING_UNRESOLVABLE,
    CC_ROUTING_CONFLICT,
    CC_INTAKE_INVALID,
    CC_INTAKE_MISSING_FAMILY,
    CC_INTAKE_MISSING_DATE,
    CC_INTAKE_MISSING_IDENTITY,
]

# Build O(1) rank lookup
_CEILING_RANK: dict[str, int] = {label: i for i, label in enumerate(CEILING_ORDER)}


def ceiling_rank(label: str | None) -> int:
    """
    Return the restrictiveness rank of a label (0=most permissive).
    Unknown labels are ranked as most permissive (0) so they don't erroneously
    enforce an unknown ceiling.  Callers should validate before calling.
    """
    if label is None:
        return -1  # None < any label (no ceiling set)
    return _CEILING_RANK.get(label, 0)


def is_cc_label(label: str | None) -> bool:
    """True if the label is in the CC: namespace."""
    return bool(label and label.startswith("CC:"))


def is_reject_label(label: str | None) -> bool:
    """True if the label is any reject/purge/hard-reject tier or CC blocker."""
    if not label:
        return False
    rank = ceiling_rank(label)
    reject_floor = ceiling_rank("REJECT_NO_EDGE")
    return rank >= reject_floor


def is_approval_label(label: str | None) -> bool:
    if not label:
        return False
    return label in {"FINAL_APPROVED", "MONEY_QUALIFIED"}
