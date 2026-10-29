"""Synthetic-only regressions for DAFT/LLP observational integration."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from v17.daft_external_research import (
    CAN_EXECUTE, dedupe_messages_and_theses, evaluate_frozen_shadow,
    exact_contract_clv, exact_contract_key, expected_return_per_unit,
    llp_research_annotation, no_vig_from_complete_quotes,
    normalize_research_record, quote_age_seconds, reconcile_alert_result,
    structure_claims,
)
from v17.daft_llp_research_shadow import install_daft_llp_research_shadow


def contract(**overrides):
    data = {
        "sport": "NFL", "league": "NFL", "official_event_id": "synthetic-42",
        "market_family": "OUTRIGHT_WINNER", "period": "FULL_GAME",
        "selection": "Team A", "exact_line": "NONE",
        "settlement_rule": "FULL_GAME_INCLUDING_OVERTIME", "outcome_space": "HOME_AWAY",
        "direction": "Team A",
    }
    data.update(overrides)
    return data


def message(**overrides):
    data = contract()
    data.update({
        "kind": "SHARP_ALERT", "channel": "synthetic-nfl", "message_id": "m-01",
        "published_at": "2026-10-29T10:00:00Z",
        "event_start_at": "2026-10-29T15:00:00Z",
        "capture_attested": True, "quote_timestamp": "2026-10-29T10:00:00Z",
        "source_market_probability": 0.52,
    })
    data.update(overrides)
    return data


def normalize(**overrides):
    return normalize_research_record(
        message(**overrides), capture_at="2026-10-29T10:01:00Z"
    )


def quotes(outcomes, prices, **overrides):
    return [
        {**contract(), "bookmaker": "Fictional Book",
         "quote_timestamp": "2026-10-29T10:00:00Z",
         "outcome": selection, "american_odds": price, **overrides}
        for selection, price in zip(outcomes, prices)
    ]


def test_prospective_requires_attested_capture_and_known_exact_contract():
    assert normalize()["prospective_eligible"]
    assert not normalize(capture_attested=False)["prospective_eligible"]
    assert not normalize(official_event_id=None)["prospective_eligible"]
    assert not normalize(edit_at="2026-10-29T10:02:00Z")["prospective_eligible"]
    late = normalize_research_record(message(), capture_at="2026-10-29T16:00:00Z")
    assert not late["prospective_eligible"]
    assert late["research_eligibility"] == "RETROSPECTIVE_OR_UNRESOLVED"


def test_unknown_timestamps_and_missing_identity_never_imputed():
    record = normalize(published_at=None, quote_timestamp=None)
    assert record["published_at"] is None
    assert record["quote_timestamp"] is None
    assert not record["prospective_eligible"]
    assert exact_contract_key(record["contract"]) is not None


def test_edits_have_distinct_versions_but_one_betting_thesis():
    a = normalize()
    b = normalize_research_record(
        message(body="synthetic edit", edit_at="2026-10-29T10:02:00Z"),
        capture_at="2026-10-29T10:03:00Z",
    )
    result = dedupe_messages_and_theses([a, a, b])
    assert len(result["unique_versions"]) == 2
    assert result["duplicate_version_instances"] == 1
    assert len(result["thesis_message_ids"]) == 1


def test_structured_reasoning_is_unverified_without_independent_evidence():
    claims = structure_claims([
        {"mechanism": "ROLE_USAGE_REDISTRIBUTION", "claim": "synthetic injury theory"},
        {"mechanism": "BENEFICIARY_ALLOCATION",
         "verification_status": "INDEPENDENTLY_VERIFIED",
         "independent_evidence": ["synthetic timestamped official lineup"]},
    ])
    assert claims[0]["verification_status"] == "UNVERIFIED"
    assert claims[1]["verification_status"] == "INDEPENDENTLY_VERIFIED"
    assert all(c["probability_effect"] is None for c in claims)


def test_binary_normalization_with_two_complete_prices():
    result = no_vig_from_complete_quotes(quotes(["HOME", "AWAY"], [-110, -110]),
                                         outcome_space=["HOME", "AWAY"])
    assert result["probabilities"]["HOME"] == pytest.approx(0.5)
    assert result["prediction_authority"] is False


def test_soccer_three_way_requires_draw_and_retains_it():
    prices = quotes(["HOME", "DRAW", "AWAY"], [130, 220, 180],
                    outcome_space="HOME_DRAW_AWAY")
    result = no_vig_from_complete_quotes(prices, outcome_space=["HOME", "DRAW", "AWAY"])
    assert result["status"] == "RESEARCH_ESTIMATE_ONLY"
    assert set(result["probabilities"]) == {"HOME", "DRAW", "AWAY"}
    assert sum(result["probabilities"].values()) == pytest.approx(1)
    missing_draw = no_vig_from_complete_quotes(prices[:2], outcome_space=["HOME", "DRAW", "AWAY"])
    assert missing_draw["probabilities"] is None


def test_mixed_market_period_and_different_bookmaker_cannot_be_devigged():
    a, b = quotes(["HOME", "AWAY"], [-110, -110])
    assert no_vig_from_complete_quotes([a, {**b, "period": "FIRST_HALF"}],
                                        outcome_space=["HOME", "AWAY"])["probabilities"] is None
    assert no_vig_from_complete_quotes([a, {**b, "bookmaker": "Other"}],
                                        outcome_space=["HOME", "AWAY"])["probabilities"] is None


def test_push_aware_ev_and_binary_ev():
    assert expected_return_per_unit(p_win=0.5, p_loss=0.5, american_odds=100) == pytest.approx(0)
    assert expected_return_per_unit(p_win=0.45, p_loss=0.4, american_odds=100) == pytest.approx(0.05)
    with pytest.raises(ValueError):
        expected_return_per_unit(p_win=0.7, p_loss=0.4, american_odds=-110)


def test_signed_spreads_and_alternate_lines_are_different_contracts():
    base = contract(market_family="SPREAD", exact_line="-3")
    assert exact_contract_key(base) != exact_contract_key({**base, "exact_line": "-3.5"})
    assert exact_contract_key(base) != exact_contract_key({**base, "exact_line": "+3"})
    assert exact_contract_key(base) != exact_contract_key({**base, "settlement_rule": "REGULATION_ONLY"})


def test_quote_age_and_exact_closing_line_comparison():
    assert quote_age_seconds(None, "2026-10-29T10:02:00Z") is None
    assert quote_age_seconds("2026-10-29T11:00:00Z", "2026-10-29T10:00:00Z") is None
    entry = {**contract(), "quote_timestamp": "2026-10-29T10:00:00Z", "american_odds": 120}
    close = {**entry, "quote_timestamp": "2026-10-29T12:00:00Z", "american_odds": 100}
    assert exact_contract_clv(entry, close) > 0
    assert exact_contract_clv(entry, {**close, "exact_line": "-3.5"}) is None


def test_result_must_reconcile_exact_offer_and_not_source_grade_alone():
    alert = {**contract(), "alert_id": "A1"}
    wrong_line = {**alert, "exact_line": "-3.5",
                  "source_reported_result": "WIN",
                  "independently_reconciled_result": "WIN"}
    assert not reconcile_alert_result(alert, wrong_line)["eligible_for_exact_grade"]
    exact = {**alert, "independently_reconciled_result": "LOSS"}
    assert reconcile_alert_result(alert, exact)["eligible_for_exact_grade"]


def test_stale_and_retrospective_signals_never_change_annotation():
    base = contract()
    current = llp_research_annotation(base, [normalize()], observed_at="2026-10-29T10:01:30Z")
    assert current["external_signal"] == "AVAILABLE"
    assert current["source_market_probability"] == 0.52
    assert current["market_estimate_is_governed_probability"] is False
    stale = llp_research_annotation(base, [normalize()], observed_at="2026-10-29T10:45:00Z")
    assert stale["external_signal"] == "UNAVAILABLE"
    past = llp_research_annotation(base, [
        normalize_research_record(message(), capture_at="2026-10-29T16:00:00Z")
    ], observed_at="2026-10-29T16:01:00Z")
    assert past["external_signal"] == "UNAVAILABLE"


def test_shadow_wrapper_never_changes_native_probability_or_terminal(monkeypatch):
    monkeypatch.setenv("WOW_DAFT_LLP_SHADOW_ENABLED", "true")
    result = {
        "calibrated_home_probability": 0.61, "calibrated_home_lower_bound": 0.56,
        "calibrated_away_probability": 0.39, "calibrated_away_lower_bound": 0.32,
        "terminal_status": "HELD", "blockers": ["MODEL_INPUTS_INSUFFICIENT"],
        "rank_eligible": False, "can_execute": False,
    }
    original = deepcopy(result)
    req = SimpleNamespace(**{
        "sport": "NFL", "league": "NFL", "official_event_id": "synthetic-42",
        "market_family": "OUTRIGHT_WINNER", "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "home_team": "Team A", "away_team": "Team B",
    })
    module = SimpleNamespace(score_team_event_request=lambda *a, **k: result)
    assert install_daft_llp_research_shadow(module, observations=[normalize()])
    wrapped = module.score_team_event_request(req, event_api=object())
    assert result == original
    for name in original:
        assert wrapped[name] == original[name]
    assert wrapped["daft_external_research_shadow"]["home"]["external_signal"] in {"AVAILABLE", "UNAVAILABLE"}
    assert wrapped["can_execute"] is False
    assert not CAN_EXECUTE


def test_shadow_disabled_when_flag_false(monkeypatch):
    monkeypatch.setenv("WOW_DAFT_LLP_SHADOW_ENABLED", "false")
    module = SimpleNamespace(score_team_event_request=lambda *a, **k: {})
    assert not install_daft_llp_research_shadow(module)


def test_frozen_shadow_deduplicates_theses_and_excludes_hindsight():
    one = {
        "official_event_id": "E1", "canonical_thesis_id": "T1",
        "prospective_eligible": True, "exact_result_verified": True,
        "same_contract_settlement": True, "governed_calibrated_probability": 0.7,
        "outcome": 1,
    }
    later = {**one, "official_event_id": "E2", "prospective_eligible": False}
    grade = evaluate_frozen_shadow([one, one, later])
    assert grade["graded_unique_theses"] == 1
    assert grade["brier"] == pytest.approx(0.09)
    assert grade["promotable"] is False and not grade["can_execute"]



def test_reference_move_rejects_adjacent_line_and_stale_offer():
    from v17.daft_external_research import analyze_reference_and_offer
    opening = {**contract(), "quote_timestamp": "2026-10-29T10:00:00Z",
               "american_odds": -110}
    current = {**opening, "quote_timestamp": "2026-10-29T10:30:00Z",
               "american_odds": -125}
    offered = {**current, "american_odds": -105}
    features = analyze_reference_and_offer(
        opening, current, offered, observed_at="2026-10-29T10:30:30Z"
    )
    assert features["status"] == "MARKET_RESEARCH_ONLY"
    assert features["reference_raw_implied_move_pp"] > 0
    assert features["independent_fair_probability"] is None
    assert features["expected_return_per_unit"] is None
    assert analyze_reference_and_offer(
        opening, current, {**offered, "exact_line": "-3.5"},
        observed_at="2026-10-29T10:30:30Z",
    )["status"] == "NOT_COMPARABLE"
    assert analyze_reference_and_offer(
        opening, current, offered, observed_at="2026-10-29T10:35:00Z",
    )["status"] == "NOT_COMPARABLE"


def test_source_result_agreement_does_not_count_unverified_as_loss():
    from v17.daft_external_research import source_result_agreement
    rows = [
        {"source_reported_result": "WIN", "independently_reconciled_result": "WIN",
         "exact_selected_offer_match": True},
        {"source_reported_result": "PUSH", "independently_reconciled_result": None,
         "exact_selected_offer_match": False},
    ]
    result = source_result_agreement(rows)
    assert result["verified_comparable_rows"] == 1
    assert result["agreement_count"] == 1
    assert result["unresolved_count"] == 1


def test_private_historical_ingest_keeps_raw_package_private(tmp_path):
    import json
    import zipfile
    from v17.daft_private_historical_ingest import import_historical_packages
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    sharp = private / "synthetic-sharp.zip"
    vip = private / "synthetic-vip.zip"
    with zipfile.ZipFile(sharp, "w") as archive:
        archive.writestr("alerts_research_only.json", json.dumps([
            {"kind": "alert", "message_id": "x1", "channel": "US_FOOTBALL",
             "play": "fictional Team A -3", "alert_id": "100"},
        ]))
        archive.writestr("results_source_reported.json", json.dumps([
            {"kind": "result", "message_id": "x2", "channel": "US_FOOTBALL",
             "result_reported": "WIN", "alert_id": "100"},
        ]))
    with zipfile.ZipFile(vip, "w") as archive:
        archive.writestr("nba_reported_ledger.json", json.dumps({
            "entries": [{"recap_message_id": "x3",
                         "selection_as_reported": "fictional player over 9.5"}],
        }))
    output = private / "index.json"
    summary = import_historical_packages(
        sharp_zip=str(sharp), vip_zip=str(vip),
        captured_at="2026-10-29T15:00:00Z", destination=str(output),
    )
    assert summary["total_imported"] == 3
    assert summary["prospective_eligible"] == 0
    assert (output.stat().st_mode & 0o077) == 0
    parsed = json.loads(output.read_text())
    assert all(not row["normalized"]["prospective_eligible"] for row in parsed["rows"])
    with pytest.raises(FileExistsError):
        import_historical_packages(
            sharp_zip=str(sharp), vip_zip=str(vip),
            captured_at="2026-10-29T15:00:00Z", destination=str(output),
        )
