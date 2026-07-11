"""
skills/adapters/referee_umpire.py
Referee & Umpire Tendency Analyst adapter.

Acceptance test 25: no adjustment when assignment is unconfirmed.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.referee-umpire-tendency"
SKILL_VERSION = "1.0.0"


class RefereeUmpireAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        assignment_confirmed = context.get("ref_assignment_confirmed", False)
        official_name        = context.get("official_name", "")
        tendency_data        = context.get("tendency_data")

        findings: list[dict] = []

        # ── Acceptance test 25: no adjustment when unconfirmed ────────────────
        if not assignment_confirmed:
            return SkillResult(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                run_id=run_id or None,
                inputs_used=inputs,
                sources=[],
                data_quality="incomplete",
                findings=[{
                    "adjustment_applied": 0.0,
                    "reason": "Referee/umpire assignment not confirmed — no adjustment applied.",
                    "official": official_name or "UNCONFIRMED",
                }],
                blockers=[{
                    "code": "REF_ASSIGNMENT_UNCONFIRMED",
                    "message": "No adjustment applied: assignment is unconfirmed.",
                    "fatal": False,
                }],
                label=SkillLabel.SCOUT.value,
                confidence=0.1,
                can_execute=False,
            )

        # Confirmed assignment: return tendency data
        if tendency_data:
            findings.append({
                "official": official_name,
                "tendency_data": tendency_data,
                "adjustment_applied": tendency_data.get("prob_adjustment", 0.0),
            })
        else:
            findings.append({
                "official": official_name,
                "note": "Confirmed but no tendency data available.",
                "adjustment_applied": 0.0,
            })

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "official_assignment", "quality": 1}],
            data_quality="complete",
            findings=findings,
            blockers=[],
            label=SkillLabel.SCOUT.value,
            confidence=0.4,
            can_execute=False,
            downstream=["wow.probability-ev-auditor"],
        )
