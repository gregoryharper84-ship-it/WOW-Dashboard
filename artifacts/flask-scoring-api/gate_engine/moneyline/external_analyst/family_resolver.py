"""
gate_engine/moneyline/external_analyst/family_resolver.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE

Syndication and family deduplication for analyst opinions.

Purpose: prevent a single analyst's pick republished across multiple
aggregator sites from being counted as N independent opinions.

Deduplication key: (source_family, analyst_family, side).
When source_family is the same AND analyst_family is the same AND side
matches → second+ copies are marked is_syndicated_copy=True.

Source family registry: maps domain/site names to canonical source families.
Analyst family registry: maps known analyst aliases to canonical IDs.

can_execute=False unconditional.
"""
from __future__ import annotations

import hashlib
from typing import Any

from gate_engine.moneyline.external_analyst.types import AnalystOpinion

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Source family registry
# Maps raw source_name values to canonical source families.
# Same site = same source_family regardless of subsection URL.
# ---------------------------------------------------------------------------

SOURCE_FAMILY_REGISTRY: dict[str, str] = {
    # StumpTheSpread
    "stumpsthespread.com":       "stumps_the_spread",
    "stumps_the_spread":         "stumps_the_spread",
    "stumpthespread":            "stumps_the_spread",
    "stump the spread":          "stumps_the_spread",
    # PickDawgz
    "pickdawgz.com":             "pickdawgz",
    "pickdawgz":                 "pickdawgz",
    # Placeholder for additional future sources
    "cappers.io":                "cappers_io",
    "docs sports":               "docs_sports",
    "docsports.com":             "docs_sports",
}


def resolve_source_family(source_name: str) -> str:
    """
    Map a raw source name to a canonical source family key.
    Falls back to a normalized slug of the source_name.
    """
    normalized = source_name.lower().strip()
    return SOURCE_FAMILY_REGISTRY.get(normalized) or _slugify(source_name)


# ---------------------------------------------------------------------------
# Analyst family registry
# Maps known analyst pen names / bylines to canonical IDs.
# ---------------------------------------------------------------------------

ANALYST_FAMILY_REGISTRY: dict[str, str] = {
    # StumpTheSpread staff
    "stump":            "stumps_the_spread_official",
    "stump the spread": "stumps_the_spread_official",
    "sts staff":        "stumps_the_spread_official",
    # Generic fallbacks
    "staff":            "_staff",
    "editorial":        "_staff",
}


def resolve_analyst_family(analyst_name: str | None, source_family: str) -> str:
    """
    Map a raw analyst name to a canonical analyst family key.
    Falls back to source_family + "_unknown" when analyst is not identified.
    """
    if not analyst_name:
        return f"{source_family}_unknown"
    normalized = analyst_name.lower().strip()
    return ANALYST_FAMILY_REGISTRY.get(normalized) or _slugify(analyst_name)


# ---------------------------------------------------------------------------
# Deduplication key and hash
# ---------------------------------------------------------------------------

def make_opinion_key(opinion: AnalystOpinion) -> str:
    """
    Produce a stable deduplication key for an AnalystOpinion.

    Key components: source_family + analyst_family + side + event_date
    Multiple publications of the same pick → same key → syndicated.
    """
    parts = "|".join([
        opinion.source_family or "",
        opinion.analyst_family or "",
        (opinion.side or "").lower(),
        opinion.event_date or "",
        (opinion.team or "").lower(),
    ])
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main deduplication function
# ---------------------------------------------------------------------------

def deduplicate_opinions(
    opinions: list[AnalystOpinion],
) -> tuple[list[AnalystOpinion], list[AnalystOpinion]]:
    """
    Partition opinions into (independent, syndicated).

    An opinion is "independent" if its (source_family, analyst_family, side,
    event_date, team) key has not been seen before.
    Subsequent copies of the same key are marked is_syndicated_copy=True.

    Returns:
        independent  : First occurrence of each unique (family, analyst, side) key
        all_opinions : All opinions with is_syndicated_copy flag updated
    """
    seen: set[str] = set()
    independent: list[AnalystOpinion] = []

    for op in opinions:
        # Ensure family fields are resolved
        if not op.source_family:
            op.source_family = resolve_source_family(op.source_name)
        if not op.analyst_family:
            op.analyst_family = resolve_analyst_family(op.analyst_name, op.source_family)

        key = make_opinion_key(op)
        op.canonical_opinion_key = key

        if key in seen:
            op.is_syndicated_copy = True
        else:
            seen.add(key)
            op.is_syndicated_copy = False
            independent.append(op)

    return independent, opinions


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert a string to a lowercase slug (alphanum + underscores)."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", text.lower().strip()).strip("_")
