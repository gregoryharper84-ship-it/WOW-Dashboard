"""
skills/adapters/market_odds.py
Market & Odds Intelligence adapter.

Calls llp_odds_resolver logic for no-vig and edge calculations.
Operator-supplied prices cap at WATCH (acceptance test 7).
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult, SOURCE_QUALITY_OPERATOR_SUPPLIED
from .base import BaseSkillAdapter

SKILL_ID      = "wow.market-odds-intelligence"
SKILL_VERSION = "1.0.0"


def _american_to_decimal(american: float) -> float:
    if american > 0:
        return (american / 100) + 1
    return (100 / abs(american)) + 1


def _decimal_to_prob(decimal: float) -> float:
    if decimal <= 1.0:
        return 0.0
    return 1.0 / decimal


def _no_vig(prob_home: float, prob_away: float) -> tuple[float, float]:
    total = prob_home + prob_away
    if total <= 0:
        return 0.5, 0.5
    return prob_home / total, prob_away / total


class MarketOddsAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        # Determine source type
        source_type = context.get("odds_source_type", "direct")
        sources: list[dict] = []

        if source_type in ("screenshot", "operator_supplied"):
            sources.append({"source_id": source_type, "quality": SOURCE_QUALITY_OPERATOR_SUPPLIED})
            return SkillResult.watch(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                findings=[{"note": "Operator-supplied odds cap at WATCH."}],
                reason="OPERATOR_SUPPLIED_ODDS: screenshot or user-entered price cannot be a direct/live source.",
                confidence=0.25,
                run_id=run_id,
            )

        sources.append({"source_id": "odds_api", "quality": 2})

        # Resolve odds to no-vig probability
        home_american = context.get("home_american_odds")
        away_american = context.get("away_american_odds")
        home_decimal  = context.get("home_decimal_odds")
        away_decimal  = context.get("away_decimal_odds")

        calculations: list[dict] = []
        findings:     list[dict] = []

        if home_american is not None and away_american is not None:
            hd = _american_to_decimal(float(home_american))
            ad = _american_to_decimal(float(away_american))
            hp = _decimal_to_prob(hd)
            ap = _decimal_to_prob(ad)
            nv_home, nv_away = _no_vig(hp, ap)
            calculations.append({
                "op": "no_vig",
                "home_american": home_american,
                "away_american": away_american,
                "home_decimal": hd,
                "away_decimal": ad,
                "raw_home_prob": hp,
                "raw_away_prob": ap,
                "no_vig_home": nv_home,
                "no_vig_away": nv_away,
            })
            findings.append({"no_vig_home": nv_home, "no_vig_away": nv_away})
        elif home_decimal is not None and away_decimal is not None:
            hp = _decimal_to_prob(float(home_decimal))
            ap = _decimal_to_prob(float(away_decimal))
            nv_home, nv_away = _no_vig(hp, ap)
            calculations.append({
                "op": "no_vig_decimal",
                "home_decimal": home_decimal,
                "away_decimal": away_decimal,
                "no_vig_home": nv_home,
                "no_vig_away": nv_away,
            })
            findings.append({"no_vig_home": nv_home, "no_vig_away": nv_away})

        if not findings:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason="No odds provided (home_american_odds, away_american_odds, or decimal equivalents required).",
                run_id=run_id,
            )

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=sources,
            data_quality="complete",
            findings=findings,
            calculations=calculations,
            blockers=[],
            label=SkillLabel.SCOUT.value,
            confidence=0.55,
            can_execute=False,
            downstream=["wow.probability-ev-auditor"],
        )
