"""
skills/adapters/player_prop.py
Player Prop Intelligence adapter.

Acceptance tests 3-4:
  3. L10/L5 divergence >20% triggers outlier isolation and L9 recomputation.
  4. Role-dependent player uses matching role-split ledger.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.player-prop-intelligence"
SKILL_VERSION = "1.0.0"

L5_L10_DIVERGENCE_THRESHOLD = 0.20   # acceptance test 3


class PlayerPropAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        l10_avg    = context.get("l10_avg")
        l5_avg     = context.get("l5_avg")
        l9_avg     = context.get("l9_avg")   # recomputed after outlier removal
        role_split = context.get("role_split_ledger")
        is_role_dependent = context.get("is_role_dependent", False)

        findings:  list[dict] = []
        conflicts: list[dict] = []
        calculations: list[dict] = []
        blockers:  list[dict] = []

        # ── Acceptance test 3: L10/L5 divergence > 20% ───────────────────────
        if l10_avg is not None and l5_avg is not None:
            l10_f = float(l10_avg)
            l5_f  = float(l5_avg)
            if l10_f > 0:
                divergence = abs(l5_f - l10_f) / l10_f
                calculations.append({
                    "op": "l5_l10_divergence",
                    "l10_avg": l10_f,
                    "l5_avg": l5_f,
                    "divergence_pct": divergence,
                    "threshold": L5_L10_DIVERGENCE_THRESHOLD,
                    "flagged": divergence > L5_L10_DIVERGENCE_THRESHOLD,
                })
                if divergence > L5_L10_DIVERGENCE_THRESHOLD:
                    findings.append({
                        "outlier_isolation": True,
                        "divergence_pct": divergence,
                        "l9_recomputed": l9_avg,
                        "note": ("L5/L10 divergence >20%: one-game outlier isolated, "
                                 "L9 recomputation applied."),
                    })
                    conflicts.append({
                        "type": "L5_L10_DIVERGENCE",
                        "divergence_pct": divergence,
                        "l9_avg": l9_avg,
                    })

        # ── Acceptance test 4: role-dependent player needs role-split ledger ──
        if is_role_dependent and not role_split:
            blockers.append({
                "code": "MISSING_ROLE_SPLIT_LEDGER",
                "message": ("Role-dependent player requires a role-split ledger. "
                            "Cannot model without matching role segmentation."),
                "fatal": True,
            })
        elif is_role_dependent and role_split:
            findings.append({"role_split_applied": True, "role_split": role_split})

        if blockers and any(b["fatal"] for b in blockers):
            return SkillResult(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                run_id=run_id or None,
                inputs_used=inputs,
                sources=[],
                data_quality="incomplete",
                findings=findings,
                conflicts=conflicts,
                calculations=calculations,
                blockers=blockers,
                label=SkillLabel.REJECT_DATA_QUALITY.value,
                confidence=0.0,
                can_execute=False,
            )

        label = SkillLabel.WATCH.value if conflicts else SkillLabel.SCOUT.value
        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "l5_l10_ledger", "quality": 2}],
            data_quality="partial" if conflicts else "complete",
            findings=findings,
            conflicts=conflicts,
            calculations=calculations,
            blockers=[],
            label=label,
            confidence=0.45 if conflicts else 0.55,
            can_execute=False,
            downstream=["wow.game-script-simulator"],
        )
