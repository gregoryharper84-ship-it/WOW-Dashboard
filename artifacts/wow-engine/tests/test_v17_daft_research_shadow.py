"""Synthetic DAFT/LLP shadow regression tests (no paid content)."""
from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from v17.daft_research_market import (
    analyze_sharp_alert, comparable_clv, contract_key, decimal_implied_probability,
    deduplicate_captures, expected_return, extract_prop_hypotheses,
    normalize_capture, normalized_no_vig, quote_age_seconds, same_contract,
)
from v17.llp_daft_research_shadow import (
    build_annotation, install_llp_daft_research_shadow, load_private_signals,
)
from v17.daft_shadow_evaluation import (
    append_private_jsonl, evaluation_summary, prospective_snapshot,
    settle_shadow_snapshot,
)

NOW = "2026-10-29T18:00:00Z"
EVENT = "2026-10-29T22:00:00Z"


def contract(*, event="synthetic-001", family="MONEYLINE", selection="HOME",
             side="home", line=None, period="FULL_GAME", settlement="INCL_OT",
             overtime="INCLUDE", **extra):
    return {
        "official_event_id": event, "market_family": family, "period": period,
        "selection": selection, "side": side, "line": line,
        "settlement_basis": settlement, "overtime_rule": overtime, **extra,
    }


def capture(**updates):
    row = {
        "channel": "SHARP_BASKETBALL", "message_id": "synthetic-msg",
        "kind": "ORIGINAL_ALERT", "published_at": "2026-10-29T17:00:00Z",
        "captured_at": "2026-10-29T17:10:00Z", "event_start_at": EVENT,
        "contract": contract(), "version": "v1", "edit_history_complete": True,
    }
    row.update(updates)
    return row


def signal(**updates):
    row = {
        "contract": contract(), "research_eligibility": "PROSPECTIVE",
        "identity_verified": True, "offered_quote_at": "2026-10-29T17:59:30Z",
        "offered_decimal_odds": 2.10, "source_estimated_probability": 0.52,
        "verification_status": "UNRESOLVED",
    }
    row.update(updates)
    return row


def test_vip_chat_excluded():
    assert normalize_capture(capture(channel="VIP_CHAT")) is None


def test_all_sharp_channels_and_vip_lanes_accepted():
    for channel in ("VIP_PICKS", "VIP_RECAPS", "SHARP_US_FOOTBALL",
                    "SHARP_BASKETBALL", "SHARP_MLB", "SHARP_SOCCER",
                    "SHARP_NHL", "SHARP_GENERAL"):
        assert normalize_capture(capture(channel=channel))["channel"] == channel


def test_missing_provenance_or_timezone_fails_closed():
    with pytest.raises(ValueError):
        normalize_capture(capture(message_id=None))
    with pytest.raises(ValueError):
        normalize_capture(capture(captured_at="2026-10-29T17:10:00"))


def test_historical_capture_is_never_prospective():
    row = normalize_capture(capture(captured_at="2026-10-30T00:10:00Z"))
    assert row["research_eligibility"] == "RETROSPECTIVE_ONLY"
    assert normalize_capture(capture(edit_indicated=True, edit_history_complete=False))["research_eligibility"] == "RETROSPECTIVE_ONLY"


def test_updates_and_results_keep_distinct_provenance():
    rows = [capture(kind="ORIGINAL_ALERT"), capture(message_id="m2", kind="UPDATE",
             parent_alert_id="synthetic-msg"), capture(message_id="m3",
             kind="SOURCE_RESULT", source_reported_result="WIN")]
    ledger = deduplicate_captures(rows)
    assert ledger["source_rows"] == 3
    assert len(ledger["messages"]) == 3
    assert ledger["messages"][2]["independent_result"] is None


def test_deduplicates_same_capture_not_same_thesis():
    rows = [capture(), capture(), capture(message_id="other-msg")]
    ledger = deduplicate_captures(rows)
    assert ledger["distinct_message_versions"] == 2
    assert len(ledger["thesis_clusters"]) == 1
    assert len(next(iter(ledger["thesis_clusters"].values()))) == 2


def test_recapture_of_same_message_version_does_not_invent_new_version():
    rows = [capture(), capture(captured_at="2026-10-29T17:20:00Z")]
    ledger = deduplicate_captures(rows)
    assert ledger["distinct_capture_instances"] == 2
    assert ledger["distinct_message_versions"] == 1
    assert len(next(iter(ledger["message_versions"].values()))) == 2


def test_missing_exact_identity_is_not_merged_as_thesis():
    rows = [capture(contract=None), capture(message_id="second", contract=None)]
    assert len(deduplicate_captures(rows)["thesis_clusters"]) == 2


def test_prop_hypotheses_are_unverified_not_model_inputs():
    claims = extract_prop_hypotheses(
        "Starting after injury; more minutes against drop coverage, potential assists and ladder exposure")
    assert {c["kind"] for c in claims} >= {
        "ROLE_USAGE_REDISTRIBUTION", "MINUTES_ROTATION_WORKLOAD",
        "DEFENSIVE_ASSIGNMENT_COVERAGE", "CORRELATION_EXPOSURE",
    }
    assert all(c["verification_status"] == "UNVERIFIED" and c["model_input_effect"] is None
               for c in claims)


def test_exact_identity_includes_period_overtime_and_side():
    ref = contract()
    assert same_contract(ref, deepcopy(ref))
    assert not same_contract(ref, contract(period="FIRST_HALF"))
    assert not same_contract(ref, contract(overtime="EXCLUDE"))
    assert not same_contract(ref, contract(selection="AWAY", side="away"))


def test_signed_spreads_and_alternates_are_distinct():
    a = contract(family="SPREAD", line=-3.5)
    assert not same_contract(a, contract(family="SPREAD", line=3.5))
    assert not same_contract(a, contract(family="SPREAD", line=-2.5))
    assert contract_key(contract(family="SPREAD", line=None)) is None


def test_two_way_devig_and_missing_opponent():
    p = normalized_no_vig({"home": 1.91, "away": 1.91}, ("home", "away"))
    assert p["home"] == pytest.approx(.5)
    assert normalized_no_vig({"home": 1.91}, ("home", "away")) is None


def test_soccer_three_way_requires_draw():
    p = normalized_no_vig({"home": 2.50, "draw": 3.10, "away": 3.20},
                           ("home", "draw", "away"))
    assert sum(p.values()) == pytest.approx(1.0)
    assert normalized_no_vig({"home": 2.5, "away": 3.2},
                             ("home", "draw", "away")) is None


def test_soccer_1x2_alert_cannot_drop_the_draw():
    c = contract(family="SOCCER_1X2", settlement="REGULATION",
                 overtime="EXCLUDE", push_possible=False)
    r = analyze_sharp_alert(
        {"contract": c},
        {"contract": c, "prices": {"home": 2.1, "away": 3.2},
         "outcomes": ("home", "away")},
        {"contract": c, "decimal_odds": 2.3, "quote_at": NOW}, now=NOW)
    assert r["independent_market_no_vig"] is None
    assert r["independent_market_ev_push_free_only"] is None


def test_binary_ev_and_push_ev_differ():
    assert expected_return(.5, .5, 2.2) == pytest.approx(.1)
    assert expected_return(.4, .4, 2.2) == pytest.approx(.08)
    assert expected_return(.8, .3, 2.2) is None
    assert decimal_implied_probability(2.0) == .5


def test_alert_market_ev_is_not_sporting_probability():
    c = contract(push_possible=False)
    alert = {"contract": c, "source_estimated_probability": .55}
    ref = {"contract": c, "prices": {"home": 1.91, "away": 1.91},
           "outcomes": ("home", "away")}
    offered = {"contract": c, "decimal_odds": 2.20, "quote_at": NOW}
    result = analyze_sharp_alert(alert, ref, offered, now=NOW)
    assert result["independent_market_ev_push_free_only"] == pytest.approx(.1)
    assert result["source_market_probability"] == .55
    assert result["model_probability_mutated"] is False
    assert result["can_execute"] is False


def test_whole_number_push_unknown_prevents_market_ev():
    c = contract(family="TOTAL", selection="OVER", side="over", line=44.0,
                 push_possible=True)
    res = analyze_sharp_alert({"contract": c}, {"contract": c,
        "prices": {"over": 1.9, "under": 1.9},
        "outcomes": ("over", "under")},
        {"contract": c, "decimal_odds": 2.0, "quote_at": NOW}, now=NOW)
    assert res["independent_market_ev_push_free_only"] is None


def test_stale_and_missing_quote_timestamp():
    assert quote_age_seconds(None, NOW) is None
    assert quote_age_seconds("2026-10-29T17:00:00Z", NOW) == 3600
    res = analyze_sharp_alert(
        {"contract": contract(push_possible=False)},
        {"contract": contract(push_possible=False),
         "prices": {"home": 1.9, "away": 1.9}, "outcomes": ("home", "away")},
        {"contract": contract(push_possible=False), "decimal_odds": 2.1,
         "quote_at": "2026-10-29T17:00:00Z"}, now=NOW)
    assert not res["quote_fresh"]
    assert res["independent_market_ev_push_free_only"] is None


def test_comparable_clv_requires_same_contract_and_timestamps():
    entry = {"contract": contract(), "decimal_odds": 2.10,
             "quote_at": "2026-10-29T17:00:00Z", "event_start_at": EVENT}
    close = {"contract": contract(), "decimal_odds": 1.95,
             "quote_at": NOW, "is_closing_snapshot": True}
    assert comparable_clv(entry, close) == pytest.approx(.15)
    close["contract"] = contract(period="FIRST_HALF")
    assert comparable_clv(entry, close) is None
    close["contract"] = contract()
    close["is_closing_snapshot"] = False
    assert comparable_clv(entry, close) is None


def test_annotation_agrees_and_disagrees_without_probability_authority():
    a = build_annotation(contract(), [signal()], as_of=NOW)
    assert a["external_signal_status"] == "MATCHED"
    assert a["directional_relation"] == "AGREES"
    assert a["research_eligible"] and not a["probability_mutated"]
    disagree = build_annotation(contract(), [
        signal(contract=contract(selection="AWAY", side="away"))], as_of=NOW)
    assert disagree["directional_relation"] == "DISAGREES"
    assert not disagree["research_eligible"]


def test_annotation_never_borrows_adjacent_line():
    a = build_annotation(contract(family="SPREAD", line=-3.5),
        [signal(contract=contract(family="SPREAD", line=-2.5))], as_of=NOW)
    assert a["exact_contract_signal_count"] == 0
    assert not a["research_eligible"]


def test_annotation_without_private_feed_is_nonblocking():
    a = build_annotation(None, [], as_of=NOW, feed_status="PRIVATE_FEED_NOT_CONFIGURED")
    assert a["external_signal_status"] == "UNAVAILABLE"
    assert a["terminal_mutated"] is False


def test_shadow_installer_default_off_and_does_not_touch_scoring(monkeypatch):
    monkeypatch.delenv("WOW_LLP_DAFT_SHADOW_ENABLED", raising=False)
    module = SimpleNamespace(score_team_event_request=lambda *a, **k: {})
    assert install_llp_daft_research_shadow(module) is False


def test_shadow_installer_preserves_governed_package(monkeypatch, tmp_path):
    monkeypatch.setenv("WOW_LLP_DAFT_SHADOW_ENABLED", "true")
    monkeypatch.delenv("WOW_DAFT_PRIVATE_SIGNALS_PATH", raising=False)
    original = {"canonical_contract": contract(), "calibrated_home_probability": .625,
        "calibrated_home_lower_bound": .592, "rank_eligible": True,
        "terminal_status": "HELD", "blockers": ["PREEXISTING"], "can_execute": False}
    module = SimpleNamespace(score_team_event_request=lambda *a, **k: original)
    assert install_llp_daft_research_shadow(module) is True
    out = module.score_team_event_request(SimpleNamespace(), event_api=object())
    for key in ("calibrated_home_probability", "calibrated_home_lower_bound",
                "rank_eligible", "terminal_status", "blockers"):
        assert out[key] == original[key]
    assert out["llp_daft_research_shadow"]["can_execute"] is False
    assert out["llp_daft_research_shadow"]["external_signal_status"] == "UNAVAILABLE"


def test_private_snapshot_loader_does_not_load_repo_relative_paths(monkeypatch):
    monkeypatch.setenv("WOW_DAFT_PRIVATE_SIGNALS_PATH", "secrets.json")
    rows, status = load_private_signals()
    assert rows == [] and status == "PRIVATE_FEED_UNAVAILABLE"


def test_prospective_capture_freezes_immutable_pregame_snapshot():
    obs = {**capture(), "research_eligibility": "PROSPECTIVE"}
    snap = prospective_snapshot(obs)
    assert snap["immutable_snapshot_id"]
    assert "official_result" not in snap
    assert snap["can_execute"] is False


def test_retrospective_or_forged_result_cannot_enter_shadow_ledger():
    with pytest.raises(ValueError):
        prospective_snapshot({**capture(captured_at="2026-10-29T23:00:00Z"),
                              "research_eligibility": "PROSPECTIVE"})
    with pytest.raises(ValueError):
        prospective_snapshot({**capture(), "research_eligibility": "PROSPECTIVE",
                              "official_result": "WIN"})


def test_settlement_exact_line_and_independent_verification_required():
    snap = prospective_snapshot({**capture(), "research_eligibility": "PROSPECTIVE",
        "offer_decimal_odds": 2.0, "offer_quote_at": "2026-10-29T17:09:00Z"})
    assert not settle_shadow_snapshot(snap, {"contract": contract(line=-2.5,
        family="SPREAD"), "independently_verified": True,
        "official_result": "WIN", "settlement_source": "synthetic"})["graded"]
    unverified = settle_shadow_snapshot(snap, {"contract": contract(),
        "source_reported_result": "WIN", "official_result": "WIN"})
    assert not unverified["graded"]
    settled = settle_shadow_snapshot(snap, {"contract": contract(),
        "independently_verified": True, "official_result": "WIN",
        "settlement_source": "synthetic", "source_reported_result": "LOSS"})
    assert settled["source_result_agrees"] is False
    assert settled["available_price_return_per_unit"] == pytest.approx(1.0)


def test_source_result_for_different_alternate_line_does_not_grade():
    spread = contract(family="SPREAD", selection="AWAY", side="away", line=3.5)
    snap = prospective_snapshot({**capture(contract=spread),
        "research_eligibility": "PROSPECTIVE",
        "offer_decimal_odds": 2.0, "offer_quote_at": "2026-10-29T17:09:00Z"})
    shifted = contract(family="SPREAD", selection="AWAY", side="away", line=4.5)
    result = settle_shadow_snapshot(snap, {
        "contract": shifted, "independently_verified": True,
        "settlement_source": "synthetic", "official_result": "WIN",
        "source_reported_result": "WIN"})
    assert result["status"] == "EXACT_CONTRACT_UNRESOLVED"
    assert not result["graded"]


def test_evaluation_does_not_claim_incremental_outperformance():
    r = evaluation_summary([{"graded": True, "status": "INDEPENDENT_SETTLEMENT_RECORDED",
         "available_price_return_per_unit": 1.1, "comparable_clv_decimal": .1,
         "source_result_agrees": True}])
    assert r["priced_records"] == 1
    assert r["incremental_value_over_llp"] is None


def test_private_append_only_file_permission(tmp_path):
    target = tmp_path / "private-shadow.jsonl"
    append_private_jsonl(target, {"id": "synthetic-1", "can_execute": False})
    append_private_jsonl(target, {"id": "synthetic-2", "can_execute": False})
    rows = [json.loads(v) for v in target.read_text().splitlines()]
    assert len(rows) == 2
    assert all(r["can_execute"] is False for r in rows)
