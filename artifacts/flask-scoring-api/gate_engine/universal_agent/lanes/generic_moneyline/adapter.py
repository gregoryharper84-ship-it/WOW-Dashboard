"""
gate_engine/universal_agent/lanes/generic_moneyline/adapter.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B7

Generic Moneyline Lane Adapter — public entry point.

Transforms one WOW moneyline/winner evidence row + optional enrichment
into Universal Agent Core contracts for sports without dedicated lanes:
  - EvidencePacket (lane=Lane.GENERIC_MONEYLINE, frozen)
  - Six validated B1 advisory role payloads

Scope
─────
Handles: NFL, NHL, NBA moneyline, NCAAF, NCAAB, soccer, MMA/UFC, boxing,
motorsport, golf, rugby, cricket, and any other sport not served by a
dedicated UAC lane.

Explicitly rejected:
  MLB (→ MLB_MONEYLINE or MLB_PROPS)
  WNBA / NBA props (→ WNBA_PROPS)
  Tennis (→ TENNIS_PROPS)

Design principles
─────────────────
1. No sport-specific model logic. Probability fields are passed through
   to the sport_specialist role payload, which references the existing
   LLP probability specialist by name (not by call).

2. No probability fabrication. When calibrated_probability is absent,
   probability_status="PROBABILITY_UNAVAILABLE" is recorded. The advisory
   agent must surface the gap — it must not fabricate a value.

3. No generic fallback for unsupported sports. generic_fallback_blocked=True
   in the model_routing dict. If the LLP specialist does not support the
   sport, the advisory agent must return PROBABILITY_UNAVAILABLE, not a
   generic estimate.

PipelineStateGuard integration
───────────────────────────────
ACQUISITION_PROVIDER_ERROR — acquisition_error kwarg supplied; adapt()
  returns TECHNICAL_FAILURE immediately without processing the row.

Invariants
──────────
- can_execute = False
- No live LLM, API, or network calls
- No app.py import or Flask route wiring
- Row wins on enrichment key collision
- Missing fields degrade gracefully; never fabricated
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from gate_engine.universal_agent.evidence_packet import (
    EvidencePacket, Lane, build_evidence_packet,
)
from gate_engine.universal_agent.lanes.generic_moneyline.field_map import (
    SOURCE_ROW_FIELDS_USED, build_data_gaps,
    extract_canonical_event_id, extract_deterministic_model_inputs,
    extract_event_date, extract_event_name, extract_market_snapshot,
    extract_source_conflicts, extract_source_failures,
    extract_source_provenance, extract_source_timestamps, extract_team_identity,
)
from gate_engine.universal_agent.lanes.generic_moneyline.role_inputs import (
    build_data_slate_integrity_input, build_failure_contradiction_input,
    build_final_refresh_input, build_market_exact_line_input,
    build_news_status_input, build_sport_specialist_input,
    RoleInputBuildError,
    LLP_PROBABILITY_SPECIALIST_REF,
)
from gate_engine.universal_agent.lanes.generic_moneyline.validation import (
    AdapterInputError, validate_generic_moneyline_row,
    DEDICATED_LANE_SPORTS, MONEYLINE_MARKET_KEYS,
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
ADAPTER_MODULE  = "generic_moneyline_adapter"
ADAPTER_VERSION = "v1.0"

_GUARD = PipelineStateGuard()


class AdapterStatus:
    COMPLETE          = "COMPLETE"
    DEGRADED          = "DEGRADED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE  = "CONTRACT_FAILURE"


@dataclass(frozen=True)
class GenericMoneylineAdapterResult:
    """
    Immutable result of one Generic Moneyline adapter run.

    failure_classification
        Set for TECHNICAL_FAILURE and CONTRACT_FAILURE only. None for
        COMPLETE and DEGRADED.
    ceiling_result
        UpgradeGuardResult from PipelineStateGuard.can_upgrade() when
        failure_classification is set. None for COMPLETE / DEGRADED.
    """
    packet:                 EvidencePacket
    role_payloads:          dict
    adapter_status:         str
    degradation_reasons:    tuple
    source_row_fields_used: tuple
    failure_classification: Optional[ScopedContractFailure] = None
    ceiling_result:         Optional[UpgradeGuardResult] = None


class GenericMoneylineAdapter:
    """
    Stateless re-entrant Generic Moneyline lane adapter.
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
    ) -> GenericMoneylineAdapterResult:
        """
        Transform one moneyline row into Universal Agent Core contracts.

        Raises AdapterInputError when:
        - sport is in DEDICATED_LANE_SPORTS (use a dedicated adapter)
        - market is not a moneyline/winner type
        - event_id is missing
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
            return GenericMoneylineAdapterResult(
                packet=stub,
                role_payloads={},
                adapter_status=AdapterStatus.TECHNICAL_FAILURE,
                degradation_reasons=(f"ACQUISITION_ERROR: {acquisition_error}",),
                source_row_fields_used=(),
                failure_classification=failure,
                ceiling_result=ceiling_result,
            )

        # ── Normal processing path ────────────────────────────────────────────
        validate_generic_moneyline_row(row)

        if not isinstance(run_id, str) or not run_id.strip():
            raise AdapterInputError(
                "ADAPTER_MISSING_RUN_ID",
                "adapt() requires a non-empty run_id string",
            )

        combined: dict = {**(enrichment or {}), **row}
        team_ids = extract_team_identity(combined)

        packet = build_evidence_packet(
            run_id=run_id,
            canonical_event_id=extract_canonical_event_id(combined),
            lane=Lane.GENERIC_MONEYLINE,
            snapshot_id=snapshot_id,
            event_name=extract_event_name(combined),
            event_date=extract_event_date(combined),
            player_id=None,
            player_name=combined.get("player"),
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

        role_payloads: dict = {
            DSI_ROLE_ID: build_data_slate_integrity_input(combined),
            NS_ROLE_ID:  build_news_status_input(combined),
            MEL_ROLE_ID: build_market_exact_line_input(combined),
            SS_ROLE_ID:  build_sport_specialist_input(combined),
            FC_ROLE_ID:  build_failure_contradiction_input(combined),
            FR_ROLE_ID:  build_final_refresh_input(combined),
        }

        data_gaps = build_data_gaps(combined)
        status    = AdapterStatus.DEGRADED if data_gaps else AdapterStatus.COMPLETE
        return GenericMoneylineAdapterResult(
            packet=packet,
            role_payloads=role_payloads,
            adapter_status=status,
            degradation_reasons=tuple(data_gaps),
            source_row_fields_used=SOURCE_ROW_FIELDS_USED,
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
    team = str(r.get("team") or r.get("team_name") or "UNKNOWN")
    opp  = str(r.get("opponent") or r.get("opponent_name") or "UNKNOWN")
    return build_evidence_packet(
        run_id=run_id,
        canonical_event_id=event_id,
        lane=Lane.GENERIC_MONEYLINE,
        snapshot_id=snapshot_id,
        event_name=f"{team} vs {opp}" if team != "UNKNOWN" else "",
        event_date=None,
        player_id=None,
        player_name=None,
        team_id=team,
        team_name=team,
        opponent_team_id=opp,
        opponent_team_name=opp,
        source_timestamps={},
        source_provenance={},
        market_snapshot={},
        injury_status_evidence={},
        deterministic_model_inputs={},
        source_failures={},
        source_conflicts={},
    )


def _build_injury_evidence(combined: dict) -> dict:
    rs = combined.get("role_status") or {}
    if not isinstance(rs, dict):
        rs = {}
    return {
        "active_status": rs.get("active_status"),
        "injury_flag":   bool(rs.get("injury_flag")),
    }
