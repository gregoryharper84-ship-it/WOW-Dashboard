"""No fake engine identity, private account leakage or model promotion."""
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).with_name("research") / "kalshi_slip_learning_packet.py"
spec = importlib.util.spec_from_file_location("kalshi_slip_learning_packet", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def diag(structure="SINGLE_PLAYER_SAME_GAME_MULTI_MARKET", factors=None, **updates):
    row = {
        "status": "STRUCTURAL_TOPOLOGY_ONLY_NOT_MODEL_CERTIFIED",
        "structure": structure,
        "shared_failure_factors": factors or [],
        "calibration_eligible": False,
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    row.update(updates)
    return row


def snapshot(**updates):
    row = {
        "economics_basis": "PAYOUT_MINUS_ENTRY_BEFORE_UNOBSERVED_FEES",
        "fees_observed": False,
        "total_vs_settlement_payout_mismatch_rows": 1,
        "model_feature_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    row.update(updates)
    return row


def test_method_signals_can_be_read_by_both_but_not_be_published():
    r = m.build_research_packet(topology_diagnostics=[
        diag(factors=["COMPLEMENTARY_STATS_SHARED_PLAYER_ROLE"]),
        diag("MULTI_PLAYER_SAME_GAME", [
            "SAME_TEAM_RECEIVING_OPPORTUNITY_COMPETITION"
        ]),
    ], snapshot_audit=snapshot())
    codes = {item["finding_code"] for item in r["findings"]}
    assert "SINGLE_PLAYER_COMMON_CAUSE" in codes
    assert "MULTI_PLAYER_GAME_SCRIPT_FRAGILITY" in codes
    assert "SAME_TEAM_TARGET_SHARE_COMPETITION" in codes
    assert "FEE_BASIS_UNVERIFIED" in codes
    assert "TOTAL_PAYOUT_NOT_SETTLEMENT_OUTCOME" in codes
    assert r["target_consumer_hosts"] == list(m.CONSUMERS)
    assert r["source_host"] is None
    assert r["cross_host_published"] is False
    assert r["model_feature_eligible"] is False
    assert r["probability_publishable"] is False
    assert r["can_execute"] is False


def test_raw_account_identifiers_and_probability_are_not_reflected():
    r = m.build_research_packet(topology_diagnostics=[
        diag(factors=["VERIFIED_NESTED_THRESHOLD_REDUNDANCY"],
             account_number="NOT_FOR_OUTPUT", market_id="DO_NOT_COPY",
             pregame_joint_probability=0.95),
    ])
    representation = str(r)
    assert "NOT_FOR_OUTPUT" not in representation
    assert "DO_NOT_COPY" not in representation
    assert "0.95" not in representation
    assert r["origin_provenance"].startswith("USER_ACCOUNT")


@pytest.mark.parametrize("tamper", [
    {"can_execute": True},
    {"model_feature_eligible": True},
    {"calibration_eligible": True},
    {"probability_publishable": True},
    {"status": "CERTIFIED"},
    {"structure": None},
    {"shared_failure_factors": "SINGLE_PLAYER_COMMON_CAUSE"},
])
def test_unsafe_topology_cannot_create_packet(tamper):
    with pytest.raises(m.ResearchPacketHold):
        m.build_research_packet(topology_diagnostics=[diag(**tamper)])


@pytest.mark.parametrize("tamper", [
    {"fees_observed": True},
    {"economics_basis": "FEE_ADJUSTED_NET"},
    {"can_execute": True},
    {"total_vs_settlement_payout_mismatch_rows": -1},
])
def test_snapshot_requires_truthful_fee_basis(tamper):
    with pytest.raises(m.ResearchPacketHold):
        m.build_research_packet(topology_diagnostics=[], snapshot_audit=snapshot(**tamper))


def test_empty_packet_does_not_fabricate_findings():
    r = m.build_research_packet(topology_diagnostics=[])
    assert r["findings"] == []
    assert r["topology_groups_inspected"] == 0
