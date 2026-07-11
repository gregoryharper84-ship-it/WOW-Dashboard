"""
skills/adapters/game_script.py
Game Script Simulator adapter.

Models scenario survival and script-adjusted probability.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.game-script-simulator"
SKILL_VERSION = "1.0.0"

REQUIRED_SCENARIOS = [
    "close_game", "favorite_blowout", "underdog_upset",
    "pace_total_bands", "script_adjusted_probability",
]


class GameScriptAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        scenarios = context.get("game_script_scenarios") or {}
        script_prob = context.get("script_adjusted_probability")

        findings: list[dict] = []
        blockers: list[dict] = []

        if not scenarios:
            return SkillResult.scout(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                findings=[{"note": "No game script scenarios provided; returning SCOUT."}],
                reason="GAME_SCRIPT_NO_SCENARIOS: upstream context required.",
                run_id=run_id,
            )

        for scenario in REQUIRED_SCENARIOS:
            if scenario not in scenarios:
                findings.append({"missing_scenario": scenario})

        if script_prob is not None:
            findings.append({"script_adjusted_probability": float(script_prob)})

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[],
            data_quality="complete" if not findings else "partial",
            findings=findings,
            blockers=blockers,
            label=SkillLabel.SCOUT.value,
            confidence=0.45,
            can_execute=False,
            downstream=["wow.market-odds-intelligence"],
        )
