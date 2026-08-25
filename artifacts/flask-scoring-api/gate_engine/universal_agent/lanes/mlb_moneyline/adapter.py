"""
gate_engine/universal_agent/lanes/mlb_moneyline/adapter.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A

MLB Moneyline Lane Adapter — public entry point.

Transforms one WOW/LLP MLB moneyline evidence row (read-only, post-preflight)
into the Universal Agent Core contracts:
  - One immutable EvidencePacket (lane=Lane.MLB_MONEYLINE, frozen dataclass).
  - Six validated B1 advisory role input payloads (one per role).

Usage
-----
    from gate_engine.universal_agent.lanes.mlb_moneyline import (
        MlbMoneylineAdapter, AdapterInputError
    )

    adapter = MlbMoneylineAdapter()
    try:
        result = adapter.adapt(row=scoring_row, run_id="llp-run-abc")
    except AdapterInputError as exc:
        # sport / market mismatch, or event_id missing
        log_failure(exc.code, exc.message)
        return

    # result.packet       — frozen EvidencePacket, Lane.MLB_MONEYLINE
    # result.role_payloads — dict[role_id → validated B1 payload]
    # result.adapter_status — "COMPLETE" or "DEGRADED"
    # result.degradation_reasons — tuple of gap descriptors

Invariants
----------
- can_execute = False: adapter is a pure data transformer.
- No live LLM, API, or network calls.
- No app.py import or Flask route wiring.
- All authority remains in existing WOW/LLP decision logic.
- Never fabricates probability values or status strings.
- Missing fields degrade to UNKNOWN / MISSING — never to fabricated estimates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gate_engine.universal_agent.evidence_packet import EvidencePacket, Lane, build_evidence_packet
from gate_engine.universal_agent.lanes.mlb_moneyline.field_map import (
    SOURCE_ROW_FIELDS_USED,
    build_data_gaps,
    extract_canonical_event_id,
    extract_deterministic_model_inputs,
    extract_event_date,
    extract_event_name,
    extract_market_snapshot,
    extract_source_conflicts,
    extract_source_failures,
    extract_source_provenance,
    extract_source_timestamps,
    extract_team_identity,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.role_inputs import (
    build_data_slate_integrity_input,
    build_failure_contradiction_input,
    build_final_refresh_input,
    build_market_exact_line_input,
    build_news_status_input,
    build_sport_specialist_input,
    RoleInputBuildError,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.validation import (
    AdapterInputError,
    validate_mlb_moneyline_row,
)
from gate_engine.universal_agent.roles.data_slate_integrity import ROLE_ID as DSI_ROLE_ID
from gate_engine.universal_agent.roles.news_status import ROLE_ID as NS_ROLE_ID
from gate_engine.universal_agent.roles.market_exact_line import ROLE_ID as MEL_ROLE_ID
from gate_engine.universal_agent.roles.sport_specialist import ROLE_ID as SS_ROLE_ID
from gate_engine.universal_agent.roles.failure_contradiction import ROLE_ID as FC_ROLE_ID
from gate_engine.universal_agent.roles.final_refresh import ROLE_ID as FR_ROLE_ID

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
ADAPTER_MODULE  = "mlb_moneyline_adapter"
ADAPTER_VERSION = "v1.0"


class AdapterStatus:
    """
    Adapter result status constants.

    COMPLETE — EvidencePacket built, all six role payloads valid,
               all eight coverage evidence fields are present.
    DEGRADED — EvidencePacket built, all six role payloads valid,
               but one or more evidence fields were absent (gaps present).
               Downstream orchestrator can still run; role payloads
               contain explicit UNKNOWN / MISSING values.
    """
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class MlbMoneylineAdapterResult:
    """
    Immutable result of one MLB moneyline adapter run.

    Fields
    ------
    packet
        Frozen EvidencePacket with lane=Lane.MLB_MONEYLINE.
    role_payloads
        Dict mapping each of the six B1 role_ids to a validated payload dict.
        All six payloads are always present (no partial failure — the adapter
        either succeeds for all six or raises).
    adapter_status
        "COMPLETE" or "DEGRADED". See AdapterStatus constants.
    degradation_reasons
        Tuple of "MISSING:{field_key}" strings for each absent evidence field.
        Empty tuple when adapter_status == COMPLETE.
    source_row_fields_used
        Fixed tuple of all row field names this adapter reads.
        Serves as an audit trail of the adapter's read surface.
    """
    packet:                EvidencePacket
    role_payloads:         dict          # role_id → validated payload dict
    adapter_status:        str           # AdapterStatus constant
    degradation_reasons:   tuple         # ("MISSING:field_key", ...)
    source_row_fields_used: tuple        # all field names read from row


class MlbMoneylineAdapter:
    """
    Transforms a WOW/LLP MLB moneyline evidence row into Universal Agent Core
    contracts (EvidencePacket + six B1 advisory role payloads).

    The adapter is stateless and re-entrant. One instance may be used for
    multiple adapt() calls.

    can_execute = False — no orders, no wagers, no market mutations.
    """

    def adapt(
        self,
        *,
        row: Any,
        run_id: str,
        snapshot_id: str | None = None,
    ) -> MlbMoneylineAdapterResult:
        """
        Transform one MLB moneyline row into Universal Agent Core contracts.

        Parameters
        ----------
        row
            WOW/LLP scoring row dict (read-only). Must be an MLB moneyline
            candidate with a non-empty event_id.
        run_id
            Caller-supplied run identifier (propagated into EvidencePacket.run_id).
        snapshot_id
            Optional: override the auto-generated UUID snapshot_id. Useful
            for deterministic test assertions.

        Returns
        -------
        MlbMoneylineAdapterResult (frozen dataclass).

        Raises
        ------
        AdapterInputError
            If row is not a dict, sport is not MLB, market is not a
            winner/moneyline type, or event_id is missing/empty.
        RoleInputBuildError
            If field_map produces an enum value that fails B1 validation.
            This indicates a bug in field_map derivation logic.
        """
        # ── Step 1: identity and scope validation ─────────────────────────────
        validate_mlb_moneyline_row(row)

        if not isinstance(run_id, str) or not run_id.strip():
            raise AdapterInputError(
                "ADAPTER_MISSING_RUN_ID",
                "adapt() requires a non-empty run_id string",
            )

        # ── Step 2: extract EvidencePacket fields ─────────────────────────────
        canonical_event_id = extract_canonical_event_id(row)
        team_ids           = extract_team_identity(row)

        packet = build_evidence_packet(
            run_id=run_id,
            canonical_event_id=canonical_event_id,
            lane=Lane.MLB_MONEYLINE,
            snapshot_id=snapshot_id,
            event_name=extract_event_name(row),
            event_date=extract_event_date(row),
            player_id=None,    # team-level market — no individual player
            player_name=None,
            team_id=team_ids["team_id"],
            team_name=team_ids["team_name"],
            opponent_team_id=team_ids["opponent_team_id"],
            opponent_team_name=team_ids["opponent_team_name"],
            source_timestamps=extract_source_timestamps(row),
            source_provenance=extract_source_provenance(row),
            market_snapshot=extract_market_snapshot(row),
            injury_status_evidence={},   # team market; no per-player injury slot
            deterministic_model_inputs=extract_deterministic_model_inputs(row),
            source_failures=extract_source_failures(row),
            source_conflicts=extract_source_conflicts(row),
        )

        # ── Step 3: build all six B1 role payloads ────────────────────────────
        role_payloads: dict[str, dict] = {
            DSI_ROLE_ID: build_data_slate_integrity_input(row),
            NS_ROLE_ID:  build_news_status_input(row),
            MEL_ROLE_ID: build_market_exact_line_input(row),
            SS_ROLE_ID:  build_sport_specialist_input(row),
            FC_ROLE_ID:  build_failure_contradiction_input(row),
            FR_ROLE_ID:  build_final_refresh_input(row),
        }

        # ── Step 4: determine adapter status ──────────────────────────────────
        data_gaps = build_data_gaps(row)
        if data_gaps:
            status  = AdapterStatus.DEGRADED
            reasons = tuple(data_gaps)
        else:
            status  = AdapterStatus.COMPLETE
            reasons = ()

        return MlbMoneylineAdapterResult(
            packet=packet,
            role_payloads=role_payloads,
            adapter_status=status,
            degradation_reasons=reasons,
            source_row_fields_used=SOURCE_ROW_FIELDS_USED,
        )
