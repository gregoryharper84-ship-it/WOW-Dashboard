"""
gate_engine/universal_agent/shadow/__init__.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3B

Shadow integration package — offline, default-off MLB Moneyline pipeline.

Connects:
  B3A MlbMoneylineAdapter  →  Universal EvidencePacket + six B1 role payloads
  DeterministicAdapterRunner  →  no LLM/API calls, wraps adapter outputs
  B2 run_orchestrator  →  EvidenceBundle + persistence to uac_* tables

The pipeline is OFF by default. It is never wired into production app.py routes.
No live Anthropic, OpenAI, or HTTP calls are made at any point.

can_execute = False
SHADOW_ENABLED = False  (see shadow_pipeline.SHADOW_ENABLED)

Public API
----------
    from gate_engine.universal_agent.shadow import (
        DeterministicAdapterRunner,
        ShadowPipeline,
        ShadowPipelineResult,
        ShadowPipelineStatus,
        run_shadow_pipeline,
        SHADOW_ENABLED,
    )
"""
from gate_engine.universal_agent.shadow.deterministic_runner import (
    DeterministicAdapterRunner,
)
from gate_engine.universal_agent.shadow.shadow_pipeline import (
    ShadowPipeline,
    ShadowPipelineResult,
    ShadowPipelineStatus,
    run_shadow_pipeline,
    SHADOW_ENABLED,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

__all__ = [
    "DeterministicAdapterRunner",
    "ShadowPipeline",
    "ShadowPipelineResult",
    "ShadowPipelineStatus",
    "run_shadow_pipeline",
    "SHADOW_ENABLED",
    "can_execute",
    "EXECUTION_RULE",
]
