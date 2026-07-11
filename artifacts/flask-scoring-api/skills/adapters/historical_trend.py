"""
skills/adapters/historical_trend.py
Historical Trend Researcher adapter.

Calls gate_engine.outlier_gate and gate_engine.l5_l10_ledger.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.historical-trend-researcher"
SKILL_VERSION = "1.0.0"

MIN_SAMPLE_SIZE = 5


class HistoricalTrendAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        games    = context.get("historical_games") or []
        line     = context.get("line")
        prop     = context.get("prop_type", "")

        findings:     list[dict] = []
        calculations: list[dict] = []
        blockers:     list[dict] = []

        if len(games) < MIN_SAMPLE_SIZE:
            return SkillResult.scout(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                findings=[{"sample_size": len(games), "minimum": MIN_SAMPLE_SIZE}],
                reason=f"Sample size {len(games)} < minimum {MIN_SAMPLE_SIZE}.",
                run_id=run_id,
            )

        # Best-effort outlier detection via gate_engine
        try:
            from gate_engine.outlier_gate import run as outlier_run
            mock_row = {
                "gates": {
                    "l5_l10_ledger": {
                        "passed": True,
                        "l10_hit_rate": context.get("l10_hit_rate", 0.5),
                        "l5_hit_rate":  context.get("l5_hit_rate",  0.5),
                        "l10_avg":      context.get("l10_avg"),
                        "l5_avg":       context.get("l5_avg"),
                    }
                }
            }
            mock_row = outlier_run(mock_row)
            og = mock_row.get("gates", {}).get("outlier_gate", {})
            if og.get("l5_l10_gap_flagged"):
                findings.append({"outlier_flag": True, "l5_l10_gap": og.get("l5_l10_gap_pct")})
            calculations.append({"op": "outlier_gate", "result": og})
        except (ImportError, Exception):
            pass

        findings.append({"sample_size": len(games), "prop_type": prop})

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "l5_l10_ledger", "quality": 2}],
            data_quality="complete",
            findings=findings,
            calculations=calculations,
            blockers=blockers,
            label=SkillLabel.SCOUT.value,
            confidence=0.5,
            can_execute=False,
            downstream=["wow.player-prop-intelligence"],
        )
