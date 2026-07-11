"""
skills/adapters/financial_market.py
Financial Market Analyst adapter (stub — no live market feed available).
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.financial-market-analyst"
SKILL_VERSION = "1.0.0"


class FinancialMarketAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)
        ticker = context.get("financial_ticker", "")
        if not ticker:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID, skill_version=self.SKILL_VERSION,
                inputs=inputs, reason="financial_ticker required.", run_id=run_id)
        return SkillResult(
            skill_id=self.SKILL_ID, skill_version=self.SKILL_VERSION,
            run_id=run_id or None, inputs_used=inputs,
            sources=[], data_quality="incomplete",
            findings=[{"ticker": ticker, "note": "Financial market data not available in this environment."}],
            blockers=[{"code": "FINANCIAL_FEED_UNAVAILABLE",
                       "message": "No live financial market feed configured.", "fatal": False}],
            label=SkillLabel.SCOUT.value, confidence=0.1,
            can_execute=False, downstream=["wow.qa-hallucination-auditor"])
