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


def test_health_detects_missing_database_settings_without_exposing_values(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    response = TestClient(api.app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database_status"] == "UNAVAILABLE"
    assert body["database_diagnostic"] == "DATABASE_CONFIGURATION_INCOMPLETE"
    assert "SUPABASE_SERVICE_KEY" not in response.text
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False


def test_health_distinguishes_failed_ledger_read_from_missing_config(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "do-not-expose-this-test-key")

    class Broken:
        def load_runtime_capability(self):
            raise api.KalshiWeatherPersistenceError("LEDGER_READ_FAILED", "secret-internal-detail")

    monkeypatch.setattr(api, "_persistence", lambda: Broken())
    response = TestClient(api.app).get("/health")
    body = response.json()
    assert body["database_diagnostic"] == "DATABASE_CAPABILITY_READ_FAILED"
    assert body["database_status"] == "UNAVAILABLE"
    assert "secret-internal-detail" not in response.text
    assert "do-not-expose" not in response.text
    assert body["can_execute"] is False


def test_health_confirms_database_read_without_claiming_calibration(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "redacted")
    class Healthy:
        def load_runtime_capability(self):
            return {"capability_status": "UNAVAILABLE"}
    monkeypatch.setattr(api, "_persistence", lambda: Healthy())
    response = TestClient(api.app).get("/health")
    body = response.json()
    assert body["database_status"] == "AVAILABLE"
    assert body["database_diagnostic"] == "DATABASE_READ_VERIFIED"
    assert body["governed_probability_capability"] == "UNAVAILABLE"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
