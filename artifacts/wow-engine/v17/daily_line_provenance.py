"""Fail-closed offered-line provenance for V17 Daily prop presentation.

Canonical source snapshots predate explicit offered-line metadata. Receipt-backed
Daily discovery proves only a prior-ten research threshold, never a sportsbook
offer; historical or ambiguous snapshots must not be upgraded by inference.
This guard does not change fitted model scoring or terminal authority.
"""
from __future__ import annotations

from typing import Any

DISCOVERY_LINE_SOURCE = "PRIOR10_MEDIAN_HALF_POINT_DISCOVERY_ONLY"
RESEARCH_BLOCKER = "RESEARCH_ONLY_LINE_NOT_A_BOOK_OFFER"
UNKNOWN_BLOCKER = "LINE_PROVENANCE_NOT_RECORDED_ON_SNAPSHOT"


def line_provenance_for_daily_prop(
    snapshot: dict[str, Any],
    acquisition: dict[str, Any] | None,
) -> dict[str, Any]:
    """Classify current exact receipt identity, never an adjacent line or player.

    The snapshot schema does not persist a signed book offer. Therefore no Daily
    snapshot is independently eligible for book-offer/pick presentation here.
    Only current producer receipts can prove the specific research line source.
    """
    ids = {str(s) for s in (snapshot.get("source_snapshot_ids") or []) if s}
    if snapshot.get("source_snapshot_id"):
        ids.add(str(snapshot["source_snapshot_id"]))
    acquired = acquisition if isinstance(acquisition, dict) else {}
    for receipt in acquired.get("receipts") or []:
        if not isinstance(receipt, dict):
            continue
        if (
            str(receipt.get("source_snapshot_id") or "") not in ids
            or receipt.get("write_status") != "SNAPSHOT_WRITE_SUCCEEDED"
            or receipt.get("source_type") != "AUTONOMOUS_DISCOVERY"
        ):
            continue
        if acquired.get("line_source") == DISCOVERY_LINE_SOURCE:
            return {
                "line_source": DISCOVERY_LINE_SOURCE,
                "source_type": "AUTONOMOUS_DISCOVERY",
                "book_offer_verified": False,
                "research_only": True,
                "pick_presentation_eligible": False,
                "presentation_blocker": RESEARCH_BLOCKER,
            }
    # Older snapshots may be useful for independent sporting model research;
    # their line was not authenticated as a book offer. Never infer otherwise.
    return {
        "line_source": "UNKNOWN",
        "source_type": "UNKNOWN",
        "book_offer_verified": False,
        "research_only": True,
        "pick_presentation_eligible": False,
        "presentation_blocker": UNKNOWN_BLOCKER,
    }


__all__ = ["line_provenance_for_daily_prop", "DISCOVERY_LINE_SOURCE",
           "RESEARCH_BLOCKER", "UNKNOWN_BLOCKER"]
