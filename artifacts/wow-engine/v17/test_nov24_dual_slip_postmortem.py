"""November 24 three-leg/five-leg chat slip retrospective; NO model attribution.

Read-only, source-described settled chat selections. These were hypothetical
assistant-generated slips, not authenticated submissions, PrizePicks tickets or
fitted forecasts. Original overlapping slips are excluded to avoid double
counting; only the final three-leg and five-leg cards are scored.

The fixture has no official event ID. Tests that exercise game-level exposure
attach an explicitly synthetic ``CHAT_GAME_LABEL:`` identifier so the
construction report can be audited without pretending an official event
identity was verified.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

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
    / "2026-11-24-dual-slip-postmortem.json"
)
_AS_OF = "2026-11-24T22:00:00Z"
_START = "2026-11-25T00:00:00Z"


def _source():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _retro_payload(*, synthetic_event_ids: bool = False):
    source = _source()
    labels = source["pick_verification"]
    legs = []
    positions = []
    for slip_id, ids in source["final_slips"].items():
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
            item = dict(source["verified_picks"][pick_id])
            if synthetic_event_ids:
                item["event_id"] = "CHAT_GAME_LABEL:" + labels[pick_id]["chat_game_label"]
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
        preserve=["exact line and reported settlement", "unique picks counted once"],
        refine=[
            "matchup-specific opportunity",
            "calibrated joint probability",
            "game-level cross-slip exposure",
        ],
        regression_checks=[
            "8 unique picks, 4 wins / 4 losses",
            "3-leg 2/3 and 5-leg 2/5",
            "no probability backfill",
        ],
        legs=legs,
        positions=positions,
    )
    return build_postmortem_payload(batch)


def _preflight_leg(row: str, event: str, player: str, *, stat="POINTS", line=20.5, side="MORE", sport="NBA"):
    return {
        "row_id": row,
        "sport": sport,
        "official_event_id": event,
        "event_id": event,
        "home_team_id": "HOME",
        "away_team_id": "AWAY",
        "market_family": "PLAYER_PROP",
        "market_period": "FULL_GAME",
        "side": side,
        "exact_line": line,
        "settlement_basis": "INCL_OT",
        "event_start_utc": _START,
        "source_snapshot_at_utc": "2026-11-24T20:00:00Z",
        "source_snapshot_receipt_id": "synthetic-snapshot-" + event,
        "official_player_id": "SYNTHETIC:" + player,
        "stat_type": stat,
        "participant": player,
        "direction": side,
    }


def _preflight_cards(*, card_labels: dict[str, str] | None = None, leg_labels: dict[str, str] | None = None):
    source = _source()
    labels = source["pick_verification"]
    cards = []
    for slip_id, ids in source["final_slips"].items():
        legs = []
        for pick_id in ids:
            item = source["verified_picks"][pick_id]
            leg = _preflight_leg(
                f"{slip_id}:{pick_id}",
                "SYNTHETIC:" + labels[pick_id]["chat_game_label"],
                pick_id,
                stat=item["market"],
                line=item["exact_line"],
                side=item["side"],
                sport=item["sport"],
            )
            if leg_labels and pick_id in leg_labels:
                leg["confidence_label"] = leg_labels[pick_id]
            legs.append(leg)
        card = {"card_id": slip_id, "structure": "POWER", "legs": legs}
        if card_labels and slip_id in card_labels:
            card["confidence_label"] = card_labels[slip_id]
        cards.append(card)
    return cards


def test_nov24_fixture_reconciles_with_reported_scorecard():
    source = _source()
    picks = source["verified_picks"]
    card = source["reported_scorecard"]
    results = Counter(item["settled_result"] for item in picks.values())
    assert len(picks) == 8
    assert (results["WIN"], results["LOSS"]) == (4, 4)
    assert card["wins"] == 4 and card["losses"] == 4
    assert card["overall_accuracy"] == 0.5
    assert card["perfect_slips"] == 0 and card["slip_count"] == 2
    assert len(source["final_slips"]["THREE_LEG"]) == 3
    assert len(source["final_slips"]["FIVE_LEG"]) == 5
    # No selection may appear in both scored cards.
    assert not set(source["final_slips"]["THREE_LEG"]) & set(source["final_slips"]["FIVE_LEG"])
    boundaries = source["evidence_boundaries"]
    for key in (
        "verified_submission", "verified_prizepicks_settlement",
        "verified_financial_returns", "verified_wow_pregame_model_receipt",
        "verified_official_final_totals", "verified_production_model_execution",
        "recordable_to_governed_calibration", "engineering_changes_deployed",
        "can_execute",
    ):
        assert boundaries[key] is False
    assert sorted(boundaries["partial_snapshot_confirmed_winning_thresholds"]) == [
        "braun_assists", "marshall_steals"
    ]


def test_nov24_unique_selection_and_slip_level_results():
    payload = _retro_payload()
    summary = payload["summary"]
    assert (summary["legs"], summary["wins"], summary["losses"]) == (8, 4, 4)
    assert summary["leg_hit_rate"] == 0.5
    assert summary["distinct_selections"] == 8
    assert summary["reused_selection_groups"] == 0
    assert summary["reused_selection_extra_positions"] == 0
    assert summary["max_selection_position_exposure"] == 1
    assert summary["positions"] == 2
    assert summary["all_leg_clear_positions"] == 0
    assert summary["shared_losing_hinge_count"] == 0
    positions = {p["position_reference"]: p for p in payload["positions"]}
    three = positions["SLIP_THREE_LEG"]
    five = positions["SLIP_FIVE_LEG"]
    assert (three["leg_wins"], three["leg_losses"]) == (2, 1)
    assert (five["leg_wins"], five["leg_losses"]) == (2, 3)
    assert round(three["leg_hit_rate"], 4) == 0.6667
    assert round(five["leg_hit_rate"], 4) == 0.4
    assert three["all_legs_hit"] is False and five["all_legs_hit"] is False


def test_nov24_sport_and_direction_splits_match_the_postmortem():
    payload = _retro_payload()
    reported = _source()["reported_scorecard"]
    legs = payload["legs"]

    def tally(predicate):
        rows = [leg for leg in legs if predicate(leg)]
        wins = sum(1 for leg in rows if leg["settled_result"] == "WIN")
        return wins, len(rows)

    assert tally(lambda leg: leg["sport"] == "NBA") == (3, 6) == (
        reported["nba"]["wins"], reported["nba"]["legs"]
    )
    assert tally(lambda leg: leg["sport"] == "NHL") == (1, 2) == (
        reported["nhl"]["wins"], reported["nhl"]["legs"]
    )
    assert tally(lambda leg: leg["side"] == "MORE") == (3, 6) == (
        reported["more"]["wins"], reported["more"]["legs"]
    )
    assert tally(lambda leg: leg["side"] == "LESS") == (1, 2) == (
        reported["less"]["wins"], reported["less"]["legs"]
    )


def test_nov24_never_backfills_probability_or_invents_economics():
    payload = _retro_payload()
    summary = payload["summary"]
    assert summary["matched_pregame_count"] == 0
    assert summary["calibration_eligible_count"] == 0
    assert summary["roi"] is None
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
    reed = next(leg for leg in payload["legs"] if leg["row_key"] == "FIVE_LEG:reed_rebounds_assists")
    assert reed["actual_stat"] == 7
    assert reed["margin_to_line"] == -0.5


def test_nov24_missing_official_event_identity_is_flagged_not_certified():
    report = _retro_payload()["slip_construction_postmortem"]
    assert report["source_identity_basis"] == "EXACT_OBSERVED_FIELDS_NOT_OFFICIAL_IDENTITY_ATTESTATION"
    assert report["distinct_event_count"] == 0
    assert report["cross_slip_shared_event_groups"] == 0
    assert report["cross_slip_same_player_event_groups"] == 0
    assert report["independent_joint_probability"] is None
    assert report["accepted_quote_verified"] is False
    assert report["realized_roi_verified"] is False
    assert report["can_execute"] is False
    for card in report["cards"]:
        flags = card["construction_flags"]
        assert "OFFICIAL_EVENT_ID_MISSING" in flags
        assert "JOINT_SCENARIO_RECEIPT_UNVERIFIED" in flags
        assert "ACCEPTED_PAYOUT_QUOTE_UNVERIFIED" in flags
        assert card["accepted_ticket_settlement"] is None
        assert card["realized_profit_verified"] is False
        assert card["observed_all_legs_clear"] is False
    five = next(card for card in report["cards"] if card["position_reference"] == "SLIP_FIVE_LEG")
    assert "FOUR_PLUS_LEGS_FRAGILITY" in five["construction_flags"]


def test_nov24_game_level_cross_slip_exposure_is_evaluated_not_hidden_by_distinct_players():
    report = _retro_payload(synthetic_event_ids=True)["slip_construction_postmortem"]
    assert report["cross_slip_same_player_event_groups"] == 0
    assert report["shared_losing_hinge_count"] == 0
    assert report["distinct_event_count"] == 4
    assert report["cross_slip_shared_event_groups"] == 3
    assert report["cross_slip_shared_event_position_occurrences"] == 3
    assert report["game_identity_basis"] == (
        "EXACT_OBSERVED_SPORT_EVENT_PAIR_NOT_OFFICIAL_ATTESTATION"
    )
    cards = {card["position_reference"]: card for card in report["cards"]}
    three, five = cards["SLIP_THREE_LEG"], cards["SLIP_FIVE_LEG"]
    # Every leg of the three-leg card sits in a game also used by the five-leg card.
    assert three["cross_slip_shared_event_legs"] == 3
    assert three["distinct_event_count"] == 3
    assert "CROSS_SLIP_GAME_EXPOSURE_UNVERIFIED" in three["construction_flags"]
    # Detroit appears twice inside the five-leg card but in no other scored card.
    assert five["same_event_extra_legs"] == 1
    assert five["cross_slip_shared_event_legs"] == 3
    assert "SAME_EVENT_DEPENDENCY_UNVERIFIED" in five["construction_flags"]
    assert "CROSS_SLIP_GAME_EXPOSURE_UNVERIFIED" in five["construction_flags"]
    assert report["independent_joint_probability"] is None


def test_nov24_strong_confidence_claims_fail_closed_for_label_variants():
    cards = _preflight_cards(
        card_labels={"THREE_LEG": "STRONG CONFIDENCE", "FIVE_LEG": "strong-confidence"},
        leg_labels={"hauser_points": "Lock of the Day"},
    )
    audit = audit_research_slips(cards, as_of_utc=_AS_OF)
    assert audit["status"] == "RESEARCH_HOLD"
    assert audit["joint_probability"] is None
    assert audit["certified_sporting_probability"] is None
    assert audit["can_execute"] is False
    for card in audit["cards"]:
        assert "SLIP_JOINT_CONFIDENCE_UNVERIFIED" in card["blockers"]
        assert card["joint_probability_certified"] is False
        assert card["can_publish_as_certified"] is False
        assert card["can_execute"] is False
    five = next(card for card in audit["cards"] if card["card_id"] == "FIVE_LEG")
    hauser = next(leg for leg in five["legs"] if leg["row_id"] == "FIVE_LEG:hauser_points")
    assert "RESEARCH_STRONG_CONFIDENCE_CLAIM_UNVERIFIED" in hauser["blockers"]
    assert hauser["model_probability_verified"] is False


def test_nov24_three_leg_plus_five_leg_shared_games_force_a_research_hold():
    """The exact November 24 case: 8 different players, 3 shared games.

    Distinct players are not independent outcomes. Both proposed cards must be
    held until a joint verification receipt exists, not merely flagged.
    """
    audit = audit_research_slips(_preflight_cards(), as_of_utc=_AS_OF)
    assert audit["status"] == "RESEARCH_HOLD"
    cards = {card["card_id"]: card for card in audit["cards"]}
    for slip_id in ("THREE_LEG", "FIVE_LEG"):
        card = cards[slip_id]
        assert "CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED" in card["blockers"]
        assert card["cross_slip_shared_game_count"] == 3
        assert card["status"] == "RESEARCH_HOLD"
        assert card["joint_probability"] is None
        assert card["joint_probability_certified"] is False
        assert card["can_publish_as_certified"] is False
        assert card["can_execute"] is False
    three = {leg["row_id"]: leg for leg in cards["THREE_LEG"]["legs"]}
    five = {leg["row_id"]: leg for leg in cards["FIVE_LEG"]["legs"]}
    # All three three-leg picks sit in games the five-leg card also uses.
    for row_id in (
        "THREE_LEG:davis_steals",
        "THREE_LEG:mcdavid_points",
        "THREE_LEG:braun_assists",
    ):
        assert "CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED" in three[row_id]["blockers"]
    # The two Detroit picks are the only legs with no cross-card game exposure.
    assert "CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED" not in five["FIVE_LEG:jenkins_blocks"]["blockers"]
    assert "CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED" not in five["FIVE_LEG:reed_rebounds_assists"]["blockers"]
    for row_id in (
        "FIVE_LEG:marshall_steals",
        "FIVE_LEG:hauser_points",
        "FIVE_LEG:draisaitl_points",
    ):
        assert "CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED" in five[row_id]["blockers"]


def test_nov24_caller_supplied_joint_receipt_cannot_clear_the_hold():
    """Only a governed/authenticated joint artifact could clear this hold.

    A receipt typed into the same untrusted research payload is exactly what this
    preflight exists to disbelieve, so it must not unblock the shared-game hold.
    """
    cards = _preflight_cards()
    for card in cards:
        card["cross_slip_joint_verification"] = {
            "source_receipt_id": "synthetic-joint-scenario-sim",
            "card_ids": ["THREE_LEG", "FIVE_LEG"],
            "event_keys": [
                "NBA|SYNTHETIC:BOS-WAS",
                "NBA|SYNTHETIC:DAL-DEN",
                "NHL|SYNTHETIC:VGK-EDM",
            ],
            "joint_scenario_count": 1000,
            "frozen_at_utc": "2026-11-24T21:00:00Z",
            "joint_model_artifact_hash": "synthetic-joint-model-hash",
            "calibration_receipt_id": "synthetic-joint-calibrator",
        }
    audit = audit_research_slips(cards, as_of_utc=_AS_OF)
    assert audit["status"] == "RESEARCH_HOLD"
    for card in audit["cards"]:
        assert "CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED" in card["blockers"]
        assert card["status"] == "RESEARCH_HOLD"
        assert card["joint_probability"] is None
        assert card["can_publish_as_certified"] is False
        assert card["can_execute"] is False


def test_nov24_third_proposal_on_a_shared_game_blocks_despite_distinct_players():
    event = "SYNTHETIC:BOS-WAS"
    cards = [
        {
            "card_id": f"shared-game-{index}",
            "structure": "POWER",
            "legs": [
                _preflight_leg(f"{index}-a", event, f"player-{index}-a", line=20.5 + index),
                _preflight_leg(f"{index}-b", event, f"player-{index}-b", line=5.5 + index),
            ],
        }
        for index in range(3)
    ]
    audit = audit_research_slips(cards, as_of_utc=_AS_OF)
    assert audit["cross_slip_event_exposure_counts"][f"NBA|{event}"] == 3
    # The shared game holds all three cards, even though every player differs.
    for card in audit["cards"]:
        assert "CROSS_SLIP_EVENT_EXPOSURE_UNVERIFIED" in card["blockers"]
        assert card["status"] == "RESEARCH_HOLD"
        assert card["can_publish_as_certified"] is False
    # The block is game-level, not a repeated exact selection.
    assert "CROSS_SLIP_DUPLICATE_THESIS_EXPOSURE" not in audit["cards"][2]["blockers"]
