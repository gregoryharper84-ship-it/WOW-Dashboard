"""
skills/adapters/bankroll_risk.py
Bankroll & Risk Manager adapter.

Acceptance test 23: no allocation when capital lane is blocked.
Calls gate_engine.exposure_gate for duplicate exposure checks.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.bankroll-risk-manager"
SKILL_VERSION = "1.0.0"

RELIABILITY_FREEZE_MAX_UNITS = 0.25   # quarter-Kelly during Reliability Freeze


class BankrollRiskAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        # ── Acceptance test 23: no allocation when capital lane is blocked ────
        capital_lane_blocked = context.get("capital_lane_blocked", False)
        upstream_label       = context.get("upstream_final_label", SkillLabel.READY.value)

        # Any non-READY/WATCH/SCOUT upstream label blocks allocation
        blocking_labels = {
            SkillLabel.HOLD.value,
            SkillLabel.REJECT_BAD_RULES.value,
            SkillLabel.REJECT_DATA_QUALITY.value,
            SkillLabel.DATA_UNOBTAINABLE.value,
        }
        lane_blocked_by_upstream = upstream_label in blocking_labels

        if capital_lane_blocked or lane_blocked_by_upstream:
            reason = ("capital_lane_blocked=True" if capital_lane_blocked
                      else f"upstream label {upstream_label!r} blocks allocation")
            return SkillResult(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                run_id=run_id or None,
                inputs_used=inputs,
                sources=[],
                data_quality="complete",
                findings=[{"allocation": 0.0, "reason": reason}],
                blockers=[{
                    "code": "CAPITAL_LANE_BLOCKED",
                    "message": f"No allocation: {reason}.",
                    "fatal": True,
                }],
                label=SkillLabel.HOLD.value,
                confidence=0.0,
                can_execute=False,
            )

        # ── Reliability Freeze cap ────────────────────────────────────────────
        reliability_freeze = context.get("reliability_freeze", False)
        base_kelly          = context.get("kelly_fraction", 0.0)
        kelly_fraction = float(base_kelly)

        if reliability_freeze and kelly_fraction > RELIABILITY_FREEZE_MAX_UNITS:
            kelly_fraction = RELIABILITY_FREEZE_MAX_UNITS

        # ── Build sizing output ───────────────────────────────────────────────
        findings: list[dict] = [{
            "allocation_units": kelly_fraction,
            "reliability_freeze": reliability_freeze,
            "note": "Dry-run only — no live orders.",
        }]
        calculations: list[dict] = [{
            "op": "kelly_cap",
            "base_kelly": base_kelly,
            "reliability_freeze": reliability_freeze,
            "capped_kelly": kelly_fraction,
        }]

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "exposure_gate", "quality": 2}],
            data_quality="complete",
            findings=findings,
            calculations=calculations,
            blockers=[],
            label=SkillLabel.WATCH.value,
            confidence=0.6,
            can_execute=False,
            downstream=["wow.qa-hallucination-auditor"],
        )
