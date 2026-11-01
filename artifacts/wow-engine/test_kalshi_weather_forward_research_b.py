"""Offline deterministic tests: Release B is never trading or auto-certification."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import copy

import pytest

from kalshi_weather_v2.forward_research_b import (
    MIN_PRIOR_SETTLED, MODEL_VERSION, build_release_b_research_report,
    evaluate_walk_forward, load_cases,
)

BASE = datetime(2026, 10, 1, 0, tzinfo=timezone.utc)


class FakeQuery:
    def __init__(self, records):
        self.records = records

    def select(self, _fields):
        return self

    def order(self, _key, desc=False):
        return self

    def limit(self, _limit):
        return self

    def execute(self):
        return SimpleNamespace(data=self.records)


class FakeClient:
    def __init__(self, fixture):
        self.fixture = fixture

    def table(self, table):
        return FakeQuery(self.fixture[table])


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def ledger(n=35):
    fixture = {
        "wow_kalshi_weather_predictions": [],
        "wow_kalshi_weather_outcomes": [],
        "wow_kalshi_weather_source_snapshots": [],
        "wow_kalshi_weather_contract_rules": [],
    }
    for i in range(n):
        target = BASE + timedelta(hours=i)
        decision = target - timedelta(minutes=20)
        forecast = 75.0
        official = forecast + (1 if i % 2 else -1)
        pid, rid, sid = f"pred-{i}", f"rule-{i}", f"source-{i}"
        fixture["wow_kalshi_weather_predictions"].append({
            "prediction_id": pid, "rule_snapshot_id": rid, "ticker": f"MIAMI-{i}",
            "decision_time": _iso(decision), "model_payload": {
                "lead_time_bucket": "H0", "contract": {
                    "lane": "HOURLY_TEMPERATURE",
                    "settlement_location_code": "KALSHI_WEATHER_INDEX:MIAMI",
                    "settlement_source": "Synoptic Data",
                    "rounding_convention": "USE_OFFICIAL_KALSHI_WEATHER_INDEX_AS_PUBLISHED;NO_ENGINE_ROUNDING",
                    "observation_window": "POINT_IN_TIME:" + _iso(target),
                    "threshold_lower": 75.0, "threshold_upper": None,
                    "lower_inclusive": False, "yes_condition": "index > 75°F",
                }, "forecast": {
                    "estimates": [{"model": "NWS_HOURLY", "provider": "NWS",
                                   "source_snapshot_id": sid,
                                   "target_time_utc": _iso(target),
                                   "temperature_f": forecast}]
                }
            }
        })
        fixture["wow_kalshi_weather_outcomes"].append({
            "prediction_id": pid, "settled_at": _iso(target + timedelta(minutes=30)),
            "settled_value": official, "settlement_source_name": "Synoptic Data",
            "yes_outcome": official > 75,
        })
        fixture["wow_kalshi_weather_source_snapshots"].append({
            "source_snapshot_id": sid, "provider": "NWS",
            "source_role": "PRIMARY_FORECAST",
            "issued_at": _iso(decision - timedelta(minutes=5)),
            "retrieved_at": _iso(decision),
        })
        fixture["wow_kalshi_weather_contract_rules"].append({
            "rule_snapshot_id": rid, "settlement_source_name": "Synoptic Data",
        })
    return fixture


def test_35_serialized_settlements_form_5_unpublished_walk_forward_candidates():
    report = build_release_b_research_report(FakeClient(ledger()))
    assert report["source_qc"]["examined"] == 35
    assert report["unique_point_in_time_cases"] == 35
    assert report["unique_settled_cases"] == 35
    assert report["shadow_candidate_count"] == 5
    assert report["graded_shadow_candidate_count"] == 5
    first = report["candidates"][0]
    assert first["prior_settled_samples"] == 30
    assert first["shadow_p_yes"] == 0.5
    assert first["shadow_p_no"] == 0.5
    assert first["model_version"] == MODEL_VERSION
    assert report["mean_brier"] is not None
    assert report["mean_log_loss"] is not None
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False
    assert all(c["status"] == "NO_PLAY_DATA_INSUFFICIENT" for c in report["candidates"])


def test_30_targets_only_give_no_forward_probability():
    report = build_release_b_research_report(FakeClient(ledger(30)))
    assert report["unique_settled_cases"] == 30
    assert report["shadow_candidate_count"] == 0
    assert report["mean_brier"] is None


def test_later_outcome_cannot_change_earlier_candidate_probability():
    fixture = ledger(35)
    initial = build_release_b_research_report(FakeClient(fixture))
    changed = copy.deepcopy(fixture)
    # This outcome is not available at its own decision time.
    changed["wow_kalshi_weather_outcomes"][-1]["settled_value"] = 100.0
    changed["wow_kalshi_weather_outcomes"][-1]["yes_outcome"] = True
    revised = build_release_b_research_report(FakeClient(changed))
    assert initial["candidates"][-1]["shadow_p_yes"] == revised["candidates"][-1]["shadow_p_yes"]
    assert initial["candidates"][-1]["brier"] != revised["candidates"][-1]["brier"]


def test_h1_does_not_augment_h0_prior_samples():
    fixture = ledger(30)
    fixture["wow_kalshi_weather_predictions"][0]["model_payload"]["lead_time_bucket"] = "H1"
    report = build_release_b_research_report(FakeClient(fixture))
    assert report["unique_point_in_time_cases"] == 29
    assert report["shadow_candidate_count"] == 0
    assert report["source_qc"]["excluded_invalid_or_unverified"] == 1


def test_unavailable_forecast_cannot_be_used_in_backtest():
    fixture = ledger(35)
    fixture["wow_kalshi_weather_source_snapshots"][0]["retrieved_at"] = (
        _iso(BASE + timedelta(hours=2))
    )
    report = build_release_b_research_report(FakeClient(fixture))
    assert report["source_qc"]["excluded_invalid_or_unverified"] == 1
    assert report["shadow_candidate_count"] == 4


def test_unknown_forecast_issue_time_is_rejected_even_with_recent_capture():
    fixture = ledger(35)
    fixture["wow_kalshi_weather_source_snapshots"][0]["issued_at"] = None
    report = build_release_b_research_report(FakeClient(fixture))
    assert report["source_qc"]["excluded_invalid_or_unverified"] == 1


def test_wrong_settlement_index_or_source_is_rejected_not_substituted():
    fixture = ledger(35)
    fixture["wow_kalshi_weather_outcomes"][0]["settlement_source_name"] = "NWS"
    fixture["wow_kalshi_weather_predictions"][1]["model_payload"]["contract"]["settlement_location_code"] = "KALSHI_WEATHER_INDEX:NYC"
    report = build_release_b_research_report(FakeClient(fixture))
    assert report["source_qc"]["excluded_invalid_or_unverified"] == 2


def test_rule_yes_condition_must_match_official_index_and_boundary():
    fixture = ledger(35)
    fixture["wow_kalshi_weather_predictions"][0]["model_payload"]["contract"]["yes_condition"] = "index >= 75°F"
    fixture["wow_kalshi_weather_outcomes"][1]["yes_outcome"] = False
    report = build_release_b_research_report(FakeClient(fixture))
    assert report["source_qc"]["excluded_invalid_or_unverified"] == 2


def test_duplicate_hour_chooses_earliest_decision_not_best_fitted_error():
    fixture = ledger(35)
    duplicate = copy.deepcopy(fixture["wow_kalshi_weather_predictions"][0])
    duplicate["prediction_id"] = "duplicate-later"
    duplicate["decision_time"] = _iso(BASE - timedelta(minutes=10))
    duplicate["model_payload"]["forecast"]["estimates"][0]["temperature_f"] = 100.0
    fixture["wow_kalshi_weather_predictions"].insert(0, duplicate)
    report = build_release_b_research_report(FakeClient(fixture))
    assert report["source_qc"]["excluded_duplicate_target"] == 1
    assert report["unique_point_in_time_cases"] == 35
    assert report["candidates"][0]["prior_settled_samples"] == 30


def test_same_wall_clock_hour_at_dst_is_not_same_utc_target():
    fixture = ledger(2)
    # Distinct UTC targets represent distinct index windows even if the local
    # city displays 01:00 twice on fallback day.
    cases, _ = load_cases(FakeClient(fixture))
    assert len(set(c.key for c in cases)) == 2


def test_future_settlement_not_in_prior_information_set():
    fixture = ledger(35)
    # Defer publication of all the earliest 30 outcomes until after last decision.
    late = _iso(BASE + timedelta(hours=40))
    for o in fixture["wow_kalshi_weather_outcomes"][:30]:
        o["settled_at"] = late
    report = build_release_b_research_report(FakeClient(fixture))
    assert report["shadow_candidate_count"] == 0


def test_cannot_weaken_sample_gate_or_publish_directly():
    cases, _ = load_cases(FakeClient(ledger(35)))
    with pytest.raises(ValueError, match="MINIMUM_PRIOR_SAMPLE_GATE"):
        evaluate_walk_forward(cases, min_prior=5)
    assert MIN_PRIOR_SETTLED == 30
    assert not any(hasattr(c, "market_price") for c in cases)
