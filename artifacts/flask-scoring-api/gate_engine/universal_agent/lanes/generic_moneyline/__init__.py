"""
gate_engine/universal_agent/lanes/generic_moneyline/__init__.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B7

Generic Moneyline Lane — public surface.

Handles moneyline / winner / h2h markets for any sport that does NOT have a
dedicated UAC lane (NFL, NHL, NBA moneyline, soccer, college sports, etc.).

Sports with dedicated lanes (MLB_MONEYLINE, MLB_PROPS, WNBA_PROPS,
TENNIS_PROPS) are explicitly rejected — callers must route those rows to
their own adapters.

    from gate_engine.universal_agent.lanes.generic_moneyline import (
        GenericMoneylineAdapter, AdapterInputError
    )

can_execute = False
"""
from gate_engine.universal_agent.lanes.generic_moneyline.adapter import (
    GenericMoneylineAdapter,
    GenericMoneylineAdapterResult,
    AdapterStatus,
)
from gate_engine.universal_agent.lanes.generic_moneyline.validation import AdapterInputError

__all__ = [
    "GenericMoneylineAdapter",
    "GenericMoneylineAdapterResult",
    "AdapterStatus",
    "AdapterInputError",
]

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
