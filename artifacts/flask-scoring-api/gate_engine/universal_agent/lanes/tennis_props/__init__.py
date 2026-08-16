"""
gate_engine/universal_agent/lanes/tennis_props/__init__.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B6

Tennis Props Lane — public surface.

    from gate_engine.universal_agent.lanes.tennis_props import (
        TennisPropsAdapter, AdapterInputError
    )

can_execute = False
"""
from gate_engine.universal_agent.lanes.tennis_props.adapter import (
    TennisPropsAdapter,
    TennisPropsAdapterResult,
    AdapterStatus,
)
from gate_engine.universal_agent.lanes.tennis_props.validation import AdapterInputError

__all__ = [
    "TennisPropsAdapter",
    "TennisPropsAdapterResult",
    "AdapterStatus",
    "AdapterInputError",
]

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
