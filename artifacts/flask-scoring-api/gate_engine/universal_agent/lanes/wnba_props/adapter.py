"""
gate_engine/universal_agent/lanes/wnba_props/adapter.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4
#193-INTEGRATION: PipelineStateGuard wired at the real B4 decision point.

WNBA/NBA Props Lane Adapter — public entry point.

Transforms one WOW WNBA/NBA props evidence row + optional enrichment into:
  - EvidencePacket (lane=Lane.WNBA_PROPS, frozen)
  - Six validated B1 advisory role payloads
  - game_script_shadow — provisional game-script distribution shadow output
    (ceiling MODEL_QUALIFIED_HOLD, can_execute=False, best-effort)

Pipeline state integration (#193)
──────────────────────────────────
PipelineStateGuard.can_upgrade() is called inside adapt() for every path
that produces a technical or contract failure, making the state-separation
logic load-bearing. Two failure paths are wired:

  ACQUISITION_PROVIDER_ERROR  — acquisition_error kwarg supplied by caller;
                                 returns TECHNICAL_FAILURE before row processing.
  GAME_SCRIPT_MODEL_ERROR     — game-script shadow gate raised an exception
                                 (distinguishable from a legitimate None return);
                                 returns TECHNICAL_FAILURE with packet and
                                 role_payloads preserved as upstream.

Legitimate DEGRADED results (build_data_gaps() non-empty, no exception) are
unchanged — they carry no failure_classification and no ceiling_result.

Invariants
──────────
- can_execute = False
- No live LLM, API, or network calls
- No app.py import or Flask route wiring
- Row fields take precedence over enrichment on key collision
- Missing fields degrade gracefully to UNKNOWN / MISSING sentinels
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from gate_engine.universal_agent.evidence_packet import (
    EvidencePacket, Lane, build_evidence_packet,
)
from gate_engine.universal_agent.lanes.wnba_props.field_map import (
    SOURCE_ROW_FIELDS_USED, build_data_gaps,
    extract_canonical_event_id, extract_deterministic_model_inputs,
    extract_event_date, extract_event_name, extract_market_snapshot,
    extract_player_identity, extract_source_conflicts, extract_source_failures,
    extract_source_provenance, extract_source_timestamps, extract_team_identity,
    _role_status,
)
from gate_engine.universal_agent.lanes.wnba_props.role_inputs import (
    build_data_slate_integrity_input, build_failure_contradiction_input,
    build_final_refresh_input, build_market_exact_line_input,
    build_news_status_input, build_sport_specialist_input,
    RoleInputBuildError,
)
from gate_engine.universal_agent.lanes.wnba_props.validation import (
    AdapterInputError, validate_wnba_props_row,
)
from gate_engine.universal_agent.pipeline_state import (
    FailureKind,
    PipelineLayer,
    UpgradeCeiling,
    PipelineStateGuard,
    ScopedContractFailure,
    UpgradeGuardResult,
)
from gate_engine.universal_agent.roles.data_slate_integrity import ROLE_ID as DSI_ROLE_ID
from gate_engine.universal_agent.roles.news_status import ROLE_ID as NS_ROLE_ID
from gate_engine.universal_agent.roles.market_exact_line import ROLE_ID as MEL_ROLE_ID
from gate_engine.universal_agent.roles.sport_specialist import ROLE_ID as SS_ROLE_ID
from gate_engine.universal_agent.roles.failure_contradiction import ROLE_ID as FC_ROLE_ID
from gate_engine.universal_agent.roles.final_refresh import ROLE_ID as FR_ROLE_ID

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
ADAPTER_MODULE  = "wnba_props_adapter"
ADAPTER_VERSION = "v1.1"   # bumped for #193-INTEGRATION

# Module-level guard singleton — stateless, thread-safe.
# This instance is used inside adapt() for every failure classification call.
_GUARD = PipelineStateGuard()


class AdapterStatus:
    """
    COMPLETE          — all six role payloads valid, all coverage fields present.
    DEGRADED          — all six role payloads valid, but some evidence absent.
                        Legitimate data quality issue; no technical failure.
    TECHNICAL_FAILURE — infrastructure/backend error (acquisition provider,
                        game-script model crash). Upstream work is preserved
                        in failure_classification.preserved_upstream_result.
    CONTRACT_FAILURE  — data contract violation (AdapterInputError, schema
                        mismatch). Fail-closed; reconstruction is blocked.

    COMPLETE and DEGRADED carry failure_classification=None and ceiling_result=None.
    TECHNICAL_FAILURE and CONTRACT_FAILURE always carry both fields.
    """
    COMPLETE          = "COMPLETE"
    DEGRADED          = "DEGRADED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE  = "CONTRACT_FAILURE"


@dataclass(frozen=True)
class WnbaPropsAdapterResult:
    """
    Immutable result of one WNBA props adapter run.

    game_script_shadow
        Provisional game-script distribution output (dict) or None.
        Shadow / advisory only — ceiling MODEL_QUALIFIED_HOLD.
        None when game-script inputs are insufficient (legitimate absence)
        or when a model error occurred (in which case adapter_status is
        TECHNICAL_FAILURE and failure_classification is set).

    failure_classification
        Set for TECHNICAL_FAILURE and CONTRACT_FAILURE status only.
        Contains the ScopedContractFailure for this specific row.
        None for COMPLETE and DEGRADED.

    ceiling_result
        The UpgradeGuardResult from PipelineStateGuard.can_upgrade() for
        the HOLD ceiling, evaluated at adapt() time. Set when
        failure_classification is not None. None for COMPLETE / DEGRADED.
        This is the load-bearing guard call — if allowed=False, the B4
        pipeline output reflects a failure state, not a clean result.
    """
    packet:                 EvidencePacket
    role_payloads:          dict    # role_id -> validated B1 payload
    adapter_status:         str     # AdapterStatus constant
    degradation_reasons:    tuple   # ("MISSING:field_key", ...)
    source_row_fields_used: tuple
    game_script_shadow:     Optional[dict] = None
    failure_classification: Optional[ScopedContractFailure] = None
    ceiling_result:         Optional[UpgradeGuardResult] = None


class WnbaPropsAdapter:
    """
    Stateless re-entrant adapter. One instance may serve multiple adapt() calls.
    can_execute = False — no orders, wagers, or market mutations.
    """

    def adapt(
        self,
        *,
        row: Any,
        run_id: str,
        enrichment: Optional[dict] = None,
        snapshot_id: Optional[str] = None,
        acquisition_error: Optional[str] = None,
    ) -> WnbaPropsAdapterResult:
        """
        Parameters
        ----------
        row               WOW scoring row dict (read-only). Row wins on key collision.
        run_id            Caller-supplied run identifier (echoed in EvidencePacket).
        enrichment        Optional dict (event_status, game_log, market_comparison…).
        snapshot_id       Optional deterministic snapshot_id override for tests.
        acquisition_error Optional string describing a provider/backend acquisition
                          failure that occurred BEFORE the row reached the adapter.
                          When set, adapt() immediately returns TECHNICAL_FAILURE
                          without processing the row (row may be partially valid).
                          PipelineStateGuard.can_upgrade() is called at ADVISORY
                          ceiling and the result is in ceiling_result.

        Raises AdapterInputError for identity/scope failures (when
        acquisition_error is None — normal path).
        Raises RoleInputBuildError for field_map derivation bugs.
        """
        # ── Acquisition-layer technical failure ───────────────────────────────
        # Caller signals that evidence acquisition failed before this row could
        # be fetched (e.g. HTTP 503 from BallDontLie, network timeout from stats
        # provider). Return TECHNICAL_FAILURE immediately without processing.
        if acquisition_error is not None:
            row_id = _row_id_from(row, run_id)
            stub   = _build_stub_packet(row, run_id, snapshot_id)
            failure = _GUARD.scope_failure(
                row_id=row_id,
                failure_kind=FailureKind.TECHNICAL,
                failure_code="ACQUISITION_PROVIDER_ERROR",
                failed_at_layer=PipelineLayer.ACQUISITION,
                message=f"Evidence acquisition failed: {acquisition_error}",
                preserved_upstream_result={},   # nothing processed yet
            )
            ceiling_result = _GUARD.can_upgrade(failure, UpgradeCeiling.ADVISORY)
            return WnbaPropsAdapterResult(
                packet=stub,
                role_payloads={},
                adapter_status=AdapterStatus.TECHNICAL_FAILURE,
                degradation_reasons=(f"ACQUISITION_ERROR: {acquisition_error}",),
                source_row_fields_used=(),
                game_script_shadow=None,
                failure_classification=failure,
                ceiling_result=ceiling_result,
            )

        # ── Normal processing path ────────────────────────────────────────────
        validate_wnba_props_row(row)

        if not isinstance(run_id, str) or not run_id.strip():
            raise AdapterInputError(
                "ADAPTER_MISSING_RUN_ID",
                "adapt() requires a non-empty run_id string",
            )

        combined: dict = {**(enrichment or {}), **row}

        team_ids   = extract_team_identity(combined)
        player_ids = extract_player_identity(combined)

        packet = build_evidence_packet(
            run_id=run_id,
            canonical_event_id=extract_canonical_event_id(combined),
            lane=Lane.WNBA_PROPS,
            snapshot_id=snapshot_id,
            event_name=extract_event_name(combined),
            event_date=extract_event_date(combined),
            player_id=None,
            player_name=player_ids["player_name"],
            team_id=team_ids["team_id"],
            team_name=team_ids["team_name"],
            opponent_team_id=team_ids["opponent_team_id"],
            opponent_team_name=team_ids["opponent_team_name"],
            source_timestamps=extract_source_timestamps(combined),
            source_provenance=extract_source_provenance(combined),
            market_snapshot=extract_market_snapshot(combined),
            injury_status_evidence=_build_injury_evidence(combined),
            deterministic_model_inputs=extract_deterministic_model_inputs(combined),
            source_failures=extract_source_failures(combined),
            source_conflicts=extract_source_conflicts(combined),
        )

        role_payloads: dict[str, dict] = {
            DSI_ROLE_ID: build_data_slate_integrity_input(combined),
            NS_ROLE_ID:  build_news_status_input(combined),
            MEL_ROLE_ID: build_market_exact_line_input(combined),
            SS_ROLE_ID:  build_sport_specialist_input(combined),
            FC_ROLE_ID:  build_failure_contradiction_input(combined),
            FR_ROLE_ID:  build_final_refresh_input(combined),
        }

        # ── Model layer: game-script shadow ───────────────────────────────────
        # _run_game_script_shadow_classified() distinguishes:
        #   (dict, None)  — shadow gate ran, produced output
        #   (None, None)  — shadow gate ran, returned None legitimately
        #   (None, str)   — shadow gate raised an exception (TECHNICAL failure)
        #
        # The third case is the fix: previously all exceptions were swallowed
        # into None, making a model crash indistinguishable from a legitimate
        # absence of game-script output. Now it is distinguishable and classified.
        game_script_shadow, shadow_error = _run_game_script_shadow_classified(
            combined, run_id
        )

        if shadow_error is not None:
            # Game-script model crash — TECHNICAL failure.
            # Packet and role_payloads ARE preserved (they were built above).
            row_id = f"{packet.canonical_event_id}:{run_id}"
            upstream_snapshot = {
                "role_payloads_built":   len(role_payloads),
                "packet_snapshot_id":    packet.snapshot_id,
                "packet_run_id":         packet.run_id,
            }
            failure = _GUARD.scope_failure(
                row_id=row_id,
                failure_kind=FailureKind.TECHNICAL,
                failure_code="GAME_SCRIPT_MODEL_ERROR",
                failed_at_layer=PipelineLayer.ADAPTER,
                message=f"Game-script shadow gate raised: {shadow_error}",
                preserved_upstream_result=upstream_snapshot,
            )
            # Evaluate HOLD ceiling — allowed=True because upstream is preserved
            ceiling_result = _GUARD.can_upgrade(failure, UpgradeCeiling.HOLD)
            data_gaps = build_data_gaps(combined)
            return WnbaPropsAdapterResult(
                packet=packet,
                role_payloads=role_payloads,
                adapter_status=AdapterStatus.TECHNICAL_FAILURE,
                degradation_reasons=tuple(data_gaps) + (
                    f"GAME_SCRIPT_ERROR: {shadow_error}",
                ),
                source_row_fields_used=SOURCE_ROW_FIELDS_USED,
                game_script_shadow=None,
                failure_classification=failure,
                ceiling_result=ceiling_result,
            )

        # ── Legitimate path: COMPLETE or DEGRADED ─────────────────────────────
        data_gaps = build_data_gaps(combined)
        status    = AdapterStatus.DEGRADED if data_gaps else AdapterStatus.COMPLETE
        return WnbaPropsAdapterResult(
            packet=packet,
            role_payloads=role_payloads,
            adapter_status=status,
            degradation_reasons=tuple(data_gaps),
            source_row_fields_used=SOURCE_ROW_FIELDS_USED,
            game_script_shadow=game_script_shadow,
            failure_classification=None,
            ceiling_result=None,
        )


# ── Private helpers ───────────────────────────────────────────────────────────

def _row_id_from(row: Any, run_id: str) -> str:
    """Derive a stable row identifier for ScopedContractFailure."""
    event_id = str(
        (row.get("event_id") or row.get("canonical_event_id") or "unknown")
        if isinstance(row, dict) else "unknown"
    )
    return f"{event_id}:{run_id}"


def _build_stub_packet(
    row: Any,
    run_id: str,
    snapshot_id: Optional[str],
) -> EvidencePacket:
    """
    Build a minimal EvidencePacket for failure cases where full processing
    did not occur (e.g. acquisition_error path). Uses .get() with fallbacks
    so the row may be partially invalid.
    """
    r = row if isinstance(row, dict) else {}
    event_id = str(r.get("event_id") or r.get("canonical_event_id") or f"unknown-{run_id}")
    return build_evidence_packet(
        run_id=run_id,
        canonical_event_id=event_id,
        lane=Lane.WNBA_PROPS,
        snapshot_id=snapshot_id,
        event_name="",
        event_date=None,
        player_id=None,
        player_name=str(r.get("player") or "UNKNOWN"),
        team_id=str(r.get("team") or "UNKNOWN"),
        team_name=str(r.get("team") or "UNKNOWN"),
        opponent_team_id=str(r.get("opponent") or "UNKNOWN"),
        opponent_team_name=str(r.get("opponent") or "UNKNOWN"),
        source_timestamps={},
        source_provenance={},
        market_snapshot={},
        injury_status_evidence={},
        deterministic_model_inputs={},
        source_failures={},
        source_conflicts={},
    )


def _build_injury_evidence(combined: dict) -> dict:
    rs = _role_status(combined)
    return {
        "active_status":     rs.get("active_status"),
        "projected_minutes": rs.get("projected_minutes"),
        "expected_start":    rs.get("expected_start"),
        "minutes_low":       rs.get("minutes_low"),
        "minutes_high":      rs.get("minutes_high"),
        "usage_role":        rs.get("usage_role"),
    }


def _run_game_script_shadow_classified(
    combined: dict,
    run_id: str,
) -> tuple:
    """
    Invoke the game-script shadow gate, distinguishing three outcomes:

      (dict, None)  — shadow gate ran and produced a result dict.
      (None, None)  — shadow gate ran normally, returned None (legitimate
                      absence: insufficient inputs, fail-closed in shadow gate).
      (None, str)   — shadow gate raised an exception. The string is a
                      description of the exception; this path triggers
                      TECHNICAL_FAILURE classification in adapt().

    This replaces the old _run_game_script_shadow() which swallowed all
    exceptions into (None) making model crashes indistinguishable from
    legitimate None returns. That was the silent collapse #193 fixes.
    """
    try:
        from gate_engine.universal_agent.lanes.wnba_props.game_script.shadow_gate import (
            GameScriptShadowGate,
        )
        result = GameScriptShadowGate().run(combined=combined, run_id=run_id)
        return (result, None)   # success: result may be None (legitimate)
    except Exception as exc:
        return (None, f"{type(exc).__name__}: {exc}")   # technical failure
