"""
gate_engine/universal_agent/lanes/wnba_props/__init__.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4

WNBA/NBA Props lane adapter public API.

Maps a WOW WNBA/NBA props evidence row (post-acquisition, read-only) plus
optional enrichment into the Universal Agent Core contracts:
  - EvidencePacket   (lane=Lane.WNBA_PROPS)
  - Six B1 advisory-role input payloads (one per role, validated)
  - game_script_shadow — provisional game-script distribution shadow output
    (ceiling MODEL_QUALIFIED_HOLD, can_execute=False)

can_execute = False — all authority remains in existing WOW decision logic.
"""
from gate_engine.universal_agent.lanes.wnba_props.adapter import (
    WnbaPropsAdapter,
    WnbaPropsAdapterResult,
    AdapterStatus,
)
from gate_engine.universal_agent.lanes.wnba_props.validation import AdapterInputError

__all__ = [
    "WnbaPropsAdapter",
    "WnbaPropsAdapterResult",
    "AdapterStatus",
    "AdapterInputError",
]
