from datetime import datetime, timezone
import hashlib
import pytest

from v17_diagnostics import MarketComparison, TemporalFeatureProvenance, HypothesisChange, monitor_model_disagreement
from v17_connector_contract import ConnectorPolicy, immutable_evidence_snapshot
from v17_public_connectors import NWSStadiumWeatherConnector, OddsAPISnapshotConnector, OddsCreditBudget, ConnectorFailure

UTC = timezone.utc
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def test_disagreement_only_triggers_review_and_never_suppresses():
    result = monitor_model_disagreement(MarketComparison("e1", .70, .52, .55, .54, NOW), persistent_prior_gaps=[.11, .12])
    assert result["review_status"] == "REVIEW_REQUIRED"
    assert result["automatic_suppression"] is False and result["probability_unchanged"] is True


def test_temporal_provenance_rejects_future_known_feature():
    row = TemporalFeatureProvenance("lineup", "a" * 64, "s1", NOW, NOW, NOW, datetime(2026, 9, 2, 11, tzinfo=UTC), "official post")
    with pytest.raises(ValueError, match="TEMPORAL_FEATURE_LEAKAGE"):
        row.validate()


def test_hypothesis_requires_untouched_later_holdout():
    change = HypothesisChange("c1", "fatigue lowers late efficiency", "rest_days", "DECREASE",
        datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC),
        datetime(2025, 7, 1, tzinfo=UTC), datetime(2025, 9, 1, tzinfo=UTC), {"brier": .21}, {"brier": .19})
    assert change.ledger_record()["automatic_promotion"] is False


def test_connector_contract_is_evidence_only_by_default():
    snap = immutable_evidence_snapshot(policy=ConnectorPolicy("x", "PUBLIC", 60), payload={"a": 1}, request_timestamp=NOW, source_published_timestamp=NOW)
    assert snap["immutable_raw_snapshot"] and not snap["model_authoritative"] and not snap["can_execute"]


def test_nws_follows_only_official_forecast_link():
    def fetch(url, headers, params):
        if "/points/" in url: return {"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/X/1,2/forecast/hourly"}}, {}
        return {"properties": {"periods": [{"temperature": 72}]}}, {}
    snap = NWSStadiumWeatherConnector(fetch, "WOW/17 contact@example.com").stadium_forecast(40, -75, event_id="g1", requested_at=NOW)
    assert snap["source_identity"] == "NWS_API" and snap["allowed_model_fields"] == ()


def test_odds_budget_fails_before_network_and_secret_not_persisted():
    calls = []
    def fetch(url, headers, params): calls.append(params); return [], {"x-requests-used": "6"}
    connector = OddsAPISnapshotConnector(fetch, OddsCreditBudget(10, reserve=5, used=5), "secret")
    with pytest.raises(ConnectorFailure, match="CREDIT_BUDGET"):
        connector.snapshot("baseball_mlb", regions="us", markets="h2h", event_id=None, requested_at=NOW)
    assert calls == []
