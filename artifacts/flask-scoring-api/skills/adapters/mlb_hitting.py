"""
skills/adapters/mlb_hitting.py
MLB Hitting Expert adapter.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.mlb-hitting-expert"
SKILL_VERSION = "1.0.0"


class MlbHittingAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        lineup_confirmed = context.get("mlb_lineup_confirmed")
        if lineup_confirmed is False:
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="MLB_LINEUP_NOT_CONFIRMED",
                message="Lineup not confirmed — cannot model hitting props.",
                label=SkillLabel.HOLD.value,
                run_id=run_id,
            )

        batter    = context.get("batter_name", "")
        xwoba     = context.get("xwoba")
        findings: list[dict] = []
        if batter:
            findings.append({"batter": batter, "lineup_confirmed": lineup_confirmed})
        if xwoba is not None:
            findings.append({"xwoba": float(xwoba)})

        if not findings:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason="MLB hitting context (batter_name, xwoba) required.",
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
            label=SkillLabel.SCOUT.value,
            confidence=0.5,
            can_execute=False,
            downstream=["wow.game-script-simulator"],
        )
