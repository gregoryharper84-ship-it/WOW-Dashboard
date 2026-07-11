"""
skills/adapters/qa_auditor.py
QA & Hallucination Auditor adapter.

Acceptance test 21: QA auditor recomputes edge and catches arithmetic mismatch.
Fail-closed: any arithmetic mismatch in upstream results degrades label.
"""
from __future__ import annotations

import math
from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.qa-hallucination-auditor"
SKILL_VERSION = "1.0.0"

ARITHMETIC_TOLERANCE = 0.001   # 0.1% tolerance for EV recomputation


def _recompute_ev(model_prob: float, no_vig_prob: float) -> float:
    if no_vig_prob <= 0:
        return 0.0
    return (model_prob - no_vig_prob) / no_vig_prob


class QaAuditorAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        upstream_results = context.get("upstream_skill_results") or []
        findings:   list[dict] = []
        conflicts:  list[dict] = []
        blockers:   list[dict] = []

        # ── Acceptance test 21: recompute edge from each upstream EV result ───
        for res in upstream_results:
            if not isinstance(res, dict):
                continue
            for calc in res.get("calculations") or []:
                if calc.get("op") != "ev":
                    continue
                mp  = calc.get("model_prob")
                nvp = calc.get("no_vig_prob")
                ev  = calc.get("ev")
                if mp is None or nvp is None or ev is None:
                    continue
                recomputed = _recompute_ev(float(mp), float(nvp))
                delta = abs(recomputed - float(ev))
                findings.append({
                    "qa_recomputed_ev": recomputed,
                    "claimed_ev": ev,
                    "delta": delta,
                    "skill_id": res.get("skill_id", "unknown"),
                })
                if delta > ARITHMETIC_TOLERANCE:
                    conflicts.append({
                        "type": "EV_ARITHMETIC_MISMATCH",
                        "claimed_ev": ev,
                        "recomputed_ev": recomputed,
                        "delta": delta,
                        "skill_id": res.get("skill_id", "unknown"),
                        "message": (f"EV mismatch: claimed {ev:.6f} vs "
                                    f"recomputed {recomputed:.6f} (Δ={delta:.6f})."),
                    })
                    blockers.append({
                        "code": "QA_EV_ARITHMETIC_MISMATCH",
                        "message": conflicts[-1]["message"],
                        "fatal": True,
                    })

        # ── Check for required fields in all upstream results ─────────────────
        for res in upstream_results:
            if not isinstance(res, dict):
                continue
            if res.get("can_execute") is not False:
                conflicts.append({
                    "type": "CAN_EXECUTE_VIOLATION",
                    "skill_id": res.get("skill_id", "unknown"),
                    "message": "can_execute must be False in all results.",
                })
                blockers.append({
                    "code": "QA_CAN_EXECUTE_VIOLATION",
                    "message": conflicts[-1]["message"],
                    "fatal": True,
                })

        label = SkillLabel.READY.value
        if blockers:
            label = SkillLabel.REJECT_DATA_QUALITY.value
        elif conflicts:
            label = SkillLabel.WATCH.value
        elif not upstream_results:
            label = SkillLabel.SCOUT.value

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[],
            data_quality="complete" if not conflicts else "partial",
            findings=findings,
            conflicts=conflicts,
            blockers=blockers,
            label=label,
            confidence=0.9 if not blockers else 0.0,
            can_execute=False,
        )
