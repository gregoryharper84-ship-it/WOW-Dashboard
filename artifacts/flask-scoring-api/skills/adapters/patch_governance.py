"""
skills/adapters/patch_governance.py
Patch Governance & Architecture Manager adapter.

Routes through gate_engine.llp_governance for compliance checks.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.patch-governance-architect"
SKILL_VERSION = "1.0.0"


class PatchGovernanceAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        patch_id   = context.get("patch_id", "")
        patch_type = context.get("patch_type", "")  # rule_change | regression | conflict | deprecation

        findings: list[dict] = []
        blockers: list[dict] = []

        # Conflict or regression patch requires explicit review
        if patch_type in ("regression", "conflict"):
            blockers.append({
                "code": "PATCH_REVIEW_REQUIRED",
                "message": (f"Patch type {patch_type!r} requires explicit review "
                            "before deployment."),
                "fatal": False,
            })

        if patch_id:
            findings.append({"patch_id": patch_id, "patch_type": patch_type})

        # Best-effort: check llp_governance patch registry
        try:
            from gate_engine.llp_governance import PLAYABLE_STAKE_CAPS
            findings.append({"llp_governance_caps_available": True,
                             "reliability_freeze_max": PLAYABLE_STAKE_CAPS.get("reliability_freeze_max_units")})
        except ImportError:
            pass

        label = SkillLabel.WATCH.value if blockers else SkillLabel.SCOUT.value
        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "llp_governance", "quality": 2}],
            data_quality="complete",
            findings=findings,
            blockers=blockers,
            label=label,
            confidence=0.6,
            can_execute=False,
        )
