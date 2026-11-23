"""November 22 chat slip retrospective regression; NO pregame model attribution.

Read-only, source-linked settled chat selections. These were hypothetical
assistant-generated slips, not authenticated submissions or fitted forecasts.
"""
from __future__ import annotations

import json
from pathlib import Path

from v17.postmortem_engine import (
    PostmortemLegInput,
    PostmortemPositionInput,
    PostmortemRunInput,
    build_postmortem_payload,
)
from v17.slip_research_preflight import audit_research_slips

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "docs" / "wow" / "research"
    / "2026-11-22-chat-cross-sport-slip-retro.json"
)


def _source():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _retro_payload():
    source = _source()
    assert source["evidence_boundaries"]["verified_submission"] is False
    assert source["evidence_boundaries"]["verified_wow_pregame_model_receipt"] is False
    legs = []
    positions = []
    for slip_id, ids in source["hypothetical_slips"].items():
        position = f"SLIP_{slip_id}"
        positions.append(
            PostmortemPositionInput(
                position_reference=position,
                position_structure="HYPOTHETICAL_ALL_OR_NOTHING_NOT_SUBMITTED",
                underlying_market_count=len(ids),
                entry_cost=0,
                gross_return=0,
                all_or_nothing=True,
            )
        )
        for pick_id in ids:
            item = source["verified_picks"][pick_id]
            legs.append(
                PostmortemLegInput(
                    row_key=f"{slip_id}:{pick_id}",
                    position_reference=position,
                    **item,
                )
            )
    batch = PostmortemRunInput(
        research_run_id=source["research_run_id"],
        slate_date=source["slate_date"],
        source_type=source["status"],
        preserve=["independent settlement", "exact line", "favorable winners"],
        refine=["single-play tail risk", "cross-slip shared hinge", "unverified model receipts"],
        regression_checks=["12 occurrences vs 8 unique", "Tate 3 slips", "no probability backfill"],
        legs=legs,
        positions=positions,
    )
    return build_postmortem_payload(batch)


def test_nov22_retrospective_counts_unique_selections_separately_from_reused_occurrences():
    source = _source()
    picks = source["verified_picks"]
    assert len(picks) == 8
    assert sum(p["settled_result"] == "WIN" for p in picks.values()) == 4
    assert sum(p["settled_result"] == "LOSS" for p in picks.values()) == 4
    payload = _retro_payload()
    summary = payload["summary"]
    assert (summary["legs"], summary["wins"], summary["losses"]) == (12, 5, 7)
    assert summary["positions"] == 3
    assert summary["all_leg_clear_positions"] == 0
    assert summary["distinct_selections"] == 8
    assert summary["reused_selection_groups"] == 3  # Tate, Wagner, Kucherov
    assert summary["reused_selection_extra_positions"] == 4
    assert summary["max_selection_position_exposure"] == 3
    assert summary["cross_position_exposure_basis"] == (
        "EXACT_OBSERVED_FIELDS_ONLY_NOT_CERTIFIED_IDENTITY"
    )
    assert {p["position_reference"] for p in payload["positions"]} == {
        "SLIP_A", "SLIP_B", "SLIP_C"
    }
    assert all(p["all_legs_hit"] is False for p in payload["positions"])


def test_nov22_chat_slips_never_backfill_probability_or_create_fake_economics():
    payload = _retro_payload()
    assert payload["summary"]["calibration_eligible_count"] == 0
    assert payload["summary"]["matched_pregame_count"] == 0
    assert payload["summary"]["roi"] is None
    assert payload["run"]["process_status"] == "COMPLETED_WITH_BLOCKERS"
    assert payload["can_execute"] is False
    for leg in payload["legs"]:
        assert leg["recommendation_record_id"] is None
        assert leg["calibrated_probability"] is None
        assert leg["lower_bound"] is None
        assert leg["calibration_eligible"] is False
        assert leg["excluded_from_calibration"] is True
        assert leg["capture_timing"] == "POST_EVENT_RETROACTIVE"
        assert leg["can_execute"] is False
    tate = next(x for x in payload["legs"] if x["row_key"] == "A:tate")
    assert tate["actual_stat"] == 59
    assert tate["margin_to_line"] == -35.5


def test_nov22_shared_tate_hinge_is_held_in_existing_research_preflight():
    source = _source()
    # Synthetic pregame skeleton tests structure only. These event IDs are not
    # real official IDs, and no quote/news/model receipt is manufactured.
    events = {
        "tate": "ten-dal", "wagner": "orl-was", "kucherov": "buf-tbl",
        "cook": "mia-buf", "dallas": "pit-dal", "denver": "den-chi",
        "nashville": "cin-nsh", "samford": "unc-sam",
    }
    cards = []
    for slip_id, ids in source["hypothetical_slips"].items():
        card_legs = []
        for pick_id in ids:
            item = source["verified_picks"][pick_id]
            card_legs.append({
                "row_id": f"{slip_id}:{pick_id}",
                "sport": item["sport"],
                "official_event_id": "SYNTHETIC:" + events[pick_id],
                "market_family": "PLAYER_PROP" if item["exact_line"] is not None else "MONEYLINE",
                "official_player_id": "SYNTHETIC:" + pick_id if item["exact_line"] is not None else None,
                "stat_type": item["market"] if item["exact_line"] is not None else None,
                "side": item["side"],
                "market_period": "FULL_GAME",
                "settlement_basis": "90_MIN" if pick_id == "nashville" else "INCL_OT",
                "exact_line": item["exact_line"],
                "event_start_utc": "2026-11-22T23:00:00Z",
                "source_snapshot_at_utc": "2026-11-22T17:00:00Z",
                "source_snapshot_receipt_id": None,
            })
        cards.append({"card_id": slip_id, "structure": "FLEX", "legs": card_legs})
    audit = audit_research_slips(cards, as_of_utc="2026-11-22T17:30:00Z")
    assert max(audit["cross_slip_exact_exposure_counts"].values()) == 3
    assert audit["status"] == "RESEARCH_HOLD"
    assert sum("CROSS_SLIP_DUPLICATE_THESIS_EXPOSURE" in c["blockers"] for c in audit["cards"]) == 2
    assert audit["joint_probability"] is None
    assert audit["certified_sporting_probability"] is None
    assert audit["can_execute"] is False
    assert all(c["can_publish_as_certified"] is False for c in audit["cards"])
