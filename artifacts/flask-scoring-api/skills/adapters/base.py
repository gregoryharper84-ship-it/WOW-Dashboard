"""
skills/adapters/base.py
BaseSkillAdapter — all 21 adapters inherit from this.

Provides:
  - Invariant enforcement (can_execute, dry-run label)
  - Source-quality helpers
  - Operator-supplied price cap (WATCH ceiling)
  - Kalshi freshness check (>10 min → DATA_UNOBTAINABLE)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..contracts import (
    SkillResult,
    SkillLabel,
    SourceEvidence,
    SOURCE_QUALITY_OPERATOR_SUPPLIED,
    FRESHNESS_LIVE_PRICE,
    lower_ceiling,
)


class BaseSkillAdapter(ABC):
    """Abstract base for every WOW v16 skill adapter."""

    SKILL_ID:      str = ""
    SKILL_VERSION: str = "1.0.0"

    @abstractmethod
    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        """Execute the skill against the provided context. Must return SkillResult."""
        ...

    # ── Source evidence helpers ───────────────────────────────────────────────

    def _operator_supplied_cap(
        self,
        sources: list[dict],
        inputs: dict,
        findings: list,
        run_id: str | None,
    ) -> SkillResult | None:
        """
        Acceptance test 7: if ANY source is operator-supplied (quality >= 5),
        cap the result at WATCH. Returns a WATCH SkillResult or None.
        """
        for src in sources:
            q = src.get("quality", 0) if isinstance(src, dict) else getattr(src, "quality", 0)
            if q >= SOURCE_QUALITY_OPERATOR_SUPPLIED:
                return SkillResult.watch(
                    skill_id=self.SKILL_ID,
                    skill_version=self.SKILL_VERSION,
                    inputs=inputs,
                    findings=findings,
                    reason="OPERATOR_SUPPLIED_SOURCE: screenshot or user-entered price caps at WATCH",
                    confidence=0.3,
                    run_id=run_id,
                )
        return None

    def _kalshi_freshness_check(
        self,
        price_age_seconds: float | None,
        inputs: dict,
        run_id: str | None,
    ) -> SkillResult | None:
        """
        Acceptance test 8: Kalshi price age > 10 minutes → DATA_UNOBTAINABLE.
        Returns unobtainable SkillResult or None if fresh.
        """
        if price_age_seconds is not None and price_age_seconds > FRESHNESS_LIVE_PRICE:
            return SkillResult.unobtainable(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                reason=(f"Stale Kalshi price: {price_age_seconds:.0f}s old "
                        f"(limit {FRESHNESS_LIVE_PRICE}s)."),
                run_id=run_id,
            )
        return None

    def _inputs_or_empty(self, context: dict) -> dict:
        return dict(context.get("inputs") or {})
