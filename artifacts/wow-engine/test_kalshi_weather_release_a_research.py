"""Fail-closed Release A research and browser-auth regression tests."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

import api_kalshi_weather_v2 as api
from kalshi_weather_v2.research_release_a import build_research_snapshot


DATE = "2026-11-01T03:00:00Z"
PRED = {
    "prediction_id": "prediction-1",
    "rule_snapshot_id": "rule-1",
    "ticker": "KXTEMPMIAH-26OCT3123-T74.99",
    "decision_time": "2026-11-01T02:40:00Z",
    "central_estimate_f": 78.1,
    "probability_status": "SHADOW_BLOCKED",
    "blockers": ["CALIBRATION_PROFILE_UNAVAILABLE"],
    "model_version": "SHADOW-ONLY",
    "model_payload": {
        "lead_time_bucket": "H0",
        "forecast": {"estimates": [{"model": "NWS_HOURLY", "temperature_f": 80.0}]},
        "contract": {
            "lane": "HOURLY_TEMPERATURE",
            "settlement_location_code": "KALSHI_WEATHER_INDEX:MIAMI",
            "market_title": 'Will "Miami" <script>alert(1)</script> exceed 74.99?',
            "yes_condition": "index > 74.99°F",
            "no_condition": "index <= 74.99°F",
            "observation_window": DATE.replace("03:00", "03:00"),
            "timezone": "EDT",
            "rounding_convention": "NO_ENGINE_ROUNDING",
        },
    },
}
PRED["model_payload"]["contract"]["observation_window"] = "POINT_IN_TIME:2026-11-01T03:00:00Z"
OUTCOME = {
    "prediction_id": "prediction-1",
    "settled_at": "2026-11-01T03:10:00Z",
    "settled_value": 80.6,
    "settlement_source_name": "Synoptic Data",
    "yes_outcome": True,
}
RULE = {"rule_snapshot_id": "rule-1", "settlement_source_name": "Synoptic Data", "settlement_source_url": "https://example.invalid/index"}
QUOTE = {
    "ticker": PRED["ticker"],
    "retrieved_at": "2026-11-01T02:40:00Z",
    "market_status": "active",
    "yes_best_ask": 0.65,
    "no_best_ask": 0.38,
}


class FakeQuery:
    def __init__(self, records, broken=False):
        self.records = records
        self.broken = broken

    def select(self, _columns):
        return self

    def order(self, _column, desc=False):
        return self

    def limit(self, _count):
        return self

    def execute(self):
        if self.broken:
            raise RuntimeError("do-not-leak-secret-internal-db-error")
        return SimpleNamespace(data=self.records)


class FakeClient:
    def __init__(self, predictions=None, outcomes=None, broken=False):
        self.broken = broken
        self.data = {
            "wow_kalshi_weather_predictions": [PRED] if predictions is None else predictions,
            "wow_kalshi_weather_outcomes": [OUTCOME] if outcomes is None else outcomes,
            "wow_kalshi_weather_contract_rules": [RULE],
            "wow_kalshi_weather_market_microstructure_snapshots": [QUOTE],
        }

    def table(self, table_name):
        return FakeQuery(self.data[table_name], broken=self.broken)


def _auth():
    token = base64.b64encode(b"weather:research-secret").decode()
    return {"Authorization": "Basic " + token}


def test_release_a_research_deduplicates_and_does_not_publish_probabilities():
    data = build_research_snapshot(
        client=FakeClient(predictions=[PRED, {**PRED, "prediction_id": "prediction-older"}]),
        now=datetime(2026, 11, 1, 3, 15, tzinfo=timezone.utc),
    )
    assert data["loaded_forecasts"] == 2
    assert data["calibration"]["eligible_unique_settled_samples"] == 1
    assert data["calibration"]["remaining_to_candidate"] == 29
    assert data["calibration"]["historical_mae_f"] == 2.5
    assert data["safety"]["can_execute"] is False
    row = data["predictions"][0]
    assert row["model_p_yes"] is None
    assert row["raw_edge"] is None
    assert row["rank_eligible"] is False
    assert row["status"] == "NO_PLAY_DATA_INSUFFICIENT"
    assert row["settlement_source"] == "Synoptic Data"
    assert row["settlement_station_id"] is None
    assert row["quote_fresh"] is False
    assert row["settled_value_f"] == 80.6


def test_research_calibration_uses_earliest_asof_per_target_across_versions():
    earliest = {**PRED, "prediction_id": "early", "decision_time": "2026-11-01T02:10:00Z",
                "central_estimate_f": 76.6, "model_version": "OLD"}
    latest = {**PRED, "prediction_id": "later", "decision_time": "2026-11-01T02:40:00Z",
              "central_estimate_f": 78.1, "model_version": "NEW"}
    outcomes = [
        {**OUTCOME, "prediction_id": "early"},
        {**OUTCOME, "prediction_id": "later"},
    ]
    data = build_research_snapshot(
        client=FakeClient(predictions=[latest, earliest], outcomes=outcomes),
        now=datetime(2026, 11, 1, 3, 15, tzinfo=timezone.utc),
    )
    assert data["calibration"]["eligible_unique_settled_samples"] == 1
    assert data["calibration"]["historical_mae_f"] == 4.0
    assert data["calibration"]["historical_signed_bias_f"] == -4.0


def test_release_a_filters_different_cities_and_fails_closed_when_empty():
    other = {**PRED, "model_payload": {**PRED["model_payload"], "contract": {**PRED["model_payload"]["contract"], "settlement_location_code": "KALSHI_WEATHER_INDEX:NYC"}}}
    data = build_research_snapshot(client=FakeClient(predictions=[other]), now=datetime(2026, 11, 1, 3, 15, tzinfo=timezone.utc))
    assert data["loaded_forecasts"] == 0
    assert data["status"] == "NO_PLAY_DATA_INSUFFICIENT"


def test_research_browser_requires_configured_basic_auth(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_API_KEY", "research-secret")
    monkeypatch.setattr(api, "get_client", lambda: FakeClient())
    client = TestClient(api.app)
    without = client.get("/research")
    assert without.status_code == 401
    assert "WWW-Authenticate" in without.headers
    wrong = client.get("/research", headers={"Authorization": "Basic " + base64.b64encode(b"wrong:research-secret").decode()})
    assert wrong.status_code == 401
    assert client.get("/research", headers=_auth()).status_code == 200
    assert client.get("/research.json", headers=_auth()).status_code == 200
    monkeypatch.delenv("WOW_KALSHI_WEATHER_API_KEY", raising=False)
    assert client.get("/research", headers=_auth()).status_code == 503


def test_release_a_html_escapes_untrusted_market_title_and_no_secrets(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_API_KEY", "research-secret")
    monkeypatch.setattr(api, "get_client", lambda: FakeClient())
    response = TestClient(api.app).get("/research", headers=_auth())
    assert response.status_code == 200
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
    assert "research-secret" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "NO_PLAY_DATA_INSUFFICIENT" in response.text


def test_release_a_data_error_does_not_expose_db_detail(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_API_KEY", "research-secret")
    monkeypatch.setattr(api, "get_client", lambda: FakeClient(broken=True))
    response = TestClient(api.app).get("/research.json", headers=_auth())
    assert response.status_code == 503
    assert "do-not-leak-secret" not in response.text
    assert response.json()["detail"] == "RESEARCH_LEDGER_UNAVAILABLE"


def test_release_a_stale_forecasts_are_not_labeled_fresh():
    data = build_research_snapshot(
        client=FakeClient(), now=datetime(2026, 11, 2, 3, 15, tzinfo=timezone.utc)
    )
    assert data["collection_fresh"] is False
    assert data["predictions"][0]["forecast_fresh"] is False
    assert data["predictions"][0]["quote_fresh"] is False
    assert data["predictions"][0]["last_recorded_yes_ask"] == 0.65
    assert data["predictions"][0]["model_p_no"] is None
