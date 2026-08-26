"""
gate_engine/universal_agent/lanes/mlb_props/event_tree/__init__.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

1IP and other MLB Props event-tree routing module.
can_execute = False
"""
from gate_engine.universal_agent.lanes.mlb_props.event_tree.one_ip_gate import (
    OneIpGate,
    OneIpGateResult,
    ONE_IP_EVENT_TREE_ID,
)

__all__ = ["OneIpGate", "OneIpGateResult", "ONE_IP_EVENT_TREE_ID"]

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
