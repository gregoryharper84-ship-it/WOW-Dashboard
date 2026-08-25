"""
typed_hydration.py — WOW-PATCH-2026-08-17-TYPED-HYDRATION-AND-MODEL-READINESS-V1

Typed lifecycle state machine and four-gate data-presence enforcement layer.

SCOPE: Player-prop markets only (PrizePicks props, NBA/WNBA/MLB/NFL/NHL).
  This module is NOT universal.  Moneylines, tennis totals, Kalshi weather
  contracts, and event-level (non-player) markets are out of scope.  Their
  controlling specialists and the Full Model Gatekeeper govern lane entry
  for those market families.  Fields such as player, lineup, L5/L10, and
  projected_minutes_or_workload are unconditional requirements because they
  are unconditionally required for the player-prop model contract.

Architectural invariant (per patch spec):
  Data acquisition failure must NEVER be presented as model judgment.
  lifecycle_state / data_status / model_status / failure_class are separate
  typed dimensions.  terminal_label remains a native WOW label (e.g.,
  DATA_CONTRACT_FAIL).  INCOMPLETE_INPUT, DATA_PROVIDER_OUTAGE, and STALE_DATA
  are typed data_status values — NOT replacements for native WOW terminal labels.

Lifecycle (state machine, forward-only):
  BOARD_EXTRACTED → DATA_HYDRATING
    → BLOCKED              (Gate 1/2/3 failure — or Gate 4 BLOCKING)
    → CONTRACT_COMPLETE → FOUR_GATES_CLEARED → MODEL_READY
                                                  → SCORING_ATOMIC → SCORED
                                                  → BLOCKED (calibration/write fail)

Gate 4 (Market/Settlement) is 3-way — only BLOCKING outcomes block MODEL_READY:
  AVAILABLE   — market data complete → all lanes (confidence + market-edge + money)
  UNAVAILABLE — market data absent/outage → confidence/model lane survives;
                market-edge and money lanes blocked; terminal ceiling lowered
  BLOCKING    — SOURCE_CONFLICT or STALE_DATA → row is BLOCKED entirely

Design rationale: under the reconstructed-confidence architecture and the Full
Model Gatekeeper contract, an exact two-way market is NOT required to run the
confidence/model lane.  Absent market evidence should reduce the ceiling, not
prevent the probability model from running.

Only MODEL_READY rows may be ranked, slipped, or entered into exposure ledgers.

Four data-presence gates (no probability / no analytical gate logic):
  1. Identity / Status Gate    — canonical identity, correct slate, active participant
  2. Role / Opportunity Gate   — minutes, workload, lineup slot, role certainty
  3. Historical-Ledger Gate    — raw L5/L10, hit rates, median, sample window, push rate
  4. Market / Settlement Gate  — 3-way (see above); TTL freshness; SOURCE_CONFLICT blocking

can_execute: False  (DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Module-level governance invariants (unconditional)
# ---------------------------------------------------------------------------

can_execute: bool = False
DRY_RUN_ONLY: str = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
PRODUCTION_AUTHORITY: bool = False
PATCH_ID: str = "WOW-PATCH-2026-08-17-TYPED-HYDRATION-AND-MODEL-READINESS-V1"

# New terminal label strings for this patch.
# Defined here (not in labels.py, which is a protected file) so that this
# module remains self-contained.  These follow the existing RUN_INVALID
# naming convention used in PropLabel.
LABEL_RUN_INVALID_HYDRATION_RECONCILIATION: str = (
    "RUN_INVALID — HYDRATION_RECONCILIATION_FAILURE"
)
LABEL_HYDRATION_ABORT: str = "RUN_INVALID — HYDRATION_ABORTED"

# Alert threshold (from build packet)
_ALERT_CONTRACT_FAILURE_RATE: float = 0.05
# Default systemic abort threshold — fraction of rows that must fail before hard abort
_DEFAULT_SYSTEMIC_THRESHOLD: float = 0.50


# ---------------------------------------------------------------------------
# Typed state dimensions (separate from terminal_label)
# ---------------------------------------------------------------------------

class LifecycleState(str, Enum):
    """Row lifecycle state.  Transitions are forward-only and validated."""
    BOARD_EXTRACTED    = "BOARD_EXTRACTED"
    DATA_HYDRATING     = "DATA_HYDRATING"
    CONTRACT_COMPLETE  = "CONTRACT_COMPLETE"
    FOUR_GATES_CLEARED = "FOUR_GATES_CLEARED"
    MODEL_READY        = "MODEL_READY"
    SCORING_ATOMIC     = "SCORING_ATOMIC"
    SCORED             = "SCORED"
    BLOCKED            = "BLOCKED"


class DataStatus(str, Enum):
    """
    Typed data status — typed acquisition state, NOT a terminal label.
    INCOMPLETE_INPUT / DATA_PROVIDER_OUTAGE / STALE_DATA describe why data
    is absent or unusable; the native WOW terminal_label records the outcome.
    """
    COMPLETE             = "COMPLETE"
    INCOMPLETE_INPUT     = "INCOMPLETE_INPUT"
    DATA_PROVIDER_OUTAGE = "DATA_PROVIDER_OUTAGE"
    STALE_DATA           = "STALE_DATA"
    SOURCE_CONFLICT      = "SOURCE_CONFLICT"


class ModelStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    READY       = "READY"
    RUNNING     = "RUNNING"
    COMPLETED   = "COMPLETED"
    FAILED      = "FAILED"


class FailureClass(str, Enum):
    INPUT_FAILURE     = "INPUT_FAILURE"
    PROVIDER_FAILURE  = "PROVIDER_FAILURE"
    FRESHNESS_FAILURE = "FRESHNESS_FAILURE"
    CONFLICT_FAILURE  = "CONFLICT_FAILURE"
    MODEL_FAILURE     = "MODEL_FAILURE"
    NONE              = "NONE"


class MarketGateOutcome(str, Enum):
    """
    3-way outcome for Gate 4 (Market / Settlement).

    Only BLOCKING outcomes prevent MODEL_READY.  UNAVAILABLE allows the
    confidence/model lane to proceed with a reduced terminal ceiling —
    consistent with the Full Model Gatekeeper contract and the reconstructed-
    confidence architecture, which do not require an exact two-way market to
    run the probability model.

      AVAILABLE   — all market data present; all lanes open
      UNAVAILABLE — market data absent or provider outage; confidence/model
                    lane survives; market-edge and money lanes are blocked;
                    terminal ceiling is lowered (MODEL_QUALIFIED_HOLD max)
      BLOCKING    — SOURCE_CONFLICT or STALE_DATA; row is BLOCKED entirely
    """
    AVAILABLE   = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKING    = "BLOCKING"


# ---------------------------------------------------------------------------
# State machine — valid forward transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.BOARD_EXTRACTED:    frozenset({LifecycleState.DATA_HYDRATING,
                                                  LifecycleState.BLOCKED}),
    LifecycleState.DATA_HYDRATING:     frozenset({LifecycleState.CONTRACT_COMPLETE,
                                                  LifecycleState.BLOCKED}),
    LifecycleState.CONTRACT_COMPLETE:  frozenset({LifecycleState.FOUR_GATES_CLEARED,
                                                  LifecycleState.BLOCKED}),
    LifecycleState.FOUR_GATES_CLEARED: frozenset({LifecycleState.MODEL_READY,
                                                  LifecycleState.BLOCKED}),
    LifecycleState.MODEL_READY:        frozenset({LifecycleState.SCORING_ATOMIC,
                                                  LifecycleState.BLOCKED}),
    LifecycleState.SCORING_ATOMIC:     frozenset({LifecycleState.SCORED,
                                                  LifecycleState.BLOCKED}),
    LifecycleState.SCORED:             frozenset(),   # terminal
    LifecycleState.BLOCKED:            frozenset(),   # terminal
}

_TERMINAL_STATES: frozenset[LifecycleState] = frozenset({
    LifecycleState.SCORED,
    LifecycleState.BLOCKED,
})


def _validate_transition(
    current: LifecycleState,
    target: LifecycleState,
) -> None:
    """
    Enforce forward-only lifecycle transitions.  Raises ValueError on any
    invalid or backward transition (fail-closed — unknown transitions are
    rejected, not silently allowed).
    """
    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        allowed_str = sorted(s.value for s in allowed)
        raise ValueError(
            f"Invalid lifecycle transition: {current.value!r} → {target.value!r}. "
            f"Allowed from {current.value!r}: "
            f"{allowed_str if allowed_str else ['none (terminal state)']}"
        )


# ---------------------------------------------------------------------------
# Gate identifiers
# ---------------------------------------------------------------------------

GATE_IDENTITY: str = "identity_status_gate"
GATE_ROLE:     str = "role_opportunity_gate"
GATE_LEDGER:   str = "historical_ledger_gate"
GATE_MARKET:   str = "market_settlement_gate"
ALL_GATES:     tuple[str, ...] = (GATE_IDENTITY, GATE_ROLE, GATE_LEDGER, GATE_MARKET)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FourGateResult:
    gate_id:             str
    passed:              bool
    missing_fields:      list[str]
    data_status:         DataStatus
    failure_class:       FailureClass
    failure_reason:      str
    # Gate 4 only — None for Gates 1/2/3
    market_gate_outcome: "MarketGateOutcome | None" = None


@dataclass
class HydrationResult:
    """
    Single-row typed hydration result.

    All four state dimensions (lifecycle_state / data_status / model_status /
    failure_class) are separate from terminal_label, which remains a native
    WOW label (e.g., DATA_CONTRACT_FAIL for blocked rows, "" for MODEL_READY
    rows pending scoring).

    Gate 4 (Market/Settlement) is 3-way — see MarketGateOutcome.  A row may
    reach MODEL_READY even when market data is absent (UNAVAILABLE outcome),
    in which case:
      confidence_lane_available = True   (probability model may run)
      market_lane_available     = False  (market-edge/money lanes blocked)
    This is consistent with the Full Model Gatekeeper contract: absent market
    evidence lowers the terminal ceiling; it does not prevent model execution.
    """
    row_id:          str
    lifecycle_state: LifecycleState
    data_status:     DataStatus
    model_status:    ModelStatus
    failure_class:   FailureClass
    terminal_label:  str                    # native WOW label — never a DataStatus value
    gate_results:    dict[str, FourGateResult] = field(default_factory=dict)
    gates_passed:    int = 0
    gates_failed:    int = 0
    missing_fields:  list[str] = field(default_factory=list)
    failure_summary: str = ""
    # Gate 4 market-lane separation (player-prop scope)
    market_gate_outcome:     "MarketGateOutcome"     = MarketGateOutcome.AVAILABLE
    market_lane_available:   bool                    = True   # market-edge / money
    confidence_lane_available: bool                  = True   # probability model
    # Acquisition provenance
    provider_attempts: list[dict[str, Any]] = field(default_factory=list)
    fallback_sources:  list[str] = field(default_factory=list)


@dataclass
class RunControllerResult:
    hard_abort:                  bool
    abort_reason:                str | None
    run_status:                  str            # "OK" | "DEGRADED" | "ABORTED"
    contract_complete_count:     int
    blocked_count:               int
    rows_extracted:              int
    alert_contract_failure_rate: float          # configured threshold (0.05)
    actual_failure_rate:         float          # observed rate this run
    model_ready_row_ids:         list[str]      # only MODEL_READY rows
    blocked_row_ids:             list[str]      # excluded from rankings/slips/exposure
    reconciliation:              dict[str, Any]
    all_rows_provider_outage:    bool
    systemic_threshold_exceeded: bool


# ---------------------------------------------------------------------------
# Severity ordering (worst-wins across multiple gate failures)
# ---------------------------------------------------------------------------

_DATA_STATUS_SEVERITY: dict[DataStatus, int] = {
    DataStatus.COMPLETE:             0,
    DataStatus.INCOMPLETE_INPUT:     1,
    DataStatus.STALE_DATA:           2,
    DataStatus.SOURCE_CONFLICT:      3,
    DataStatus.DATA_PROVIDER_OUTAGE: 4,
}

_FAILURE_CLASS_SEVERITY: dict[FailureClass, int] = {
    FailureClass.NONE:             0,
    FailureClass.INPUT_FAILURE:    1,
    FailureClass.FRESHNESS_FAILURE: 2,
    FailureClass.CONFLICT_FAILURE:  3,
    FailureClass.PROVIDER_FAILURE:  4,
    FailureClass.MODEL_FAILURE:     5,
}


def _worst_data_status(failed: list[FourGateResult]) -> DataStatus:
    return max(failed, key=lambda r: _DATA_STATUS_SEVERITY[r.data_status]).data_status


def _worst_failure_class(failed: list[FourGateResult]) -> FailureClass:
    return max(failed, key=lambda r: _FAILURE_CLASS_SEVERITY[r.failure_class]).failure_class


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_absent(value: Any) -> bool:
    """True when a value is considered missing (None, empty string, empty list)."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


# ---------------------------------------------------------------------------
# Gate 1 — Identity / Status Gate
# ---------------------------------------------------------------------------

def _run_identity_gate(
    row: dict[str, Any],
    enrichment: dict[str, Any],
) -> FourGateResult:
    """
    Gate 1 — Identity / Status Gate (data presence only).

    Required row fields: player, sport, prop_type, line, side, event_id,
      event_date, source
    Required enrichment fields: participant_status, lineupConfirmed,
      status_checked_at
    """
    row_required = [
        "player",
        "sport",
        "prop_type",
        "line",
        "side",
        "event_id",
        "event_date",
        "source",
    ]
    enr_required = [
        "participant_status",
        "lineupConfirmed",
        "status_checked_at",
    ]

    missing: list[str] = []
    for f in row_required:
        if _is_absent(row.get(f)):
            missing.append(f)
    for f in enr_required:
        if _is_absent(enrichment.get(f)):
            missing.append(f)

    # Provider outage wins over missing-field classification
    raw_ds = str(row.get("data_status") or enrichment.get("data_status") or "").upper()
    if raw_ds == DataStatus.DATA_PROVIDER_OUTAGE.value:
        return FourGateResult(
            gate_id=GATE_IDENTITY,
            passed=False,
            missing_fields=missing,
            data_status=DataStatus.DATA_PROVIDER_OUTAGE,
            failure_class=FailureClass.PROVIDER_FAILURE,
            failure_reason="Provider outage flagged on identity/status data",
        )

    if missing:
        return FourGateResult(
            gate_id=GATE_IDENTITY,
            passed=False,
            missing_fields=missing,
            data_status=DataStatus.INCOMPLETE_INPUT,
            failure_class=FailureClass.INPUT_FAILURE,
            failure_reason=f"Missing identity/status fields: {missing}",
        )

    return FourGateResult(
        gate_id=GATE_IDENTITY,
        passed=True,
        missing_fields=[],
        data_status=DataStatus.COMPLETE,
        failure_class=FailureClass.NONE,
        failure_reason="",
    )


# ---------------------------------------------------------------------------
# Gate 2 — Role / Opportunity Gate
# ---------------------------------------------------------------------------

def _run_role_gate(
    row: dict[str, Any],      # noqa: ARG001
    enrichment: dict[str, Any],
) -> FourGateResult:
    """
    Gate 2 — Role / Opportunity Gate (data presence only).

    Required enrichment fields: role, projected_minutes_or_workload,
      role_checked_at, role_source
    """
    enr_required = [
        "role",
        "projected_minutes_or_workload",
        "role_checked_at",
        "role_source",
    ]
    missing: list[str] = []
    for f in enr_required:
        if _is_absent(enrichment.get(f)):
            missing.append(f)

    if missing:
        return FourGateResult(
            gate_id=GATE_ROLE,
            passed=False,
            missing_fields=missing,
            data_status=DataStatus.INCOMPLETE_INPUT,
            failure_class=FailureClass.INPUT_FAILURE,
            failure_reason=f"Missing role/opportunity fields: {missing}",
        )

    return FourGateResult(
        gate_id=GATE_ROLE,
        passed=True,
        missing_fields=[],
        data_status=DataStatus.COMPLETE,
        failure_class=FailureClass.NONE,
        failure_reason="",
    )


# ---------------------------------------------------------------------------
# Gate 3 — Historical-Ledger Gate
# ---------------------------------------------------------------------------

def _run_ledger_gate(
    row: dict[str, Any],      # noqa: ARG001
    enrichment: dict[str, Any],
) -> FourGateResult:
    """
    Gate 3 — Historical-Ledger Gate (data presence only).

    Required enrichment fields: l5_values (non-empty), l10_values (non-empty),
      l5_line_used, l10_median, l10_mean, role_timestamp
    """
    enr_required = [
        "l5_values",
        "l10_values",
        "l5_line_used",
        "l10_median",
        "l10_mean",
        "role_timestamp",
    ]
    missing: list[str] = []
    for f in enr_required:
        if _is_absent(enrichment.get(f)):
            missing.append(f)

    if missing:
        return FourGateResult(
            gate_id=GATE_LEDGER,
            passed=False,
            missing_fields=missing,
            data_status=DataStatus.INCOMPLETE_INPUT,
            failure_class=FailureClass.INPUT_FAILURE,
            failure_reason=f"Missing historical-ledger fields: {missing}",
        )

    return FourGateResult(
        gate_id=GATE_LEDGER,
        passed=True,
        missing_fields=[],
        data_status=DataStatus.COMPLETE,
        failure_class=FailureClass.NONE,
        failure_reason="",
    )


# ---------------------------------------------------------------------------
# Gate 4 — Market / Settlement Gate
# ---------------------------------------------------------------------------

def _run_market_gate(
    row: dict[str, Any],      # noqa: ARG001
    enrichment: dict[str, Any],
    now: datetime | None = None,
) -> FourGateResult:
    """
    Gate 4 — Market / Settlement Gate (data presence + TTL freshness).

    Three distinct outcome classes (MarketGateOutcome):

    BLOCKING  — SOURCE_CONFLICT, expired TTL, or missing general provenance
                field (data_timestamp).  Row is fully BLOCKED.  data_timestamp
                is a general intake field stamped by auto_enrichment; its
                absence is an intake failure, not market-data absence.

    UNAVAILABLE — market_no_vig_probability absent.  Confidence/model lane
                  survives; market-edge and money lanes blocked; ceiling capped
                  at MODEL_QUALIFIED_HOLD.  data_timestamp MUST be present
                  before this path is reachable (checked above).

    AVAILABLE — all market-specific fields present and fresh; all lanes open.

    TTL check: if market_checked_at + market_ttl (seconds) < now → BLOCKING.
    Expired TTL cannot be refreshed merely by reusing the old value.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # SOURCE_CONFLICT → BLOCKING (row fully blocked)
    no_vig = str(enrichment.get("market_no_vig_probability") or "").strip().upper()
    if no_vig == "SOURCE_CONFLICT":
        return FourGateResult(
            gate_id=GATE_MARKET,
            passed=False,
            missing_fields=[],
            data_status=DataStatus.SOURCE_CONFLICT,
            failure_class=FailureClass.CONFLICT_FAILURE,
            failure_reason="market_no_vig_probability=SOURCE_CONFLICT across sources",
            market_gate_outcome=MarketGateOutcome.BLOCKING,
        )

    # TTL freshness check — runs before field-presence check.
    # Expired TTL → BLOCKING (cannot refresh by reusing the old value).
    market_checked_at = enrichment.get("market_checked_at")
    market_ttl        = enrichment.get("market_ttl")
    if market_checked_at is not None and market_ttl is not None:
        try:
            if isinstance(market_checked_at, str):
                market_checked_at = datetime.fromisoformat(
                    market_checked_at.replace("Z", "+00:00")
                )
            ttl_seconds = float(market_ttl)
            age = (now - market_checked_at).total_seconds()
            if age > ttl_seconds:
                return FourGateResult(
                    gate_id=GATE_MARKET,
                    passed=False,
                    missing_fields=[],
                    data_status=DataStatus.STALE_DATA,
                    failure_class=FailureClass.FRESHNESS_FAILURE,
                    failure_reason=(
                        f"Market data TTL expired (age={age:.0f}s > ttl={ttl_seconds:.0f}s). "
                        "Expired TTL cannot be refreshed by reusing the old value."
                    ),
                    market_gate_outcome=MarketGateOutcome.BLOCKING,
                )
        except (ValueError, TypeError, AttributeError):
            pass  # malformed timestamp — fall through to field presence

    # ── General intake provenance check (BLOCKING) ──────────────────────────
    # data_timestamp is stamped by auto_enrichment for every acquired row and
    # listed as required in data_contract.py.  It is a general acquisition
    # field, NOT market-specific; its absence indicates an intake-level failure,
    # not market-data unavailability.  Absence → BLOCKING (not UNAVAILABLE).
    _provenance_required = ["data_timestamp"]
    for _f in _provenance_required:
        _v = enrichment.get(_f)
        if _v is None or (isinstance(_v, str) and not _v.strip()):
            return FourGateResult(
                gate_id=GATE_MARKET,
                passed=False,
                missing_fields=[_f],
                data_status=DataStatus.INCOMPLETE_INPUT,
                failure_class=FailureClass.INPUT_FAILURE,
                failure_reason=(
                    f"Missing intake provenance field {_f!r} — row BLOCKED. "
                    "data_timestamp is stamped by auto_enrichment on every acquired "
                    "row; its absence indicates a row-level intake failure, not "
                    "market-data unavailability."
                ),
                market_gate_outcome=MarketGateOutcome.BLOCKING,
            )

    # ── Market-specific fields (UNAVAILABLE if absent) ───────────────────────
    # market_no_vig_probability is market-specific (the two-way no-vig line).
    # Absent market odds → confidence/model lane survives with a lower ceiling;
    # market-edge and money lanes are blocked.
    _market_specific = ["market_no_vig_probability"]
    missing: list[str] = []
    for f in _market_specific:
        v = enrichment.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(f)

    if missing:
        return FourGateResult(
            gate_id=GATE_MARKET,
            passed=False,
            missing_fields=missing,
            data_status=DataStatus.INCOMPLETE_INPUT,
            failure_class=FailureClass.INPUT_FAILURE,
            failure_reason=(
                f"Market data unavailable — market-specific fields absent: {missing}. "
                "Confidence/model lane survives under ceiling (MODEL_QUALIFIED_HOLD max); "
                "market-edge and money lanes blocked."
            ),
            market_gate_outcome=MarketGateOutcome.UNAVAILABLE,
        )

    return FourGateResult(
        gate_id=GATE_MARKET,
        passed=True,
        missing_fields=[],
        data_status=DataStatus.COMPLETE,
        failure_class=FailureClass.NONE,
        failure_reason="",
        market_gate_outcome=MarketGateOutcome.AVAILABLE,
    )


# ---------------------------------------------------------------------------
# Main hydration check
# ---------------------------------------------------------------------------

def run_hydration_check(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
    now: datetime | None = None,
    provider_attempts: list[dict[str, Any]] | None = None,
    fallback_sources: list[str] | None = None,
) -> HydrationResult:
    """
    Run all four data-presence gates against a single prop row.

    Lifecycle: BOARD_EXTRACTED → DATA_HYDRATING → (CONTRACT_COMPLETE →
    FOUR_GATES_CLEARED → MODEL_READY) | BLOCKED

    Fail-closed: any gate failure → BLOCKED / DATA_CONTRACT_FAIL.
    terminal_label is a native WOW label — never a DataStatus value.
    MODEL_REJECTED is structurally impossible unless the row previously
    reached MODEL_READY (enforced: blocked rows cannot advance to SCORING_ATOMIC).

    Args:
        row:                Normalized prop row dict (post board_intake).
        enrichment:         Per-row enrichment dict.
        now:                Reference time for TTL checks (default: utcnow).
        provider_attempts:  Provenance record — all fetch attempts including
                            failures — for fallback audit trail.
        fallback_sources:   Fallback providers actually tried (for test 18).

    Returns:
        HydrationResult
    """
    if enrichment is None:
        enrichment = {}
    if now is None:
        now = datetime.now(timezone.utc)

    row_id = str(row.get("row_id") or row.get("id") or "unknown")

    # Validate the first transition (BOARD_EXTRACTED → DATA_HYDRATING)
    # This catches any caller that skips the correct entry state.
    _validate_transition(LifecycleState.BOARD_EXTRACTED, LifecycleState.DATA_HYDRATING)

    # Run all four gates
    g1 = _run_identity_gate(row, enrichment)
    g2 = _run_role_gate(row, enrichment)
    g3 = _run_ledger_gate(row, enrichment)
    g4 = _run_market_gate(row, enrichment, now=now)

    gate_results = {
        GATE_IDENTITY: g1,
        GATE_ROLE:     g2,
        GATE_LEDGER:   g3,
        GATE_MARKET:   g4,
    }

    # Gate 4 uses a 3-way outcome.  Only BLOCKING prevents MODEL_READY.
    # UNAVAILABLE (missing market data / provider outage on market) allows
    # the confidence/model lane while blocking market-edge and money lanes.
    g4_outcome: MarketGateOutcome = (
        g4.market_gate_outcome
        if g4.market_gate_outcome is not None
        else MarketGateOutcome.AVAILABLE
    )

    # Hard-blocking failures: Gates 1/2/3 failures + Gate 4 BLOCKING
    hard_blocking = [g for g in [g1, g2, g3] if not g.passed]
    if g4_outcome == MarketGateOutcome.BLOCKING:
        hard_blocking.append(g4)
    market_soft_fail = (not g4.passed) and g4_outcome == MarketGateOutcome.UNAVAILABLE

    # Counts for the reconciliation equations
    failed_gates = [g for g in gate_results.values() if not g.passed]
    passed_count = sum(1 for g in gate_results.values() if g.passed)
    all_missing  = [f for g in hard_blocking for f in g.missing_fields]
    if market_soft_fail and g4.missing_fields:
        all_missing.extend(g4.missing_fields)

    if hard_blocking:
        # Fail-closed: hard blocking failures → BLOCKED
        _validate_transition(LifecycleState.DATA_HYDRATING, LifecycleState.BLOCKED)
        worst_ds = _worst_data_status(hard_blocking)
        worst_fc = _worst_failure_class(hard_blocking)
        summary  = "; ".join(g.failure_reason for g in hard_blocking if g.failure_reason)

        return HydrationResult(
            row_id=row_id,
            lifecycle_state=LifecycleState.BLOCKED,
            data_status=worst_ds,
            model_status=ModelStatus.NOT_STARTED,
            failure_class=worst_fc,
            terminal_label=PropLabel.DATA_CONTRACT_FAIL.value,
            gate_results=gate_results,
            gates_passed=passed_count,
            gates_failed=len(failed_gates),
            missing_fields=all_missing,
            failure_summary=summary,
            market_gate_outcome=g4_outcome,
            market_lane_available=False,
            confidence_lane_available=False,
            provider_attempts=list(provider_attempts or []),
            fallback_sources=list(fallback_sources or []),
        )

    # Gates 1/2/3 all passed; Gate 4 is AVAILABLE or UNAVAILABLE.
    # Row reaches MODEL_READY in both cases.
    _validate_transition(LifecycleState.DATA_HYDRATING,     LifecycleState.CONTRACT_COMPLETE)
    _validate_transition(LifecycleState.CONTRACT_COMPLETE,  LifecycleState.FOUR_GATES_CLEARED)
    _validate_transition(LifecycleState.FOUR_GATES_CLEARED, LifecycleState.MODEL_READY)

    market_lane_ok = (g4_outcome == MarketGateOutcome.AVAILABLE)
    soft_summary   = g4.failure_reason if market_soft_fail else ""

    return HydrationResult(
        row_id=row_id,
        lifecycle_state=LifecycleState.MODEL_READY,
        data_status=DataStatus.COMPLETE if market_lane_ok else DataStatus.INCOMPLETE_INPUT,
        model_status=ModelStatus.READY,
        failure_class=FailureClass.NONE,
        terminal_label="",      # cleared at MODEL_READY; set by scoring after SCORED
        gate_results=gate_results,
        gates_passed=passed_count,
        gates_failed=len(failed_gates),
        missing_fields=list(g4.missing_fields) if market_soft_fail else [],
        failure_summary=soft_summary,
        market_gate_outcome=g4_outcome,
        market_lane_available=market_lane_ok,
        confidence_lane_available=True,   # Gates 1/2/3 passed
        provider_attempts=list(provider_attempts or []),
        fallback_sources=list(fallback_sources or []),
    )


# ---------------------------------------------------------------------------
# Lifecycle advancement helper (used after scoring)
# ---------------------------------------------------------------------------

def advance_lifecycle(
    result: HydrationResult,
    to: LifecycleState,
) -> HydrationResult:
    """
    Advance a HydrationResult to the next lifecycle state.
    Validates the transition — state cannot move backward after scoring.
    Fail-closed: invalid transitions raise ValueError.
    MODEL_REJECTED (conceptually: BLOCKED from SCORING_ATOMIC) is only
    reachable if the row previously reached MODEL_READY.
    """
    _validate_transition(result.lifecycle_state, to)
    return dataclasses.replace(result, lifecycle_state=to)


# ---------------------------------------------------------------------------
# Row reconciliation
# ---------------------------------------------------------------------------

def reconcile_run(
    results: list[HydrationResult],
    scored_row_ids: list[str] | None = None,
    model_failed_row_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Verify exact row reconciliation equations.

    Equations (from build packet):
      rows_extracted = rows_hydrating + rows_blocked_before_hydration
      rows_hydrating = rows_contract_complete + rows_hydration_failed
      rows_contract_complete = rows_model_ready + rows_gate_blocked
      rows_model_ready = rows_scored + rows_model_failed
      Every row terminates exactly once (no duplicate row_ids).

    Returns dict with 'valid' bool and 'run_status'.
    If any equation fails → valid=False, run_status='RUN_INVALID'.
    """
    if scored_row_ids is None:
        scored_row_ids = []
    if model_failed_row_ids is None:
        model_failed_row_ids = []

    rows_extracted = len(results)

    # "Blocked before hydration" = rows that arrived already blocked with
    # no gate having run (gates_passed + gates_failed == 0).
    rows_blocked_before_hydration = sum(
        1 for r in results
        if r.lifecycle_state == LifecycleState.BLOCKED
        and r.gates_passed == 0
        and r.gates_failed == 0
    )
    rows_hydrating = rows_extracted - rows_blocked_before_hydration

    rows_hydration_failed = sum(
        1 for r in results
        if r.lifecycle_state == LifecycleState.BLOCKED
        and r.gates_failed > 0
    )
    rows_contract_complete = rows_hydrating - rows_hydration_failed

    rows_gate_blocked = sum(
        1 for r in results
        if r.lifecycle_state == LifecycleState.FOUR_GATES_CLEARED
        # stuck at FOUR_GATES_CLEARED = gate blocked before MODEL_READY
    )
    rows_model_ready = sum(
        1 for r in results
        if r.lifecycle_state in (
            LifecycleState.MODEL_READY,
            LifecycleState.SCORING_ATOMIC,
            LifecycleState.SCORED,
        )
    )
    rows_scored       = len(scored_row_ids)
    rows_model_failed = len(model_failed_row_ids)

    # No-duplicate check
    all_ids    = [r.row_id for r in results]
    unique_ids = len(set(all_ids))
    no_duplicates = (unique_ids == rows_extracted)

    # Equation checks
    eq1 = rows_extracted == rows_hydrating + rows_blocked_before_hydration
    eq2 = rows_hydrating == rows_contract_complete + rows_hydration_failed
    eq3 = rows_contract_complete == rows_model_ready + rows_gate_blocked
    scoring_phase_given = bool(scored_row_ids or model_failed_row_ids)
    eq4 = (
        rows_model_ready == rows_scored + rows_model_failed
    ) if scoring_phase_given else True

    valid = eq1 and eq2 and eq3 and eq4 and no_duplicates

    equations_failed: list[str] = []
    if not eq1:
        equations_failed.append(
            f"EQ1: extracted={rows_extracted} != "
            f"hydrating({rows_hydrating}) + blocked_before({rows_blocked_before_hydration})"
        )
    if not eq2:
        equations_failed.append(
            f"EQ2: hydrating={rows_hydrating} != "
            f"contract_complete({rows_contract_complete}) + hydration_failed({rows_hydration_failed})"
        )
    if not eq3:
        equations_failed.append(
            f"EQ3: contract_complete={rows_contract_complete} != "
            f"model_ready({rows_model_ready}) + gate_blocked({rows_gate_blocked})"
        )
    if not eq4:
        equations_failed.append(
            f"EQ4: model_ready={rows_model_ready} != "
            f"scored({rows_scored}) + model_failed({rows_model_failed})"
        )
    if not no_duplicates:
        equations_failed.append(
            f"DEDUP: {rows_extracted} rows but only {unique_ids} unique row_ids"
        )

    terminal = LABEL_RUN_INVALID_HYDRATION_RECONCILIATION if not valid else ""

    return {
        "valid":                         valid,
        "run_status":                    "OK" if valid else "RUN_INVALID",
        "terminal_label":                terminal,
        "rows_extracted":                rows_extracted,
        "rows_hydrating":                rows_hydrating,
        "rows_blocked_before_hydration": rows_blocked_before_hydration,
        "rows_contract_complete":        rows_contract_complete,
        "rows_hydration_failed":         rows_hydration_failed,
        "rows_gate_blocked":             rows_gate_blocked,
        "rows_model_ready":              rows_model_ready,
        "rows_scored":                   rows_scored,
        "rows_model_failed":             rows_model_failed,
        "equations_failed":              equations_failed,
    }


# ---------------------------------------------------------------------------
# Run controller
# ---------------------------------------------------------------------------

def run_controller(
    results: list[HydrationResult],
    systemic_threshold: float = _DEFAULT_SYSTEMIC_THRESHOLD,
) -> RunControllerResult:
    """
    Evaluate run health after hydration and enforce hard-abort conditions.

    Hard-abort when (ANY of):
      1. contract_complete_count == 0  (no rows cleared all four data gates)
      2. All rows share a required-provider outage
      3. Systemic threshold exceeded (failure_rate > systemic_threshold)

    Alert-only (not hard-abort): actual_failure_rate > 0.05.
    Blocked rows are excluded from model_ready_row_ids so callers cannot
    accidentally rank, slip, or enter them into exposure ledgers.

    Partial failure behavior:
      - Preserve complete rows (model_ready_row_ids).
      - Isolate blocked rows (blocked_row_ids).
      - Mark run DEGRADED (not ABORTED) for partial failures.
      - Never rank blocked rows.
      - Prevent final-card publication when contract_complete_count == 0.
    """
    rows_extracted          = len(results)
    model_ready_results     = [
        r for r in results if r.lifecycle_state == LifecycleState.MODEL_READY
    ]
    blocked_results         = [
        r for r in results if r.lifecycle_state == LifecycleState.BLOCKED
    ]
    contract_complete_count = len(model_ready_results)
    blocked_count           = len(blocked_results)

    actual_failure_rate = blocked_count / rows_extracted if rows_extracted else 0.0

    all_rows_provider_outage = bool(results) and all(
        r.data_status == DataStatus.DATA_PROVIDER_OUTAGE for r in results
    )
    systemic_threshold_exceeded = actual_failure_rate > systemic_threshold

    # Hard-abort evaluation (fail-closed)
    hard_abort   = False
    abort_reason: str | None = None

    if contract_complete_count == 0:
        hard_abort   = True
        abort_reason = (
            "contract_complete_count=0: no rows cleared all four data-presence gates. "
            "Final-card publication blocked."
        )
    elif all_rows_provider_outage:
        hard_abort   = True
        abort_reason = "All rows share a required-provider outage (DATA_PROVIDER_OUTAGE)."
    elif systemic_threshold_exceeded:
        hard_abort   = True
        abort_reason = (
            f"Systemic hydration threshold exceeded: "
            f"failure_rate={actual_failure_rate:.1%} > threshold={systemic_threshold:.1%}."
        )

    if hard_abort:
        run_status = "ABORTED"
    elif blocked_count > 0:
        run_status = "DEGRADED"
    else:
        run_status = "OK"

    return RunControllerResult(
        hard_abort=hard_abort,
        abort_reason=abort_reason,
        run_status=run_status,
        contract_complete_count=contract_complete_count,
        blocked_count=blocked_count,
        rows_extracted=rows_extracted,
        alert_contract_failure_rate=_ALERT_CONTRACT_FAILURE_RATE,
        actual_failure_rate=actual_failure_rate,
        model_ready_row_ids=[r.row_id for r in model_ready_results],
        blocked_row_ids=[r.row_id for r in blocked_results],
        reconciliation={},      # populated by reconcile_run() if caller needs it
        all_rows_provider_outage=all_rows_provider_outage,
        systemic_threshold_exceeded=systemic_threshold_exceeded,
    )


# ---------------------------------------------------------------------------
# Market ceiling enforcement
# ---------------------------------------------------------------------------

# Labels that require market_lane_available=True.
# Rows with market_lane_available=False must never reach these labels.
MARKET_REQUIRED_LABELS: frozenset[str] = frozenset({
    PropLabel.MONEY_QUALIFIED.value,
    PropLabel.FINAL_APPROVED.value,
})

# Canonical market audit status strings for enforce_market_ceiling callers
MARKET_AUDIT_STATUS_AVAILABLE:   str = "AVAILABLE"
MARKET_AUDIT_STATUS_UNAVAILABLE: str = "DATA_UNOBTAINABLE"


def enforce_market_ceiling(
    result: HydrationResult,
    proposed_label: str,
) -> tuple[str, str]:
    """
    Canonical ceiling reducer — every MODEL_READY row must pass through this
    before its terminal_label is published.

    Returns (enforced_terminal_label, market_audit_status).

    Rules (applied in order):

    1. BLOCKED row (confidence_lane_available=False):
       → (DATA_CONTRACT_FAIL, DATA_UNOBTAINABLE)
       Model never ran; no probability, prediction, calibration, or exposure.

    2. MODEL_READY, market_lane_available=False,
       proposed_label in MARKET_REQUIRED_LABELS:
       → (MODEL_QUALIFIED_HOLD, DATA_UNOBTAINABLE)
       MONEY_QUALIFIED and FINAL_APPROVED cannot bypass absent market data.

    3. MODEL_READY, market_lane_available=False,
       proposed_label NOT in MARKET_REQUIRED_LABELS:
       → (proposed_label, DATA_UNOBTAINABLE)
       Label is below the money lane — ceiling does not lower it further,
       but market audit records DATA_UNOBTAINABLE.

    4. MODEL_READY, market_lane_available=True:
       → (proposed_label, AVAILABLE)
       All lanes are open; caller's label passes through unchanged.

    This function is idempotent: applying it twice with the same inputs
    produces the same output.  FINAL_APPROVED and MONEY_QUALIFIED cannot
    bypass market_lane_available=False regardless of how they arrive
    (preloaded, downstream routing, or direct assignment).
    """
    # Rule 1: BLOCKED row
    if not result.confidence_lane_available:
        return (PropLabel.DATA_CONTRACT_FAIL.value, MARKET_AUDIT_STATUS_UNAVAILABLE)

    market_audit = (
        MARKET_AUDIT_STATUS_AVAILABLE
        if result.market_lane_available
        else MARKET_AUDIT_STATUS_UNAVAILABLE
    )

    # Rules 2 & 3: market-lane ceiling
    if not result.market_lane_available and proposed_label in MARKET_REQUIRED_LABELS:
        return (PropLabel.MODEL_QUALIFIED_HOLD.value, market_audit)

    # Rule 4: all lanes open, or non-money label under ceiling
    return (proposed_label, market_audit)


def validate_exposure_write(
    result: HydrationResult,
) -> tuple[bool, str]:
    """
    Returns (allowed, reason).

    Market-edge, slip, final-card, and exposure ledger writes require:
      1. Row is MODEL_READY (not BLOCKED).
      2. market_lane_available=True.

    A confidence-only row (market_lane_available=False) may proceed to model
    scoring but MUST NOT write to market-edge, slip, final-card, or exposure
    ledgers.  These writes are the sole responsibility of the caller to gate;
    this function provides the canonical allow/deny decision.
    """
    if result.lifecycle_state == LifecycleState.BLOCKED:
        return (
            False,
            f"Row {result.row_id!r} is BLOCKED (lifecycle_state=BLOCKED) — "
            "no market-edge, slip, final-card, or exposure writes permitted",
        )
    if not result.market_lane_available:
        return (
            False,
            f"Row {result.row_id!r} has market_lane_available=False "
            f"(market_gate_outcome={result.market_gate_outcome.value}); "
            "market-edge, slip, final-card, and exposure writes are prohibited",
        )
    return (True, "")
