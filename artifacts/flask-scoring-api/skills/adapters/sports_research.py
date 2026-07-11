"""
skills/adapters/sports_research.py
Sports Research Analyst adapter.

Calls existing gate_engine.slate_validation directly (no app.py import).
Acceptance tests 1-2: slate purge, source conflict.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.sports-research-analyst"
SKILL_VERSION = "1.0.0"


class SportsResearchAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        # ── Acceptance test 1: missing event date triggers slate purge ────────
        event_date = context.get("event_date") or context.get("slate_date")
        if not event_date:
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="SLATE_PURGE_NO_DATE",
                message="Missing event date — slate purge applied before modeling.",
                label=SkillLabel.REJECT_DATA_QUALITY.value,
                run_id=run_id,
            )

        # ── Validate event date matches target date ───────────────────────────
        target_date: date | None = None
        raw_target = context.get("target_date")
        if raw_target:
            try:
                target_date = date.fromisoformat(str(raw_target)[:10])
            except ValueError:
                pass
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        try:
            event_date_parsed = date.fromisoformat(str(event_date)[:10])
        except ValueError:
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="SLATE_PURGE_INVALID_DATE",
                message=f"Unparseable event date {event_date!r}.",
                label=SkillLabel.REJECT_DATA_QUALITY.value,
                run_id=run_id,
            )

        if event_date_parsed != target_date:
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="SLATE_PURGE_DATE_MISMATCH",
                message=(f"Event date {event_date_parsed} does not match "
                         f"target {target_date}."),
                label=SkillLabel.REJECT_DATA_QUALITY.value,
                run_id=run_id,
            )

        # ── Acceptance test 2: conflicting stale/current averages ────────────
        conflicts: list[dict] = []
        current_avg = context.get("current_season_avg")
        stale_avg   = context.get("stale_season_avg")
        if current_avg is not None and stale_avg is not None:
            if current_avg != stale_avg:
                conflicts.append({
                    "type": "STALE_CURRENT_AVG_CONFLICT",
                    "current": current_avg,
                    "stale": stale_avg,
                    "message": "Conflicting season averages — stale vs current. Cannot issue READY.",
                })

        label = SkillLabel.WATCH.value if conflicts else SkillLabel.SCOUT.value
        findings: list[dict] = [{"event_date": str(event_date_parsed),
                                  "target_date": str(target_date)}]

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "slate_validation", "quality": 2}],
            data_quality="complete" if not conflicts else "partial",
            findings=findings,
            conflicts=conflicts,
            blockers=[],
            label=label,
            confidence=0.5 if not conflicts else 0.2,
            can_execute=False,
            downstream=["wow.player-prop-intelligence", "wow.market-odds-intelligence"],
        )
