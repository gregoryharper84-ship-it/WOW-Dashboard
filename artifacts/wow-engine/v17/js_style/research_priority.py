"""Transparent JS research-priority scoring.

The score is research triage only. It is not probability, confidence, a lower
bound, publication authority, or rank eligibility. Direction is deliberately
not an input to the arithmetic, so LESS alone can never earn points.
"""
from __future__ import annotations

from typing import Mapping

from v17.js_style.contracts import (
    EVIDENCE_COMPLETE,
    EVIDENCE_INCOMPLETE,
    PENALTY_KEYS,
    RESEARCH_COMPONENT_KEYS,
    RESEARCH_COMPONENT_MAXIMA,
    ResearchPriorityResult,
)


def compute_research_priority(
    components: Mapping[str, float | None],
    penalties: Mapping[str, float] | None = None,
) -> ResearchPriorityResult:
    missing = [key for key in RESEARCH_COMPONENT_KEYS if components.get(key) is None]
    if missing:
        return ResearchPriorityResult(
            evidence_status=EVIDENCE_INCOMPLETE,
            components={key: float(components.get(key) or 0.0) for key in RESEARCH_COMPONENT_KEYS},
            penalties={},
            js_research_priority=None,
        )

    normalized_components: dict[str, float] = {}
    for key in RESEARCH_COMPONENT_KEYS:
        value = float(components[key])
        maximum = float(RESEARCH_COMPONENT_MAXIMA[key])
        if value < 0.0 or value > maximum:
            raise ValueError(f"JS_RESEARCH_COMPONENT_OUT_OF_RANGE:{key}:{value}")
        normalized_components[key] = value

    normalized_penalties: dict[str, float] = {}
    for key, raw in (penalties or {}).items():
        if key not in PENALTY_KEYS:
            raise ValueError(f"JS_RESEARCH_PENALTY_UNKNOWN:{key}")
        value = float(raw)
        if value < 0.0 or value > 100.0:
            raise ValueError(f"JS_RESEARCH_PENALTY_OUT_OF_RANGE:{key}:{value}")
        normalized_penalties[key] = value

    gross = sum(normalized_components.values())
    net = max(0.0, min(100.0, gross - sum(normalized_penalties.values())))
    return ResearchPriorityResult(
        evidence_status=EVIDENCE_COMPLETE,
        components=normalized_components,
        penalties=normalized_penalties,
        js_research_priority=net,
    )


__all__ = ["compute_research_priority"]
