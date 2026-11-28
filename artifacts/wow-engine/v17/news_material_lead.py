"""Material-lead title heuristics for public GDELT research discovery.

GDELT headlines are discovery only. These markers prefer injuries, availability,
lineups, rotations and similar pregame personnel/context leads over fashion,
schedule features or draft speculation noise. Matching a marker never creates a
reviewed fact, model feature or prediction authority.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False

# Whole-token markers aligned to HIGH_IMPACT / injury-availability evidence kinds.
MATERIAL_LEAD_RE = re.compile(
    r"\b("
    r"injur(?:y|ies|ed)|inactive|questionable|doubtful|ruled\s+out|"
    r"will\s+not\s+play|does\s+not\s+play|game[- ]time\s+decision|\bgtd\b|"
    r"lineup|starting(?:\s+lineup)?|starter|availability|available|"
    r"scratch(?:ed)?|rotation|minutes\s+limit|pitch\s+count|"
    r"starting\s+goalie|goalie\s+start|activated|waived|suspended|"
    r"illness|protocol|designated\s+for\s+assignment|"
    r"out\s+(?:for|vs|against|tonight|sunday|monday|thursday)|"
    r"ruled\s+in|elevated|recalled|optioned"
    r")\b",
    re.IGNORECASE,
)

MATERIAL_TOPIC_CLAUSES: tuple[str, ...] = (
    "injury OR injured OR inactive",
    "lineup OR starting OR starter OR availability",
    "\"ruled out\" OR questionable OR doubtful OR scratch OR scratched",
)


def is_material_lead_title(title: Any) -> bool:
    if not isinstance(title, str) or not title.strip():
        return False
    return MATERIAL_LEAD_RE.search(title) is not None


def material_topic_clause(at_index: int) -> str:
    return MATERIAL_TOPIC_CLAUSES[int(at_index) % len(MATERIAL_TOPIC_CLAUSES)]


def material_query_for(league_label: str, *, slot_index: int) -> str:
    """Build a DOC ArtList query that rotates material topics by refresh slot."""
    label = str(league_label or "").strip()
    if not label:
        raise ValueError("NEWS_MATERIAL_QUERY_LABEL_REQUIRED")
    return f"{label} ({material_topic_clause(slot_index)})"


def prefer_material_leads(articles: Sequence[Mapping[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Stable-prefer material titles, then fill with remaining sport matches."""
    material: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for row in articles:
        if not isinstance(row, Mapping):
            continue
        item = dict(row)
        lead = is_material_lead_title(item.get("title"))
        item["material_lead"] = lead
        (material if lead else other).append(item)
    selected = (material + other)[: max(0, int(limit))]
    return selected


def material_lead_count(articles: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for row in articles if isinstance(row, Mapping) and row.get("material_lead") is True)


__all__ = [
    "CAN_EXECUTE",
    "MATERIAL_LEAD_RE",
    "MATERIAL_TOPIC_CLAUSES",
    "is_material_lead_title",
    "material_lead_count",
    "material_query_for",
    "material_topic_clause",
    "prefer_material_leads",
]
