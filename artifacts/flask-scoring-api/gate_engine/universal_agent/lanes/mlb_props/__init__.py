"""
gate_engine/universal_agent/lanes/mlb_props/__init__.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

MLB Props Lane — public surface.

    from gate_engine.universal_agent.lanes.mlb_props import (
        MlbPropsAdapter, AdapterInputError
    )

can_execute = False
"""
from gate_engine.universal_agent.lanes.mlb_props.adapter import (
    MlbPropsAdapter,
    MlbPropsAdapterResult,
    AdapterStatus,
)
from gate_engine.universal_agent.lanes.mlb_props.validation import AdapterInputError

__all__ = [
    "MlbPropsAdapter",
    "MlbPropsAdapterResult",
    "AdapterStatus",
    "AdapterInputError",
]

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
