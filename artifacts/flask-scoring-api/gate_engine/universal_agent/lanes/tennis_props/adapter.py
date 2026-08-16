"""
gate_engine/universal_agent/lanes/tennis_props/adapter.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B6

Tennis Props Lane Adapter — public entry point.

Transforms one WOW tennis props evidence row + optional enrichment into:
  - EvidencePacket (lane=Lane.TENNIS_PROPS, frozen)
  - Six validated B1 advisory role payloads

Tennis Props lane invariants
─────────────────────────────
1. total_games / set_games / first_set_games
   requires_markov_chain=True in sport_specialist payload.
   Monte Carlo and generic binomial fallback are blocked for these stat
   types — they are structurally incompatible with the exact Markov chain
   model used for tennis game totals.

2. first_set_* markets
   is_first_set_market=True; advisory agents must not apply full-match
   model parameters to first-set scoped markets.

3. Simplex probabilities (under/exact/over)
   Stored as raw full-precision floats — never rounded to 6dp. Rounding
   causes drift in simplex constraint checks (|sum - 1.0| > 1e-6).

4. Surface type
   Included in all payloads. UNKNOWN surface degrades the row but does
   not fail the adapter — market-level evidence is advisory.

PipelineStateGuard integration
───────────────────────────────
ACQUISITION_PROVIDER_ERROR — acquisition_error kwarg supplied; adapt()
  returns TECHNICAL_FAILURE immediately without processing the row.

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
from gate_engine.universal_agent.lanes.tennis_props.field_map import (
    SOURCE_ROW_FIELDS_USED, build_data_gaps,
    extract_canonical_event_id, extract_deterministic_model_inputs,
    extract_event_date, extract_event_name, extract_market_snapshot,
    extract_player_identity, extract_source_conflicts, extract_source_failures,
    extract_source_provenance, extract_source_timestamps, extract_team_identity,
)
from gate_engine.universal_agent.lanes.tennis_props.role_inputs import (
    build_data_slate_integrity_input, build_failure_contradiction_input,
    build_final_refresh_input, build_market_exact_line_input,
    build_news_status_input, build_sport_specialist_input,
    RoleInputBuildError,
)
from gate_engine.universal_agent.lanes.tennis_props.validation import (
    AdapterInputError, validate_tennis_props_row,
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
ADAPTER_MODULE  = "tennis_props_adapter"
ADAPTER_VERSION = "v1.0"

_GUARD = PipelineStateGuard()


class AdapterStatus:
    COMPLETE          = "COMPLETE"
    DEGRADED          = "DEGRADED"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE  = "CONTRACT_FAILURE"


@dataclass(frozen=True)
class TennisPropsAdapterResult:
    """
    Immutable result of one Tennis Props adapter run.

    failure_classification
        Set for TECHNICAL_FAILURE and CONTRACT_FAILURE only.
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


class TennisPropsAdapter:
    """
    Stateless re-entrant Tennis Props lane adapter.
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
    ) -> TennisPropsAdapterResult:
        """
        Transform one Tennis Props row into Universal Agent Core contracts.

        Parameters
        ----------
        row               WOW scoring row dict (read-only). Row wins on collision.
        run_id            Caller-supplied run identifier.
        enrichment        Optional dict (event_status, game_log, matchup…).
        snapshot_id       Optional deterministic snapshot_id override for tests.
        acquisition_error Optional error string for provider/backend failures.
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
            return TennisPropsAdapterResult(
                packet=stub,
                role_payloads={},
                adapter_status=AdapterStatus.TECHNICAL_FAILURE,
                degradation_reasons=(f"ACQUISITION_ERROR: {acquisition_error}",),
                source_row_fields_used=(),
                failure_classification=failure,
                ceiling_result=ceiling_result,
            )

        # ── Normal processing path ────────────────────────────────────────────
        validate_tennis_props_row(row)

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
            lane=Lane.TENNIS_PROPS,
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
        return TennisPropsAdapterResult(
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
    p1 = str(r.get("player_1") or r.get("player") or "UNKNOWN")
    p2 = str(r.get("player_2") or r.get("opponent") or "UNKNOWN")
    return build_evidence_packet(
        run_id=run_id,
        canonical_event_id=event_id,
        lane=Lane.TENNIS_PROPS,
        snapshot_id=snapshot_id,
        event_name=f"{p1} vs {p2}" if p1 != "UNKNOWN" else "",
        event_date=None,
        player_id=None,
        player_name=p1,
        team_id=p1,
        team_name=p1,
        opponent_team_id=p2,
        opponent_team_name=p2,
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
        "active_status":   rs.get("active_status"),
        "injury_flag":     bool(rs.get("injury_flag")),
        "withdrawal_risk": bool(rs.get("withdrawal_risk")),
    }
