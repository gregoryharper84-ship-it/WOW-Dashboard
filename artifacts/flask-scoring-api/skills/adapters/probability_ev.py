"""
skills/adapters/probability_ev.py
Probability & EV Auditor adapter.

Acceptance test 5: coin-flip MORE evaluation automatically assesses LESS.
Reuses gate_engine.ev_gate for edge classification where possible.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.probability-ev-auditor"
SKILL_VERSION = "1.0.0"

COIN_FLIP_THRESHOLD = 0.52   # same as ev_gate REJECT_COINFLIP threshold
MIN_EV_THRESHOLD    = 0.0    # must be positive EV to proceed


def _compute_ev(model_prob: float, no_vig_prob: float) -> float:
    """EV = (model_prob - no_vig_prob) / no_vig_prob when no_vig_prob > 0."""
    if no_vig_prob <= 0:
        return 0.0
    return (model_prob - no_vig_prob) / no_vig_prob


class ProbabilityEvAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        model_prob  = context.get("model_probability")
        no_vig_prob = context.get("no_vig_probability")

        if model_prob is None:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason="model_probability is required for EV audit.",
                run_id=run_id,
            )

        model_prob  = float(model_prob)
        no_vig_prob = float(no_vig_prob) if no_vig_prob is not None else 0.5

        calculations: list[dict] = []
        findings:     list[dict] = []
        blockers:     list[dict] = []

        # ── Acceptance test 5: coin-flip MORE also assesses LESS ──────────────
        direction = context.get("direction", "MORE").upper()
        coin_flip_both_sides = context.get("gate3_evaluate_both_sides", True)

        is_coinflip_more = (direction == "MORE" and model_prob < COIN_FLIP_THRESHOLD)
        is_coinflip_less = (direction == "LESS" and (1 - model_prob) < COIN_FLIP_THRESHOLD)

        if coin_flip_both_sides:
            # Auto-assess LESS side when MORE is a coin-flip
            opposite_prob = 1.0 - model_prob
            calculations.append({
                "op": "coinflip_both_sides_assessment",
                "direction_requested": direction,
                "model_prob": model_prob,
                "opposite_prob": opposite_prob,
                "coinflip_threshold": COIN_FLIP_THRESHOLD,
                "is_coinflip_more": is_coinflip_more,
                "is_coinflip_less": opposite_prob < COIN_FLIP_THRESHOLD,
            })
            if is_coinflip_more:
                findings.append({
                    "note": "COIN_FLIP_MORE: Gate 3 also evaluated LESS direction.",
                    "less_prob": opposite_prob,
                })

        # ── EV calculation ────────────────────────────────────────────────────
        ev = _compute_ev(model_prob, no_vig_prob)
        calculations.append({
            "op": "ev",
            "model_prob": model_prob,
            "no_vig_prob": no_vig_prob,
            "ev": ev,
        })
        findings.append({"ev": ev, "model_prob": model_prob, "no_vig_prob": no_vig_prob})

        # Coin-flip reject
        if is_coinflip_more or is_coinflip_less:
            blockers.append({
                "code": "REJECT_COINFLIP",
                "message": (f"Model probability {model_prob:.3f} is near coin-flip "
                            f"(threshold {COIN_FLIP_THRESHOLD})."),
                "fatal": True,
            })
            return SkillResult(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                run_id=run_id or None,
                inputs_used=inputs,
                sources=[],
                data_quality="complete",
                findings=findings,
                calculations=calculations,
                blockers=blockers,
                label=SkillLabel.REJECT_BAD_RULES.value,
                confidence=0.0,
                can_execute=False,
            )

        # Negative EV
        if ev < MIN_EV_THRESHOLD:
            blockers.append({
                "code": "NEGATIVE_EV",
                "message": f"EV {ev:.4f} is negative; market edge insufficient.",
                "fatal": False,
            })

        label = SkillLabel.SCOUT.value
        if ev > 0.05 and model_prob > COIN_FLIP_THRESHOLD + 0.05:
            label = SkillLabel.WATCH.value
        if blockers and any(b["fatal"] for b in blockers):
            label = SkillLabel.REJECT_BAD_RULES.value

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "ev_gate", "quality": 2}],
            data_quality="complete",
            findings=findings,
            calculations=calculations,
            blockers=blockers,
            label=label,
            confidence=min(0.8, max(0.1, model_prob)),
            can_execute=False,
            downstream=["wow.bankroll-risk-manager"],
        )
