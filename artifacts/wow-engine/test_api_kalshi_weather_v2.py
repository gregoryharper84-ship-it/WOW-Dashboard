from fastapi.testclient import TestClient

import api_kalshi_weather_v2 as api


def test_shadow_route_requires_separate_bearer_key(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_API_KEY", "secret-test-key")
    client = TestClient(api.app)
    response = client.post(
        "/v2/hourly/shadow",
        json={
            "ticker": "KXTEMPNYCHS-TEST",
            "index_city": "nyc",
            "expected_location": "New York City",
            "forecast_latitude": 40.7829,
            "forecast_longitude": -73.9654,
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"]["can_execute"] is False


def test_shadow_request_rejects_caller_supplied_decision_time(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_API_KEY", "secret-test-key")
    client = TestClient(api.app)
    response = client.post(
        "/v2/hourly/shadow",
        headers={"Authorization": "Bearer secret-test-key"},
        json={
            "ticker": "KXTEMPNYCHS-TEST",
            "index_city": "nyc",
            "expected_location": "New York City",
            "forecast_latitude": 40.7829,
            "forecast_longitude": -73.9654,
            "decision_time": "2026-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 422


def test_shadow_route_forces_nonexecution_and_passes_no_decision_time(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_API_KEY", "secret-test-key")
    seen = {}

    def fake_get_client():
        return object()

    def fake_capture(**kwargs):
        seen.update(kwargs)
        return {
            "status": "SHADOW_CAPTURED",
            "prediction_id": "p1",
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(api, "get_client", fake_get_client)
    monkeypatch.setattr(api, "capture_hourly_shadow", fake_capture)
    client = TestClient(api.app)
    response = client.post(
        "/v2/hourly/shadow",
        headers={"Authorization": "Bearer secret-test-key"},
        json={
            "ticker": "KXTEMPNYCHS-TEST",
            "index_city": "nyc",
            "expected_location": "New York City",
            "forecast_latitude": 40.7829,
            "forecast_longitude": -73.9654,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert "decision_time" not in seen
    assert seen["ticker"] == "KXTEMPNYCHS-TEST"


def test_settlement_route_requires_auth(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_API_KEY", "secret-test-key")
    client = TestClient(api.app)
    response = client.post(
        "/v2/hourly/settle",
        json={"prediction_id": "p1", "index_city": "nyc"},
    )
    assert response.status_code == 401


def test_health_never_claims_execution(monkeypatch):
    class Persistence:
        def load_runtime_capability(self):
            return {
                "capability_key": "KALSHI_WEATHER_PROBABILITY",
                "capability_status": "UNAVAILABLE",
                "can_execute": False,
            }

    monkeypatch.setattr(api, "_persistence", lambda: Persistence())
    client = TestClient(api.app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "SHADOW"
    assert body["governed_probability_capability"] == "UNAVAILABLE"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
