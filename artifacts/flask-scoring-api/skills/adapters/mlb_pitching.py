"""
skills/adapters/mlb_pitching.py
MLB Pitching Expert adapter.

Models pitcher props using existing WOW MLB Stats API pipeline.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.mlb-pitching-expert"
SKILL_VERSION = "1.0.0"


class MlbPitchingAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        starter = context.get("mlb_starter_confirmed")
        if starter is False:
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="MLB_STARTER_NOT_CONFIRMED",
                message="Starter not confirmed — cannot model pitching props.",
                label=SkillLabel.HOLD.value,
                run_id=run_id,
            )

        pitcher_name = context.get("pitcher_name", "")
        k_rate       = context.get("k_rate")
        findings: list[dict] = []
        if pitcher_name:
            findings.append({"pitcher": pitcher_name, "starter_confirmed": starter})
        if k_rate is not None:
            findings.append({"k_rate": float(k_rate)})

        label = SkillLabel.SCOUT.value
        if not findings:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason="MLB pitching context (pitcher_name, k_rate) required.",
                run_id=run_id,
            )

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "mlb_stats_api", "quality": 1}],
            data_quality="complete",
            findings=findings,
            blockers=[],
            label=label,
            confidence=0.5,
            can_execute=False,
            downstream=["wow.game-script-simulator"],
        )
