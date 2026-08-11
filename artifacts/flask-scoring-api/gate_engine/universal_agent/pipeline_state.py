"""
gate_engine/universal_agent/pipeline_state.py
WOW B4-HARDENING-#193 — Pipeline State Separation + Scoped DATA_CONTRACT_FAIL

Separates three categories of pipeline outcomes that previously shared the
same code path, causing technical failures to contaminate adjacent rows and
legitimate outcomes to be treated as errors:

  TECHNICAL           Infrastructure failure (DB timeout, network error,
                      internal exception). Upstream modeling completed before
                      the failure is PRESERVED. The row is locked at ADVISORY
                      ceiling and cannot be upgraded to any verified/money/edge
                      state, but the upstream work is not discarded.

  CONTRACT            Data-contract or schema violation (DATA_CONTRACT_FAIL,
                      missing required fields, type mismatch). Reconstruction
                      is blocked. Fail-closed at every upgrade level.

  LEGITIMATE_OUTCOME  A valid betting-system outcome (NO_PLAY, REJECT, WATCH).
                      Not an error. Passes the upgrade guard unconditionally so
                      downstream handlers can consume the outcome normally.

Row isolation guarantee
───────────────────────
Each row gets its own RowPipelineState instance. A ScopedContractFailure
carries the row_id it belongs to, and RowPipelineState.record_failure()
raises ValueError if the IDs don't match. This makes cross-row contamination
a hard error rather than a silent data-quality issue.

Fail-closed rules (in priority order)
─────────────────────────────────────
1. LEGITIMATE_OUTCOME → always allowed.
2. reconstruction_attempted=True → blocked above ADVISORY.
3. TECHNICAL or CONTRACT + target in BLOCKED_FOR_FAILURES → denied.
4. TECHNICAL + non-blocked target → allowed, upstream_result echoed.
5. CONTRACT → fail-closed at all levels above ADVISORY.

can_execute = False — no authority, no decisions, no labels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


# ── Failure-kind taxonomy ─────────────────────────────────────────────────────

class FailureKind:
    """
    Three mutually exclusive categories of pipeline failure.
    See module docstring for semantics.
    """
    TECHNICAL          = "TECHNICAL"
    CONTRACT           = "CONTRACT"
    LEGITIMATE_OUTCOME = "LEGITIMATE_OUTCOME"

    ALL: frozenset = frozenset({TECHNICAL, CONTRACT, LEGITIMATE_OUTCOME})


# ── Pipeline-layer taxonomy ───────────────────────────────────────────────────

class PipelineLayer:
    """Pipeline stages in execution order."""
    ACQUISITION = "ACQUISITION"
    ADAPTER     = "ADAPTER"
    MARKET      = "MARKET"
    MONEY       = "MONEY"
    SLIP        = "SLIP"
    GOVERNANCE  = "GOVERNANCE"

    ALL: frozenset = frozenset({ACQUISITION, ADAPTER, MARKET, MONEY, SLIP, GOVERNANCE})


# ── Upgrade-ceiling taxonomy ──────────────────────────────────────────────────

class UpgradeCeiling:
    """
    Target states that may be requested via PipelineStateGuard.can_upgrade().

    BLOCKED_FOR_FAILURES enumerates every state that is unconditionally denied
    for rows with TECHNICAL or CONTRACT failures. This list is extended with
    common aliases so callers don't need exact spelling.
    """
    ADVISORY         = "ADVISORY"
    HOLD             = "MODEL_QUALIFIED_HOLD"
    WATCH            = "WATCH"
    VERIFIED         = "VERIFIED"
    EDGE_QUALIFIED   = "EDGE_QUALIFIED"
    MONEY            = "MONEY"
    FINAL_APPROVED   = "FINAL_APPROVED"

    BLOCKED_FOR_FAILURES: frozenset = frozenset({
        "VERIFIED", "MARKET_VERIFIED", "MARKET_VERIFIED_APPROVED",
        "EDGE_QUALIFIED", "EDGE",
        "MONEY", "CAPITAL_AUTHORIZED", "STAKE_AUTHORIZED",
        "FINAL_APPROVED", "FINAL_LOCK",
    })


# ── Per-row failure record (immutable) ───────────────────────────────────────

@dataclass(frozen=True)
class ScopedContractFailure:
    """
    Immutable, row-scoped failure record.

    Carries exactly one row_id. RowPipelineState.record_failure() enforces
    that the failure's row_id matches its own row_id, preventing cross-row
    contamination from being silent.

    preserved_upstream_result
        Shallow-copy dict of whatever upstream work completed BEFORE the
        failure occurred. Non-empty only for TECHNICAL failures; empty for
        CONTRACT (there is no safe partial result to preserve).
    """
    row_id:                    str
    failure_kind:              str   # FailureKind constant
    failure_code:              str   # e.g. "DATA_CONTRACT_FAIL", "DB_TIMEOUT"
    failed_at_layer:           str   # PipelineLayer constant
    message:                   str
    reconstruction_attempted:  bool
    preserved_upstream_result: dict

    def __post_init__(self) -> None:
        if self.failure_kind not in FailureKind.ALL:
            raise ValueError(
                f"failure_kind must be one of {sorted(FailureKind.ALL)}, "
                f"got {self.failure_kind!r}"
            )
        if self.failed_at_layer not in PipelineLayer.ALL:
            raise ValueError(
                f"failed_at_layer must be one of {sorted(PipelineLayer.ALL)}, "
                f"got {self.failed_at_layer!r}"
            )
        if not isinstance(self.reconstruction_attempted, bool):
            raise TypeError(
                "reconstruction_attempted must be bool, got "
                f"{type(self.reconstruction_attempted).__name__}"
            )
        if not isinstance(self.preserved_upstream_result, dict):
            raise TypeError(
                "preserved_upstream_result must be dict, got "
                f"{type(self.preserved_upstream_result).__name__}"
            )

    def is_technical(self) -> bool:
        return self.failure_kind == FailureKind.TECHNICAL

    def is_contract(self) -> bool:
        return self.failure_kind == FailureKind.CONTRACT

    def is_legitimate_outcome(self) -> bool:
        return self.failure_kind == FailureKind.LEGITIMATE_OUTCOME


# ── Upgrade-guard result ──────────────────────────────────────────────────────

@dataclass
class UpgradeGuardResult:
    """
    Result of PipelineStateGuard.can_upgrade().

    allowed
        True iff the upgrade is permitted.
    reason
        Machine-readable reason code.
    detail
        Human-readable explanation.
    preserved_upstream_result
        Non-None only when allowed=True and the row has a TECHNICAL failure.
        Contains the upstream work completed before the failure.
    """
    allowed:                   bool
    reason:                    str
    detail:                    str
    preserved_upstream_result: Optional[dict] = None


# ── Upgrade guard (stateless) ─────────────────────────────────────────────────

class PipelineStateGuard:
    """
    Stateless validator: decides whether a failed row may be upgraded to a
    given ceiling. Fail-closed — any ambiguity produces False.

    can_execute = False. This class never issues labels or decisions.
    """

    def can_upgrade(
        self,
        failure: Optional[ScopedContractFailure],
        target_ceiling: str,
    ) -> UpgradeGuardResult:
        """
        Decide whether a row may be upgraded to target_ceiling.

        Parameters
        ----------
        failure         ScopedContractFailure for this row, or None (no failure).
        target_ceiling  Requested ceiling state (UpgradeCeiling constant or
                        any string label — compared case-insensitively against
                        BLOCKED_FOR_FAILURES).
        """
        # No failure → unrestricted
        if failure is None:
            return UpgradeGuardResult(
                allowed=True,
                reason="NO_FAILURE",
                detail="Row has no recorded failure; upgrade unrestricted.",
            )

        # LEGITIMATE_OUTCOME → valid terminal state, not an error
        if failure.is_legitimate_outcome():
            return UpgradeGuardResult(
                allowed=True,
                reason="LEGITIMATE_OUTCOME",
                detail=(
                    f"failure_code={failure.failure_code!r} is a legitimate "
                    "betting-system outcome (not an infrastructure error); "
                    "upgrade permitted."
                ),
            )

        # Reconstruction guard: partial evidence cannot upgrade above ADVISORY
        normalised = target_ceiling.upper()
        if failure.reconstruction_attempted and normalised != "ADVISORY":
            return UpgradeGuardResult(
                allowed=False,
                reason="RECONSTRUCTION_BLOCKS_UPGRADE",
                detail=(
                    f"Row {failure.row_id!r} had reconstruction_attempted=True; "
                    f"upgrade to {target_ceiling!r} blocked — reconstructed or "
                    "incomplete evidence must not reach verified/money states."
                ),
            )

        # Verified / money / edge: always blocked for TECHNICAL or CONTRACT
        if normalised in UpgradeCeiling.BLOCKED_FOR_FAILURES:
            return UpgradeGuardResult(
                allowed=False,
                reason=f"{failure.failure_kind}_BLOCKS_{normalised}",
                detail=(
                    f"Row {failure.row_id!r} has {failure.failure_kind} failure "
                    f"({failure.failure_code!r} at {failure.failed_at_layer}); "
                    f"upgrade to {target_ceiling!r} is permanently blocked."
                ),
            )

        # TECHNICAL at a non-blocked ceiling: allowed, upstream preserved
        if failure.is_technical():
            return UpgradeGuardResult(
                allowed=True,
                reason="TECHNICAL_FAILURE_UPSTREAM_PRESERVED",
                detail=(
                    f"Row {failure.row_id!r} has TECHNICAL failure at "
                    f"{failure.failed_at_layer}; completed upstream modeling "
                    f"is preserved; upgrade to {target_ceiling!r} permitted."
                ),
                preserved_upstream_result=dict(failure.preserved_upstream_result),
            )

        # CONTRACT at any non-blocked ceiling: fail-closed
        return UpgradeGuardResult(
            allowed=False,
            reason="CONTRACT_FAILURE_FAIL_CLOSED",
            detail=(
                f"Row {failure.row_id!r} has CONTRACT failure "
                f"({failure.failure_code!r} at {failure.failed_at_layer}); "
                f"fail-closed: upgrade to {target_ceiling!r} blocked."
            ),
        )

    def scope_failure(
        self,
        *,
        row_id: str,
        failure_kind: str,
        failure_code: str,
        failed_at_layer: str,
        message: str,
        reconstruction_attempted: bool = False,
        preserved_upstream_result: Optional[dict] = None,
    ) -> ScopedContractFailure:
        """
        Factory: build a ScopedContractFailure for exactly one row.
        Makes a shallow copy of preserved_upstream_result so callers cannot
        mutate the preserved snapshot after the fact.
        """
        return ScopedContractFailure(
            row_id=row_id,
            failure_kind=failure_kind,
            failure_code=failure_code,
            failed_at_layer=failed_at_layer,
            message=message,
            reconstruction_attempted=reconstruction_attempted,
            preserved_upstream_result=dict(preserved_upstream_result or {}),
        )


# ── Per-row pipeline state (one instance per row) ─────────────────────────────

_GUARD = PipelineStateGuard()   # module-level singleton — stateless, thread-safe


@dataclass
class RowPipelineState:
    """
    Mutable state for exactly one pipeline row.

    Isolation guarantee: each row must have its own RowPipelineState.
    record_failure() raises ValueError when the failure's row_id does not
    match self.row_id so cross-row assignments are a hard error.

    Usage
    -----
    state = RowPipelineState(row_id="row-001")
    state.record_layer_complete(PipelineLayer.ADAPTER, result=adapter_dict)
    failure = _GUARD.scope_failure(row_id="row-001", ...)
    state.record_failure(failure)
    result = state.check_upgrade(UpgradeCeiling.HOLD)
    if result.allowed and result.preserved_upstream_result:
        # use preserved upstream work
    """
    row_id: str
    _failure:          Optional[ScopedContractFailure] = field(
        default=None, init=False, repr=False
    )
    _completed_layers: list = field(default_factory=list,  init=False, repr=False)
    _preserved:        dict = field(default_factory=dict,  init=False, repr=False)

    def record_layer_complete(self, layer: str, result: Any = None) -> None:
        """
        Mark a pipeline layer as successfully completed for this row.
        Optionally snapshot the result dict for later retrieval.
        """
        if layer not in PipelineLayer.ALL:
            raise ValueError(f"Unknown PipelineLayer {layer!r}")
        self._completed_layers.append(layer)
        if isinstance(result, dict):
            self._preserved[layer] = dict(result)   # shallow copy — immutable snapshot

    def record_failure(self, failure: ScopedContractFailure) -> None:
        """
        Record a scoped failure for this row.
        Raises ValueError when failure.row_id != self.row_id (isolation guard).
        Idempotent: first failure wins; subsequent calls are no-ops.
        """
        if failure.row_id != self.row_id:
            raise ValueError(
                f"Row isolation violated: failure.row_id={failure.row_id!r} "
                f"does not match state.row_id={self.row_id!r}. "
                "Each ScopedContractFailure must belong to exactly one row."
            )
        if self._failure is None:
            self._failure = failure

    def check_upgrade(self, target_ceiling: str) -> UpgradeGuardResult:
        """Delegate to the module-level PipelineStateGuard."""
        return _GUARD.can_upgrade(self._failure, target_ceiling)

    @property
    def has_failure(self) -> bool:
        return self._failure is not None

    @property
    def failure(self) -> Optional[ScopedContractFailure]:
        return self._failure

    @property
    def completed_layers(self) -> tuple:
        return tuple(self._completed_layers)

    def preserved_result_for(self, layer: str) -> Optional[dict]:
        return self._preserved.get(layer)
