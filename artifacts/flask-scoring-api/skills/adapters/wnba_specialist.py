"""
skills/adapters/wnba_specialist.py
WNBA Specialist adapter.

Acceptance test 6: primary teammate OUT/GTD creates role-amplification flag.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.wnba-specialist"
SKILL_VERSION = "1.0.0"


class WnbaSpecialistAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        player            = context.get("player_name", "")
        teammate_status   = (context.get("primary_teammate_status") or "").upper()
        blowout_rebound   = context.get("blowout_rebound_gate")
        usage_amplified   = context.get("usage_amplified", False)

        findings: list[dict] = []
        conflicts: list[dict] = []

        # ── Acceptance test 6: teammate OUT/GTD → role-amplification flag ─────
        if teammate_status in ("OUT", "GTD", "DOUBTFUL"):
            role_amp_flag = True
            findings.append({
                "role_amplification": True,
                "primary_teammate_status": teammate_status,
                "note": (f"Primary teammate {teammate_status!r}: role-amplification "
                         "flag applied per WNBA specialist invariant."),
            })
        else:
            role_amp_flag = False

        # Blowout rebound MORE gate
        if blowout_rebound is not None:
            findings.append({"blowout_rebound_gate": blowout_rebound})

        if player:
            findings.insert(0, {"player": player, "role_amplification_flag": role_amp_flag})

        if not findings:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason="WNBA player context required (player_name).",
                run_id=run_id,
            )

        label = SkillLabel.WATCH.value if role_amp_flag else SkillLabel.SCOUT.value
        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "wnba_stats", "quality": 2}],
            data_quality="complete",
            findings=findings,
            conflicts=conflicts,
            blockers=[],
            label=label,
            confidence=0.5,
            can_execute=False,
            downstream=["wow.game-script-simulator"],
        )
