"""
gate_engine/universal_agent/lanes/wnba_props/game_script/shadow_gate.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Game-Script Shadow Gate — top-level orchestrator.

Runs the full game-script distribution pipeline in shadow / advisory mode:
  1. parse_game_environment      (requires spread + total_line in combined)
  2. derive_script_priors
  3. derive_player_states
  4. compute_minutes_estimates
  5. compute_conditional_hit_probs
  6. aggregate_unconditional_probability
  7. compute_fragility

Returns a structured dict suitable for inclusion in WnbaPropsAdapterResult
as game_script_shadow. Never raises — all failures produce SCRIPT_UNAVAILABLE.

Governance invariants (hardcoded, unconditional)
-----------------------------------------------
  can_execute             = False
  PRODUCTION_AUTHORITY    = False
  USER_OUTPUT_AUTHORITY   = False
  CAPITAL_AUTHORITY       = False
  CEILING                 = "MODEL_QUALIFIED_HOLD"
  SHADOW_ONLY             = True
  PATCH_ID                = "WOW-PATCH-2026-08-11-GAMESCRIPT-SHADOW"

The shadow output is advisory / provisional only. It MUST NOT be used to
set terminal labels, generate user-facing picks, or influence any capital
allocation until the promotion gate in model_validation grants APPROVED status.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

can_execute           = False
PRODUCTION_AUTHORITY  = False
USER_OUTPUT_AUTHORITY = False
CAPITAL_AUTHORITY     = False
EXECUTION_RULE        = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
CEILING               = "MODEL_QUALIFIED_HOLD"
SHADOW_ONLY           = True
PATCH_ID              = "WOW-PATCH-2026-08-11-GAMESCRIPT-SHADOW"


class GAME_SCRIPT_SHADOW_STATUS:
    COMPLETE    = "COMPLETE"       # all 7 pipeline stages succeeded
    PARTIAL     = "PARTIAL"        # some stages succeeded (fragility may be unavailable)
    UNAVAILABLE = "SCRIPT_UNAVAILABLE"  # fail-closed: required inputs missing


@dataclass(frozen=True)
class GameScriptShadowResult:
    """
    Structured output of a game-script shadow run.

    status:              COMPLETE | PARTIAL | SCRIPT_UNAVAILABLE
    unconditional_prob:  P(hit) from weighted aggregation, or None
    fragility_label:     "LOW" | "MEDIUM" | "HIGH" | None
    fragility_range:     float or None
    dominant_script:     str or None
    script_priors:       {script: prior_weight} or {}
    conditional_probs:   {script: P(hit|script)} (available scripts only) or {}
    effective_minutes:   {script: effective_minutes} or {}
    scripts_used:        int
    ceiling:             always "MODEL_QUALIFIED_HOLD"
    patch_id:            version identifier
    unavailable_reason:  str or None (set when status=SCRIPT_UNAVAILABLE)
    """
    status:             str
    unconditional_prob: float | None
    fragility_label:    str | None
    fragility_range:    float | None
    dominant_script:    str | None
    script_priors:      dict
    conditional_probs:  dict
    effective_minutes:  dict
    scripts_used:       int
    ceiling:            str
    patch_id:           str
    unavailable_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":             self.status,
            "unconditional_prob": self.unconditional_prob,
            "fragility_label":    self.fragility_label,
            "fragility_range":    self.fragility_range,
            "dominant_script":    self.dominant_script,
            "script_priors":      self.script_priors,
            "conditional_probs":  self.conditional_probs,
            "effective_minutes":  self.effective_minutes,
            "scripts_used":       self.scripts_used,
            "ceiling":            self.ceiling,
            "patch_id":           self.patch_id,
            "unavailable_reason": self.unavailable_reason,
            "can_execute":        False,
            "shadow_only":        True,
        }


class GameScriptShadowGate:
    """
    Orchestrates the 7-stage game-script pipeline.
    Stateless — one instance may serve multiple run() calls.
    can_execute = False.
    """

    def run(self, *, combined: dict, run_id: str) -> dict:
        """
        Execute the full game-script pipeline. Never raises.

        Returns the result of GameScriptShadowResult.to_dict().
        On any failure returns SCRIPT_UNAVAILABLE dict.
        """
        try:
            return self._run_pipeline(combined=combined, run_id=run_id)
        except Exception as exc:
            return _unavailable(f"pipeline_exception:{type(exc).__name__}:{exc}")

    def _run_pipeline(self, *, combined: dict, run_id: str) -> dict:
        from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
            parse_game_environment, derive_script_priors,
        )
        from gate_engine.universal_agent.lanes.wnba_props.game_script.player_state import (
            derive_player_states,
        )
        from gate_engine.universal_agent.lanes.wnba_props.game_script.minutes_distribution import (
            compute_minutes_estimates,
        )
        from gate_engine.universal_agent.lanes.wnba_props.game_script.conditional_hit_prob import (
            compute_conditional_hit_probs,
        )
        from gate_engine.universal_agent.lanes.wnba_props.game_script.unconditional_aggregator import (
            aggregate_unconditional_probability,
        )
        from gate_engine.universal_agent.lanes.wnba_props.game_script.script_fragility import (
            compute_fragility,
        )

        # Stage 1: parse game environment (requires spread + total_line)
        env = parse_game_environment(combined)
        if env is None:
            return _unavailable("missing_spread_or_total_line")

        # Stage 2: derive script priors
        priors = derive_script_priors(env)

        # Stage 3: player states
        player_states = derive_player_states(combined)

        # Stage 4: minutes estimates
        sport = (combined.get("sport") or "WNBA").strip().upper()
        minutes_ests = compute_minutes_estimates(player_states, sport=sport)

        # Stage 5: line + stat_key (required for conditional probs)
        line = combined.get("line")
        if line is None:
            return _unavailable("missing_line")
        stat_key_raw = combined.get("market") or combined.get("prop_type") or ""
        if not stat_key_raw:
            return _unavailable("missing_market_or_prop_type")

        conditionals = compute_conditional_hit_probs(
            combined=combined,
            minutes_estimates=minutes_ests,
            line=float(line),
            stat_key_raw=str(stat_key_raw),
        )

        # Stage 6: unconditional aggregation
        unconditional = aggregate_unconditional_probability(
            priors=priors,
            conditionals=conditionals,
            stat_key=stat_key_raw,
            line=float(line),
        )

        # Stage 7: fragility
        fragility = compute_fragility(unconditional=unconditional, priors=priors)

        # Assemble effective_minutes summary
        eff_min_map = {
            s: (me.effective_minutes if me.available else None)
            for s, me in minutes_ests.items()
        }

        status = (
            GAME_SCRIPT_SHADOW_STATUS.COMPLETE
            if unconditional.available and fragility.available
            else GAME_SCRIPT_SHADOW_STATUS.PARTIAL
            if unconditional.available
            else GAME_SCRIPT_SHADOW_STATUS.UNAVAILABLE
        )

        result = GameScriptShadowResult(
            status=status,
            unconditional_prob=unconditional.probability,
            fragility_label=fragility.fragility_label,
            fragility_range=fragility.fragility_range,
            dominant_script=fragility.dominant_script,
            script_priors=priors.as_dict(),
            conditional_probs=unconditional.conditional_probs,
            effective_minutes=eff_min_map,
            scripts_used=unconditional.scripts_used,
            ceiling=CEILING,
            patch_id=PATCH_ID,
            unavailable_reason=None,
        )
        return result.to_dict()


def _unavailable(reason: str) -> dict:
    return GameScriptShadowResult(
        status=GAME_SCRIPT_SHADOW_STATUS.UNAVAILABLE,
        unconditional_prob=None,
        fragility_label=None,
        fragility_range=None,
        dominant_script=None,
        script_priors={},
        conditional_probs={},
        effective_minutes={},
        scripts_used=0,
        ceiling=CEILING,
        patch_id=PATCH_ID,
        unavailable_reason=reason,
    ).to_dict()
