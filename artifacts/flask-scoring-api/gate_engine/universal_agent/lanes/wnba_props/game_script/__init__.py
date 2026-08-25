"""
gate_engine/universal_agent/lanes/wnba_props/game_script/__init__.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

WNBA/NBA Game-Script Distribution Expert — public API.

Governed shadow layer. Produces a probabilistic game-script decomposition of
a player props market:

  GameEnvironment → ScriptPriors → PlayerState (per script)
    → MinutesDistribution → ConditionalHitProbability (per script)
    → UnconditionalHitProbability → ScriptFragility

Ceiling: MODEL_QUALIFIED_HOLD (PROVISIONAL — no calibration evidence yet).
can_execute = False — shadow/advisory only; never alters production formulas.
"""
from gate_engine.universal_agent.lanes.wnba_props.game_script.shadow_gate import (
    GameScriptShadowGate,
    GameScriptShadowResult,
    GAME_SCRIPT_SHADOW_STATUS,
)

__all__ = [
    "GameScriptShadowGate",
    "GameScriptShadowResult",
    "GAME_SCRIPT_SHADOW_STATUS",
]
