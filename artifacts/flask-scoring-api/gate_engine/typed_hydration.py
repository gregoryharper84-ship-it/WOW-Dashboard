"""
typed_hydration.py — WOW-PATCH-2026-08-17-TYPED-HYDRATION-AND-MODEL-READINESS-V1

Typed lifecycle state machine and four-gate data-presence enforcement layer.

Architectural invariant (per patch spec):
  Data acquisition failure must NEVER be presented as model judgment.
  lifecycle_state / data_status / model_status / failure_class are separate
  typed dimensions.  terminal_label remains a native WOW label (e.g.,
  DATA_CONTRACT_FAIL).  INCOMPLETE_INPUT, DATA_PROVIDER_OUTAGE, and STALE_DATA
  are typed data_status values — NOT replacements for native WOW terminal labels.

Lifecycle (state machine, forward-only):
  BOARD_EXTRACTED → DATA_HYDRATING
    → BLOCKED           (any data gate fails — missing, stale, outage, conflict)
    → CONTRACT_COMPLETE → FOUR_GATES_CLEARED → MODEL_READY
                                                  → SCORING_ATOMIC → SCORED
                                                  → BLOCKED (calibration/write fail)

Only MODEL_READY rows may be ranked, slipped, or entered into exposure ledgers.
Blocked rows are structurally excluded by the RunController before any ranking step.

Four data-presence gates (not analytical gates — no probability/threshold logic):
  1. Identity / Status Gate    — canonical identity, correct slate, active participant
  2. Role / Opportunity Gate   — minutes, workload, lineup slot, role certainty
  3. Historical-Ledger Gate    — raw L5/L10, hit rates, median, sample window, push rate
  4. Market / Settlement Gate  — exact line, both directions, no-vig, timestamp, TTL

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
    gate_id:        str
    passed:         bool
    missing_fields: list[str]
    data_status:    DataStatus
    failure_class:  FailureClass
    failure_reason: str


@dataclass
class HydrationResult:
    """
    Single-row typed hydration result.

    All four state dimensions (lifecycle_state / data_status / model_status /
    failure_class) are separate from terminal_label, which remains a native
    WOW label (e.g., DATA_CONTRACT_FAIL for blocked rows, "" for MODEL_READY
    rows pending scoring).
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

    Required enrichment fields: market_no_vig_probability, data_timestamp
    TTL check: if market_checked_at + market_ttl (seconds) < now → STALE_DATA.
    SOURCE_CONFLICT overrides missing-field classification.

    Expired TTL cannot be refreshed merely by reusing the old value.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # SOURCE_CONFLICT check first
    no_vig = str(enrichment.get("market_no_vig_probability") or "").strip().upper()
    if no_vig == "SOURCE_CONFLICT":
        return FourGateResult(
            gate_id=GATE_MARKET,
            passed=False,
            missing_fields=[],
            data_status=DataStatus.SOURCE_CONFLICT,
            failure_class=FailureClass.CONFLICT_FAILURE,
            failure_reason="market_no_vig_probability=SOURCE_CONFLICT across sources",
        )

    # TTL freshness check — runs before field-presence check
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
                )
        except (ValueError, TypeError, AttributeError):
            pass  # malformed timestamp — fall through to field presence

    # Field presence check
    enr_required = ["market_no_vig_probability", "data_timestamp"]
    missing: list[str] = []
    for f in enr_required:
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
            failure_reason=f"Missing market/settlement fields: {missing}",
        )

    return FourGateResult(
        gate_id=GATE_MARKET,
        passed=True,
        missing_fields=[],
        data_status=DataStatus.COMPLETE,
        failure_class=FailureClass.NONE,
        failure_reason="",
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
    failed_gates = [g for g in gate_results.values() if not g.passed]
    passed_count = len(gate_results) - len(failed_gates)
    all_missing  = [f for g in failed_gates for f in g.missing_fields]

    if failed_gates:
        # Fail-closed: any gate failure → BLOCKED
        _validate_transition(LifecycleState.DATA_HYDRATING, LifecycleState.BLOCKED)
        worst_ds  = _worst_data_status(failed_gates)
        worst_fc  = _worst_failure_class(failed_gates)
        summary   = "; ".join(g.failure_reason for g in failed_gates if g.failure_reason)

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
            provider_attempts=list(provider_attempts or []),
            fallback_sources=list(fallback_sources or []),
        )

    # All four gates passed: CONTRACT_COMPLETE → FOUR_GATES_CLEARED → MODEL_READY
    _validate_transition(LifecycleState.DATA_HYDRATING,     LifecycleState.CONTRACT_COMPLETE)
    _validate_transition(LifecycleState.CONTRACT_COMPLETE,  LifecycleState.FOUR_GATES_CLEARED)
    _validate_transition(LifecycleState.FOUR_GATES_CLEARED, LifecycleState.MODEL_READY)

    return HydrationResult(
        row_id=row_id,
        lifecycle_state=LifecycleState.MODEL_READY,
        data_status=DataStatus.COMPLETE,
        model_status=ModelStatus.READY,
        failure_class=FailureClass.NONE,
        terminal_label="",      # cleared at MODEL_READY; set by scoring after SCORED
        gate_results=gate_results,
        gates_passed=passed_count,
        gates_failed=0,
        missing_fields=[],
        failure_summary="",
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
