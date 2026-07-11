"""
skills/adapters/promo_optimizer.py
Sportsbook Promotion Optimizer adapter (stub).
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.sportsbook-promo-optimizer"
SKILL_VERSION = "1.0.0"


class PromoOptimizerAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)
        promo_type = context.get("promo_type", "")
        if not promo_type:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID, skill_version=self.SKILL_VERSION,
                inputs=inputs, reason="promo_type required.", run_id=run_id)
        return SkillResult(
            skill_id=self.SKILL_ID, skill_version=self.SKILL_VERSION,
            run_id=run_id or None, inputs_used=inputs,
            sources=[], data_quality="incomplete",
            findings=[{"promo_type": promo_type}],
            blockers=[],
            label=SkillLabel.SCOUT.value, confidence=0.2,
            can_execute=False, downstream=["wow.qa-hallucination-auditor"])
