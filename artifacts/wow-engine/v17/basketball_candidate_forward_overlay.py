"""Research-only forward-evidence overlay for exact NBA/WNBA basketball candidates.

The universal collector intentionally rejects ordinary CANDIDATE_ONLY routes. This
module opts in only the exact fitted basketball candidate routes that now have an
explicit research scorer. It also teaches the generic forward ledger to read the
candidate scorer's raw probability field. No calibrated probability, publication,
ranking, certification, promotion, or execution authority is introduced.
"""
from __future__ import annotations

from typing import Any

import v17.prop_universal_forward_evidence as universal

CAN_EXECUTE = False

RESEARCH_GENERIC_ROUTES = frozenset({
    ("WNBA", "PRA"),
    ("WNBA", "POINTS_REBOUNDS"),
    ("WNBA", "POINTS_ASSISTS"),
    ("WNBA", "REBOUNDS_ASSISTS"),
    ("NBA", "POINTS"),
    ("NBA", "REBOUNDS"),
    ("NBA", "ASSISTS"),
    ("NBA", "PRA"),
    ("NBA", "POINTS_REBOUNDS"),
    ("NBA", "POINTS_ASSISTS"),
    ("NBA", "REBOUNDS_ASSISTS"),
})

_ORIGINAL_COLLECTOR_FOR = universal._collector_for
_ORIGINAL_EXTRACT_OUTPUT = universal._extract_output


def _collector_for(key: tuple[str, str]) -> tuple[str, str, str | None]:
    if key in RESEARCH_GENERIC_ROUTES:
        return universal.COLLECTOR_GENERIC, universal.COLLECTION_AVAILABLE, None
    return _ORIGINAL_COLLECTOR_FOR(key)


def _extract_output(scored: dict[str, Any], direction: str) -> dict[str, Any] | None:
    ordinary = _ORIGINAL_EXTRACT_OUTPUT(scored, direction)
    if ordinary is not None:
        return ordinary
    candidate = scored.get("candidate_model_output")
    if not isinstance(candidate, dict):
        return None
    raw = candidate.get("raw_candidate_probability")
    if raw is None:
        raw = candidate.get("P(MORE)" if direction == "MORE" else "P(LESS)")
    if raw is None:
        return None
    output = dict(candidate)
    output["raw_model_probability"] = raw
    output["probability_publishable"] = False
    output["rank_eligible"] = False
    output["can_execute"] = False
    return output


def install() -> None:
    universal._collector_for = _collector_for
    universal._extract_output = _extract_output


install()


__all__ = ["CAN_EXECUTE", "RESEARCH_GENERIC_ROUTES", "install"]
