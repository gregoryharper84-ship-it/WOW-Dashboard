"""Aligned scenario-payout research tests; no live offers or fitted-source draws."""
from copy import deepcopy
import hashlib
import pytest

from v17.niche_prop_quote_integrity import seal_capture_for_trusted_collector
from v17.niche_prop_joint_shadow import (
    JointShadowHold, evaluate_joint_shadow,
)

KEY = b"synthetic-signed-capture-key-length-32" * 2
RIGHTS = {"synthetic-licensed-provider": "synthetic-permission"}
AS_OF = "2026-11-23T20:00:00Z"
START = "2026-11-23T22:00:00Z"


def assemble():
    signed_offers = []
    for i in range(5):
        offer = {
            "kind": "PROP_OFFER", "platform": "PRIZEPICKS",
            "sport": "NBA", "settlement_basis": "FULL_GAME_INCL_OT",
            "source_id": "synthetic-licensed-provider",
            "rights_receipt_id": "synthetic-permission",
            "source_snapshot_sha256": hashlib.sha256(f"synthetic-offer-{i}".encode()).hexdigest(),
            "offer_id": f"offer-{i}", "official_event_id": f"event-{i}",
            "official_player_ids": [f"nba-player-{i}"],
            "category": "NBA_LOW_VOLUME_UNDER",
            "stat_type": "FREE_THROW_ATTEMPTS", "market_period": "FULL_GAME",
            "side": "LESS", "exact_line": 4.5,
            "projection_type": "STANDARD", "status": "ACTIVE",
            "captured_at_utc": "2026-11-23T19:55:00Z",
            "event_start_utc": START,
        }
        signed_offers.append(seal_capture_for_trusted_collector(offer, KEY))
    payout = {
        "kind": "LINEUP_PAYOUT", "platform": "PRIZEPICKS",
        "source_id": "synthetic-licensed-provider", "rights_receipt_id": "synthetic-permission",
        "source_snapshot_sha256": hashlib.sha256(b"synthetic-payout").hexdigest(),
        "structure": "FLEX", "leg_count": 5,
        "ordered_offer_binding_digests": [x["payload_digest"] for x in signed_offers],
        "payout_by_hits": {"0": 0., "1": 0., "2": 0., "3": .4, "4": 2., "5": 10.},
        "captured_at_utc": "2026-11-23T19:58:00Z",
        "earliest_event_start_utc": START,
    }
    signed_payout = seal_capture_for_trusted_collector(payout, KEY)
    scenarios = {
        "joint_source_receipt_id": "synthetic-joint-scenarios",
        "frozen_at_utc": "2026-11-23T19:59:00Z",
        "source_snapshot_sha256": hashlib.sha256(b"synthetic-model-snapshot").hexdigest(),
        "model_artifact_digests": [hashlib.sha256(f"synthetic-fitted-artifact-{i}".encode()).hexdigest()
                                   for i in range(5)],
        "ordered_offer_binding_digests": payout["ordered_offer_binding_digests"],
        "draws": [],
    }
    for i in range(1000):
        # 200 draw outcomes 5/5, 400 outcomes 4/5, 400 outcomes 3/5.
        hits = 5 if i < 200 else 4 if i < 600 else 3
        scenarios["draws"].append({
            "scenario_id": f"synthetic-draw-{i}",
            "outcomes": ["WIN"] * hits + ["LOSS"] * (5 - hits),
        })
    return signed_offers, signed_payout, scenarios


def evaluate(offers, payout, scenarios):
    return evaluate_joint_shadow(offers, payout, scenarios,
                                 secret=KEY, approved_sources=RIGHTS,
                                 as_of_utc=AS_OF)


def test_aligned_joint_flex_economics_do_not_assume_independence():
    offers, payout, scenarios = assemble()
    frozen = deepcopy((offers,payout,scenarios))
    result = evaluate(offers, payout, scenarios)
    assert (offers,payout,scenarios) == frozen
    assert result["status"] == "SHADOW_UNCERTIFIED"
    assert result["scenario_count"] == 1000
    assert result["hit_count_distribution"]["5"] == pytest.approx(.2)
    assert result["hit_count_distribution"]["4"] == pytest.approx(.4)
    assert result["hit_count_distribution"]["3"] == pytest.approx(.4)
    assert result["research_gross_payout_multiple"] == pytest.approx(2.96)
    assert result["research_all_clear_fraction"] == pytest.approx(.2)
    assert result["calibrated_joint_probability"] is None
    assert result["positive_ev_certified"] is False
    assert result["source_rights_independently_verified"] is False
    assert not result["can_execute"]


def test_perfectly_correlated_draws_produce_joint_half_not_independent_product():
    offers, payout, scenario = assemble()
    for i, draw in enumerate(scenario["draws"]):
        draw["outcomes"] = ["WIN"] * 5 if i < 500 else ["LOSS"] * 5
    r = evaluate(offers, payout, scenario)
    assert r["research_all_clear_fraction"] == pytest.approx(0.5)
    assert r["research_all_clear_fraction"] != pytest.approx(0.5**5)


@pytest.mark.parametrize("change,error", [
    ("duplicate", "DUPLICATE_SCENARIO"),
    ("missing_leg", "SCENARIO_OUTCOME_SHAPE"),
    ("void", "SCENARIO_UNQUOTED_PUSH_VOID"),
    ("short", "MINIMUM_JOINT_DRAWS"),
    ("wrong_binding", "JOINT_LINEUP_BINDING"),
    ("missing_model", "JOINT_MODEL_LINEAGE"),
    ("late", "JOINT_FROZEN_CHRONOLOGY"),
])
def test_missing_partial_unquoted_stale_and_unbound_draws_hold(change, error):
    offers, payout, scenarios = assemble()
    if change == "duplicate":
        scenarios["draws"][1]["scenario_id"] = scenarios["draws"][0]["scenario_id"]
    elif change == "missing_leg":
        scenarios["draws"][0]["outcomes"] = ["WIN"] * 4
    elif change == "void":
        scenarios["draws"][0]["outcomes"][0] = "VOID"
    elif change == "short":
        scenarios["draws"] = scenarios["draws"][:10]
    elif change == "wrong_binding":
        scenarios["ordered_offer_binding_digests"] = scenarios["ordered_offer_binding_digests"][::-1]
    elif change == "missing_model":
        scenarios["model_artifact_digests"][0] = ""
    else:
        scenarios["frozen_at_utc"] = "2026-11-23T20:01:00Z"
    with pytest.raises(JointShadowHold, match=error):
        evaluate(offers, payout, scenarios)


def test_wrong_quote_and_payout_tamper_is_not_considered_scenario_ev():
    offers, payout, scenario = assemble()
    offers[0]["payload"]["exact_line"] = 9.5
    with pytest.raises(Exception, match="SIGNATURE"):
        evaluate(offers, payout, scenario)
    offers, payout, scenario = assemble()
    payout["payload"]["payout_by_hits"]["4"] = 1000
    with pytest.raises(Exception, match="SIGNATURE"):
        evaluate(offers, payout, scenario)
