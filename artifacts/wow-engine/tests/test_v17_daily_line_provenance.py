"""Regression checks for Daily research-line provenance and compact presentation."""
from v17.daily_line_provenance import (
    DISCOVERY_LINE_SOURCE,
    RESEARCH_BLOCKER,
    UNKNOWN_BLOCKER,
    line_provenance_for_daily_prop,
)
from v17.daily_response_contract import compact_row


def _receipt(snapshot_id="snap-1", source_type="AUTONOMOUS_DISCOVERY"):
    return {
        "source_snapshot_id": snapshot_id,
        "write_status": "SNAPSHOT_WRITE_SUCCEEDED",
        "source_type": source_type,
    }


def test_current_exact_research_receipt_is_never_book_offer():
    provenance = line_provenance_for_daily_prop(
        {"source_snapshot_id": "snap-1", "line": 251.5},
        {"line_source": DISCOVERY_LINE_SOURCE, "receipts": [_receipt()]},
    )
    assert provenance == {
        "line_source": DISCOVERY_LINE_SOURCE,
        "source_type": "AUTONOMOUS_DISCOVERY",
        "book_offer_verified": False,
        "research_only": True,
        "pick_presentation_eligible": False,
        "presentation_blocker": RESEARCH_BLOCKER,
    }


def test_preexisting_snapshot_provenance_is_unknown_not_verified():
    provenance = line_provenance_for_daily_prop(
        {"source_snapshot_id": "old"}, None,
    )
    assert provenance["line_source"] == "UNKNOWN"
    assert provenance["presentation_blocker"] == UNKNOWN_BLOCKER
    assert provenance["book_offer_verified"] is False
    assert provenance["pick_presentation_eligible"] is False


def test_mismatched_and_non_discovery_receipts_cannot_authenticate_offer():
    snapshot = {"source_snapshot_id": "snap-1", "line": 251.5}
    for receipt in (_receipt("different"), _receipt(source_type="SCREENSHOT"),
                    {**_receipt(), "write_status": "SNAPSHOT_WRITE_FAILED"}):
        provenance = line_provenance_for_daily_prop(
            snapshot, {"line_source": DISCOVERY_LINE_SOURCE,
                       "receipts": [receipt]},
        )
        assert provenance["presentation_blocker"] == UNKNOWN_BLOCKER
        assert provenance["book_offer_verified"] is False


def test_latest_canonical_snapshot_can_match_an_acquisition_source_instance():
    provenance = line_provenance_for_daily_prop(
        {"source_snapshot_id": "older", "source_snapshot_ids": ["older", "snap-1"]},
        {"line_source": DISCOVERY_LINE_SOURCE, "receipts": [_receipt()]},
    )
    assert provenance["presentation_blocker"] == RESEARCH_BLOCKER


def test_compact_transport_never_promotes_model_probability_to_offered_pick():
    provenance = line_provenance_for_daily_prop(
        {"source_snapshot_id": "snap-1"},
        {"line_source": DISCOVERY_LINE_SOURCE, "receipts": [_receipt()]},
    )
    row = {
        "lane": "PROPS", "identity": {"source_snapshot_id": "snap-1"},
        "terminal": True, "row_status": "COMPLETED",
        "probability_publishable": True,
        "line_provenance": provenance,
        "result": {"outcomes": [], "line_provenance": provenance},
        "can_execute": False,
    }
    compact = compact_row(row, run_id="run", row_index=0, detail_available=False)
    assert compact["probability_publishable"] is True  # sporting model status
    assert compact["line_provenance"]["research_only"] is True
    assert compact["line_provenance"]["book_offer_verified"] is False
    assert compact["pick_presentation_eligible"] is False
    assert compact["official_leaderboard_eligible"] is False
    assert compact["can_execute"] is False


def test_missing_provenance_in_legacy_compact_terminal_fails_closed():
    compact = compact_row(
        {"lane": "PROPS", "identity": {}, "result": {"outcomes": []},
         "probability_publishable": True, "row_status": "COMPLETED"},
        run_id="run", row_index=0, detail_available=False,
    )
    assert compact["line_provenance"]["presentation_blocker"] == UNKNOWN_BLOCKER
    assert compact["official_leaderboard_eligible"] is False
    assert compact["pick_presentation_eligible"] is False
