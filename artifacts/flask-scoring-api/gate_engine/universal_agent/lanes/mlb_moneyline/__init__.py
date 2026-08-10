"""
gate_engine/universal_agent/lanes/mlb_moneyline/__init__.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A

MLB Moneyline lane adapter public API.

Maps an existing WOW/LLP MLB moneyline evidence row (post-preflight, read-only)
into the Universal Agent Core contracts:
  - EvidencePacket   (lane=Lane.MLB_MONEYLINE)
  - Six B1 advisory-role input payloads (one per role, validated)

Usage
-----
    from gate_engine.universal_agent.lanes.mlb_moneyline import (
        MlbMoneylineAdapter,
        MlbMoneylineAdapterResult,
        AdapterInputError,
    )

    try:
        result = MlbMoneylineAdapter().adapt(row=scoring_row, run_id="run-xyz")
    except AdapterInputError as exc:
        # required identity fields missing or sport/market mismatch
        handle_error(exc.code, exc.message)

can_execute = False — all authority remains in existing WOW decision logic.
"""
from gate_engine.universal_agent.lanes.mlb_moneyline.adapter import (
    MlbMoneylineAdapter,
    MlbMoneylineAdapterResult,
    AdapterStatus,
)
from gate_engine.universal_agent.lanes.mlb_moneyline.validation import AdapterInputError

__all__ = [
    "MlbMoneylineAdapter",
    "MlbMoneylineAdapterResult",
    "AdapterStatus",
    "AdapterInputError",
]
