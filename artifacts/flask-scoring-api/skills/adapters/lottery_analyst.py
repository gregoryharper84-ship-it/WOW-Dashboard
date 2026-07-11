"""
skills/adapters/lottery_analyst.py
Lottery Analyst adapter.

No predictive certainty. Routes to QA only. Never to Market/Odds or Risk.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.lottery-analyst"
SKILL_VERSION = "1.0.0"


class LotteryAnalystAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        game_name = context.get("lottery_game", "")
        jackpot   = context.get("jackpot_amount")
        ticket_price = context.get("ticket_price")

        findings: list[dict] = []
        calculations: list[dict] = []

        if not game_name:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason="lottery_game name required.",
                run_id=run_id,
            )

        findings.append({
            "game": game_name,
            "jackpot": jackpot,
            "no_predictive_certainty": True,
            "note": "Lottery analysis is descriptive only; no edge prediction.",
        })

        # Simple EV estimate if jackpot and odds provided
        odds_of_winning = context.get("odds_of_winning")
        if jackpot is not None and ticket_price is not None and odds_of_winning is not None:
            ev = float(jackpot) * float(odds_of_winning) - float(ticket_price)
            calculations.append({
                "op": "lottery_ev",
                "jackpot": jackpot,
                "ticket_price": ticket_price,
                "odds": odds_of_winning,
                "ev": ev,
                "note": "Negative house-edge expected; no predictive edge.",
            })
            findings.append({"estimated_ev": ev})

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "official_lottery_site", "quality": 1}],
            data_quality="complete",
            findings=findings,
            calculations=calculations,
            blockers=[],
            # Lottery is always SCOUT — never READY; no guaranteed edge
            label=SkillLabel.SCOUT.value,
            confidence=0.1,
            can_execute=False,
            downstream=["wow.qa-hallucination-auditor"],
        )
