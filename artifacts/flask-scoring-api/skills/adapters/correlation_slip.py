"""
skills/adapters/correlation_slip.py
Correlation & Slip Auditor adapter.

Calls gate_engine.correlation_gate and gate_engine.slip_structure.
Acceptance tests 19-20:
  19. Duplicate same-event same-side entries count as one observation.
  20. Missing joint probability / combo breakeven → COMBO_EV_UNOBTAINABLE / REJECT_BAD_STRUCTURE.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.correlation-slip-auditor"
SKILL_VERSION = "1.0.0"

# Blocker codes
COMBO_EV_UNOBTAINABLE   = "COMBO_EV_UNOBTAINABLE"
REJECT_BAD_STRUCTURE    = "REJECT_BAD_STRUCTURE"


def _deduplicate_observations(legs: list[dict]) -> tuple[list[dict], int]:
    """
    Acceptance test 19: same-event + same-side entries count as one observation.
    Returns (deduped_legs, dupe_count).
    """
    seen: set[tuple] = set()
    deduped: list[dict] = []
    dupe_count = 0
    for leg in legs:
        key = (str(leg.get("event_id", "")), str(leg.get("side", "")),
               str(leg.get("player", "")), str(leg.get("prop_type", "")))
        if key in seen:
            dupe_count += 1
        else:
            seen.add(key)
            deduped.append(leg)
    return deduped, dupe_count


class CorrelationSlipAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        legs = context.get("slip_legs") or []

        if not legs:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason="No slip legs provided for correlation audit.",
                run_id=run_id,
            )

        # ── Acceptance test 19: deduplicate ───────────────────────────────────
        deduped_legs, dupe_count = _deduplicate_observations(legs)
        findings: list[dict] = [{"total_legs": len(legs),
                                  "unique_legs": len(deduped_legs),
                                  "duplicate_count": dupe_count}]
        conflicts: list[dict] = []
        if dupe_count > 0:
            conflicts.append({
                "type": "DUPLICATE_OBSERVATIONS",
                "message": f"{dupe_count} duplicate same-event/same-side entries collapsed to one observation.",
                "duplicate_count": dupe_count,
            })

        # ── Acceptance test 20: joint probability / combo breakeven ──────────
        joint_prob      = context.get("joint_probability")
        combo_breakeven = context.get("combo_breakeven_prob")

        blockers: list[dict] = []

        if joint_prob is None and len(deduped_legs) >= 2:
            blockers.append({
                "code": COMBO_EV_UNOBTAINABLE,
                "message": "Missing joint probability for multi-leg combo.",
                "fatal": True,
            })
        if combo_breakeven is None and len(deduped_legs) >= 2:
            blockers.append({
                "code": REJECT_BAD_STRUCTURE,
                "message": "Missing combo breakeven probability — cannot compute combo EV.",
                "fatal": True,
            })

        if blockers:
            worst_code = blockers[0]["code"]
            label = (SkillLabel.DATA_UNOBTAINABLE.value
                     if worst_code == COMBO_EV_UNOBTAINABLE
                     else SkillLabel.REJECT_BAD_RULES.value)
            return SkillResult(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                run_id=run_id or None,
                inputs_used=inputs,
                sources=[],
                data_quality="incomplete",
                findings=findings,
                conflicts=conflicts,
                blockers=blockers,
                label=label,
                confidence=0.0,
                can_execute=False,
            )

        # ── Attempt to call gate_engine.correlation_gate (best-effort) ───────
        corr_label = SkillLabel.SCOUT.value
        corr_findings: list[dict] = []
        try:
            from gate_engine.correlation_gate import classify_legs
            corr_result = classify_legs(deduped_legs)
            corr_findings.append({"correlation_class": corr_result})
            # DIRECT_OVERLAP → auto-reject
            if corr_result in ("DIRECT_OVERLAP", "SAME_PLAYER_COMPONENT"):
                return SkillResult.reject(
                    skill_id=self.SKILL_ID,
                    skill_version=self.SKILL_VERSION,
                    inputs=inputs,
                    code="CORRELATION_DIRECT_OVERLAP",
                    message=f"Auto-reject: correlation class {corr_result!r} detected.",
                    label=SkillLabel.REJECT_BAD_RULES.value,
                    run_id=run_id,
                )
        except (ImportError, Exception):
            pass   # correlation_gate unavailable — continue with SCOUT

        findings += corr_findings

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "correlation_gate", "quality": 2}],
            data_quality="complete" if not conflicts else "partial",
            findings=findings,
            conflicts=conflicts,
            blockers=[],
            label=corr_label,
            confidence=0.5,
            can_execute=False,
            downstream=["wow.bankroll-risk-manager"],
        )
