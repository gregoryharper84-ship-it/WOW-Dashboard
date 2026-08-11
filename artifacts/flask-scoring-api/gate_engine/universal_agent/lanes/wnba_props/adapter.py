"""
gate_engine/universal_agent/lanes/wnba_props/adapter.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4

WNBA/NBA Props Lane Adapter — public entry point.

Transforms one WOW WNBA/NBA props evidence row + optional enrichment into:
  - EvidencePacket (lane=Lane.WNBA_PROPS, frozen)
  - Six validated B1 advisory role payloads
  - game_script_shadow — provisional game-script distribution shadow output
    (ceiling MODEL_QUALIFIED_HOLD, can_execute=False, best-effort)

Invariants
----------
- can_execute = False
- No live LLM, API, or network calls
- No app.py import or Flask route wiring
- Row fields take precedence over enrichment on key collision
- Missing fields degrade gracefully to UNKNOWN / MISSING sentinels
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
from gate_engine.universal_agent.roles.data_slate_integrity import ROLE_ID as DSI_ROLE_ID
from gate_engine.universal_agent.roles.news_status import ROLE_ID as NS_ROLE_ID
from gate_engine.universal_agent.roles.market_exact_line import ROLE_ID as MEL_ROLE_ID
from gate_engine.universal_agent.roles.sport_specialist import ROLE_ID as SS_ROLE_ID
from gate_engine.universal_agent.roles.failure_contradiction import ROLE_ID as FC_ROLE_ID
from gate_engine.universal_agent.roles.final_refresh import ROLE_ID as FR_ROLE_ID

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
ADAPTER_MODULE  = "wnba_props_adapter"
ADAPTER_VERSION = "v1.0"


class AdapterStatus:
    """
    COMPLETE — all six role payloads valid, all eight coverage fields present.
    DEGRADED — all six role payloads valid, but some evidence fields absent.
               Downstream orchestrator still runs; payloads use UNKNOWN/MISSING.
    """
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class WnbaPropsAdapterResult:
    """
    Immutable result of one WNBA props adapter run.

    game_script_shadow:
        Provisional game-script distribution output (dict) or None.
        Shadow / advisory only — ceiling MODEL_QUALIFIED_HOLD.
        None when game-script inputs are insufficient (fail-closed).
    """
    packet:                EvidencePacket
    role_payloads:         dict    # role_id -> validated B1 payload
    adapter_status:        str     # AdapterStatus constant
    degradation_reasons:   tuple   # ("MISSING:field_key", ...)
    source_row_fields_used: tuple
    game_script_shadow:    dict | None = None


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
        enrichment: dict | None = None,
        snapshot_id: str | None = None,
    ) -> WnbaPropsAdapterResult:
        """
        Parameters
        ----------
        row         WOW scoring row dict (read-only). Row wins on key collision.
        run_id      Caller-supplied run identifier (echoed in EvidencePacket).
        enrichment  Optional dict (event_status, game_log, market_comparison…).
        snapshot_id Optional deterministic snapshot_id override for tests.

        Raises AdapterInputError for identity/scope failures.
        Raises RoleInputBuildError for field_map derivation bugs.
        """
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

        game_script_shadow = _run_game_script_shadow(combined, run_id)

        data_gaps = build_data_gaps(combined)
        status    = AdapterStatus.DEGRADED if data_gaps else AdapterStatus.COMPLETE
        reasons   = tuple(data_gaps)

        return WnbaPropsAdapterResult(
            packet=packet,
            role_payloads=role_payloads,
            adapter_status=status,
            degradation_reasons=reasons,
            source_row_fields_used=SOURCE_ROW_FIELDS_USED,
            game_script_shadow=game_script_shadow,
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


def _run_game_script_shadow(combined: dict, run_id: str) -> dict | None:
    """
    Invoke the game-script shadow gate. Best-effort — any exception returns None.
    Result is advisory/provisional only; never alters role payloads or adapter_status.
    Ceiling: MODEL_QUALIFIED_HOLD (set inside the shadow gate).
    """
    try:
        from gate_engine.universal_agent.lanes.wnba_props.game_script.shadow_gate import (
            GameScriptShadowGate,
        )
        return GameScriptShadowGate().run(combined=combined, run_id=run_id)
    except Exception:
        return None
