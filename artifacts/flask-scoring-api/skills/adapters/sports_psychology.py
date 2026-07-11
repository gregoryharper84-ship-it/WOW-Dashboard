"""
skills/adapters/sports_psychology.py
Sports Psychology Context Analyst adapter.

Acceptance test 24: cannot exceed low-weight adjustment cap or use unsupported
mental-state claims.

Hard ban: no diagnosis, no speculation, no certainty language.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.sports-psychology-context"
SKILL_VERSION = "1.0.0"

# Maximum allowed probability adjustment (low-weight cap per SKILL.md)
LOW_WEIGHT_CAP_ABS = 0.03   # ±3 percentage points maximum


class SportsPsychologyAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        adjustment_requested = context.get("psychology_adjustment", 0.0)
        mental_state_claim   = context.get("mental_state_claim", "")
        signal_type          = context.get("psychology_signal_type", "")

        findings: list[dict] = []
        blockers: list[dict] = []

        # ── Acceptance test 24a: low-weight adjustment cap ────────────────────
        adj = float(adjustment_requested)
        if abs(adj) > LOW_WEIGHT_CAP_ABS:
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="PSYCHOLOGY_ADJUSTMENT_EXCEEDS_CAP",
                message=(f"Requested psychology adjustment {adj:+.3f} exceeds "
                         f"low-weight cap ±{LOW_WEIGHT_CAP_ABS:.3f}."),
                label=SkillLabel.REJECT_BAD_RULES.value,
                run_id=run_id,
            )

        # ── Acceptance test 24b: unsupported mental-state claims banned ───────
        unsupported_claims = [
            "confident", "motivated", "nervous", "depressed",
            "anxious", "stressed", "comfortable", "relaxed",
        ]
        if mental_state_claim:
            for banned in unsupported_claims:
                if banned in mental_state_claim.lower():
                    return SkillResult.reject(
                        skill_id=self.SKILL_ID,
                        skill_version=self.SKILL_VERSION,
                        inputs=inputs,
                        code="PSYCHOLOGY_UNSUPPORTED_MENTAL_STATE",
                        message=(f"Mental state claim {mental_state_claim!r} is "
                                 "unsupported speculation — banned per SKILL.md hard bans."),
                        label=SkillLabel.REJECT_BAD_RULES.value,
                        run_id=run_id,
                    )

        # Valid signals: verifiable behavioral/situational only
        if signal_type:
            findings.append({
                "signal_type": signal_type,
                "adjustment_applied": adj,
                "cap": LOW_WEIGHT_CAP_ABS,
                "note": "Low-weight context only; does not override domain probability.",
            })

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[],
            data_quality="complete",
            findings=findings,
            blockers=blockers,
            label=SkillLabel.SCOUT.value,
            confidence=0.2,
            can_execute=False,
            downstream=["wow.probability-ev-auditor"],
        )
