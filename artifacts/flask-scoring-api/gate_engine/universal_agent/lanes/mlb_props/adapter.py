"""
gate_engine/universal_agent/lanes/mlb_props/adapter.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

MLB Props Lane Adapter — public entry point.

Transforms one WOW MLB props evidence row + optional enrichment into:
  - EvidencePacket (lane=Lane.MLB_PROPS, frozen)
  - Six validated B1 advisory role payloads
  - one_ip_gate_result — routing decision for 1IP pitches markets
    (routing_required=True blocks generic models for that stat type)

MLB Props lane invariants
─────────────────────────
1. pitcher_strikeouts
   failure_path_probability_required=True is set in the sport_specialist
   role payload. Advisory agents must include a failure-path probability
   estimate — the stat has a well-defined failure distribution even when
   primary model data is absent.

2. pitcher_outs
   outs_equivalent is computed from innings_pitched notation (4.2 IP → 14
   outs, not 12.6) and is set in both the market_snapshot and the
   sport_specialist role payload. Advisory agents must use outs_equivalent
   (not the raw IP float) when comparing against the line.

3. pitcher_1ip_pitches
   The OneIpGate is evaluated; if routing_required=True, the adapter sets
   one_ip_gate_result with event_tree_id=MLB_1IP_PITCHES_EVENT_TREE_V1 and
   generic_model_blocked=True. Generic models (binomial, Poisson) are
   structurally incompatible with the 1IP market — the sport_specialist
   role payload flags this explicitly.

PipelineStateGuard integration
───────────────────────────────
Two failure paths are classified and returned via failure_classification:
  ACQUISITION_PROVIDER_ERROR — acquisition_error kwarg supplied; adapt()
    returns TECHNICAL_FAILURE before row processing.
  ONE_IP_GATE_MODEL_ERROR — OneIpGate raised unexpectedly (not a routing
    decision — an internal error); adapt() returns TECHNICAL_FAILURE with
    packet and role_payloads preserved.

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
from gate_engine.universal_agent.lanes.mlb_props.field_map import (
    SOURCE_ROW_FIELDS_USED, build_data_gaps,
    extract_canonical_event_id, extract_deterministic_model_inputs,
    extract_event_date, extract_event_name, extract_market_snapshot,
    extract_player_identity, extract_source_conflicts, extract_source_failures,
    extract_source_provenance, extract_source_timestamps, extract_team_identity,
)
from gate_engine.universal_agent.lanes.mlb_props.role_inputs import (
    build_data_slate_integrity_input, build_failure_contradiction_input,
    build_final_refresh_input, build_market_exact_line_input,
    build_news_status_input, build_sport_specialist_input,
    RoleInputBuildError,
)
from gate_engine.universal_agent.lanes.mlb_props.validation import (
    AdapterInputError, validate_mlb_props_row,
)
from gate_engine.universal_agent.lanes.mlb_props.event_tree.one_ip_gate import (
    OneIpGate, OneIpGateResult,
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
ADAPTER_MODULE  = "mlb_props_adapter"
ADAPTER_VERSION = "v1.0"

_GUARD      = PipelineStateGuard()
_one_ip_gate = OneIpGate()


class AdapterStatus:
    """
    COMPLETE          — all six role payloads valid, all coverage fields present.
    DEGRADED          — all six role payloads valid, some evidence fields absent.
    TECHNICAL_FAILURE — infrastructure/backend error (acquisition provider error
                        or unexpected OneIpGate internal error). Upstream work is
                        preserved in failure_classification.
    CONTRACT_FAILURE  — identity/scope data contract violation (AdapterInputError).
                        Fail-closed; reconstruction is blocked.
    """
    COMPLETE          = "COMPLETE"
    DEGRADED          = "DEGRADED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE  = "CONTRACT_FAILURE"


@dataclass(frozen=True)
class MlbPropsAdapterResult:
    """
    Immutable result of one MLB Props adapter run.

    one_ip_gate_result
        Always present: the OneIpGate routing decision. When
        routing_required=True, generic models are blocked for this row
        and event_tree_id=MLB_1IP_PITCHES_EVENT_TREE_V1.

    failure_classification
        Set for TECHNICAL_FAILURE and CONTRACT_FAILURE only.
        Contains the ScopedContractFailure for this specific row.
        None for COMPLETE and DEGRADED.

    ceiling_result
        The UpgradeGuardResult from PipelineStateGuard.can_upgrade()
        evaluated at adapt() time. Set when failure_classification is not
        None. None for COMPLETE / DEGRADED.
    """
    packet:                 EvidencePacket
    role_payloads:          dict    # role_id -> validated B1 payload
    adapter_status:         str     # AdapterStatus constant
    degradation_reasons:    tuple   # ("MISSING:field_key", ...)
    source_row_fields_used: tuple
    one_ip_gate_result:     Optional[OneIpGateResult] = None
    failure_classification: Optional[ScopedContractFailure] = None
    ceiling_result:         Optional[UpgradeGuardResult] = None


class MlbPropsAdapter:
    """
    Stateless re-entrant MLB Props lane adapter.
    One instance may serve multiple adapt() calls.
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
    ) -> MlbPropsAdapterResult:
        """
        Transform one MLB props row into Universal Agent Core contracts.

        Parameters
        ----------
        row               WOW scoring row dict (read-only). Row wins on key collision.
        run_id            Caller-supplied run identifier (echoed in EvidencePacket).
        enrichment        Optional dict (event_status, game_log, market_comparison…).
        snapshot_id       Optional deterministic snapshot_id override for tests.
        acquisition_error Optional string describing a provider/backend failure
                          that occurred BEFORE the row reached the adapter.
                          When set, adapt() returns TECHNICAL_FAILURE immediately
                          without processing the row.

        Raises AdapterInputError for identity/scope failures.
        Raises RoleInputBuildError for field_map derivation bugs.
        """
        # ── Acquisition-layer technical failure ───────────────────────────────
        if acquisition_error is not None:
            row_id = _row_id_from(row, run_id)
            stub   = _build_stub_packet(row, run_id, snapshot_id)
            failure = _GUARD.scope_failure(
                row_id=row_id,
                failure_kind=FailureKind.TECHNICAL,
                failure_code="ACQUISITION_PROVIDER_ERROR",
                failed_at_layer=PipelineLayer.ACQUISITION,
                message=f"Evidence acquisition failed: {acquisition_error}",
                preserved_upstream_result={},
            )
            ceiling_result = _GUARD.can_upgrade(failure, UpgradeCeiling.ADVISORY)
            return MlbPropsAdapterResult(
                packet=stub,
                role_payloads={},
                adapter_status=AdapterStatus.TECHNICAL_FAILURE,
                degradation_reasons=(f"ACQUISITION_ERROR: {acquisition_error}",),
                source_row_fields_used=(),
                one_ip_gate_result=None,
                failure_classification=failure,
                ceiling_result=ceiling_result,
            )

        # ── Normal processing path ────────────────────────────────────────────
        validate_mlb_props_row(row)

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
            lane=Lane.MLB_PROPS,
            snapshot_id=snapshot_id,
            event_name=extract_event_name(combined),
            event_date=extract_event_date(combined),
            player_id=player_ids.get("player_id"),
            player_name=player_ids.get("player_name"),
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

        # ── 1IP event-tree gate ───────────────────────────────────────────────
        one_ip_result, gate_error = _run_one_ip_gate_classified(combined)

        if gate_error is not None:
            row_id = f"{packet.canonical_event_id}:{run_id}"
            upstream_snapshot = {
                "role_payloads_built": len(role_payloads),
                "packet_snapshot_id":  packet.snapshot_id,
                "packet_run_id":       packet.run_id,
            }
            failure = _GUARD.scope_failure(
                row_id=row_id,
                failure_kind=FailureKind.TECHNICAL,
                failure_code="ONE_IP_GATE_MODEL_ERROR",
                failed_at_layer=PipelineLayer.ADAPTER,
                message=f"1IP gate raised: {gate_error}",
                preserved_upstream_result=upstream_snapshot,
            )
            ceiling_result = _GUARD.can_upgrade(failure, UpgradeCeiling.HOLD)
            data_gaps = build_data_gaps(combined)
            return MlbPropsAdapterResult(
                packet=packet,
                role_payloads=role_payloads,
                adapter_status=AdapterStatus.TECHNICAL_FAILURE,
                degradation_reasons=tuple(data_gaps) + (
                    f"ONE_IP_GATE_ERROR: {gate_error}",
                ),
                source_row_fields_used=SOURCE_ROW_FIELDS_USED,
                one_ip_gate_result=None,
                failure_classification=failure,
                ceiling_result=ceiling_result,
            )

        # ── COMPLETE or DEGRADED ──────────────────────────────────────────────
        data_gaps = build_data_gaps(combined)
        status    = AdapterStatus.DEGRADED if data_gaps else AdapterStatus.COMPLETE
        return MlbPropsAdapterResult(
            packet=packet,
            role_payloads=role_payloads,
            adapter_status=status,
            degradation_reasons=tuple(data_gaps),
            source_row_fields_used=SOURCE_ROW_FIELDS_USED,
            one_ip_gate_result=one_ip_result,
            failure_classification=None,
            ceiling_result=None,
        )


# ── Private helpers ───────────────────────────────────────────────────────────

def _row_id_from(row: Any, run_id: str) -> str:
    event_id = str(
        (row.get("event_id") or row.get("canonical_event_id") or "unknown")
        if isinstance(row, dict) else "unknown"
    )
    return f"{event_id}:{run_id}"


def _build_stub_packet(row: Any, run_id: str, snapshot_id: Optional[str]) -> EvidencePacket:
    r = row if isinstance(row, dict) else {}
    event_id = str(r.get("event_id") or r.get("canonical_event_id") or f"unknown-{run_id}")
    return build_evidence_packet(
        run_id=run_id,
        canonical_event_id=event_id,
        lane=Lane.MLB_PROPS,
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
    rs = (combined.get("role_status") or {})
    if not isinstance(rs, dict):
        rs = {}
    return {
        "active_status":  rs.get("active_status"),
        "injury_flag":    bool(rs.get("injury_flag")),
        "lineup_confirmed": bool(combined.get("lineup_confirmed")),
        "batting_order":  combined.get("batting_order"),
        "starter_flag":   combined.get("starter_flag"),
    }


def _run_one_ip_gate_classified(combined: dict) -> tuple:
    """
    Invoke OneIpGate.evaluate(), distinguishing three outcomes:
      (OneIpGateResult, None) — gate ran normally.
      (None, str)             — gate raised; string is error description.
    """
    try:
        result = _one_ip_gate.evaluate(combined)
        return (result, None)
    except Exception as exc:
        return (None, f"{type(exc).__name__}: {exc}")
