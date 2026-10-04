from types import SimpleNamespace

import pytest

from kalshi_weather_v2.persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError, content_id
from kalshi_weather_v2.probability_change_ledger import (
    AttributionDomain,
    ProbabilityAttributionComponent,
    build_probability_change_record,
)


class FakeQuery:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self.insert_row = None
        self.limit_n = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def lte(self, key, value):
        self.filters.append(("lte:" + key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def insert(self, row):
        self.insert_row = dict(row)
        return self

    def execute(self):
        if self.insert_row is not None:
            pk = self.table.primary_key
            key = self.insert_row[pk]
            if key in self.table.rows:
                raise RuntimeError("duplicate")
            self.table.rows[key] = dict(self.insert_row)
            return SimpleNamespace(data=[dict(self.insert_row)])

        rows = list(self.table.rows.values())
        for key, value in self.filters:
            if key.startswith("lte:"):
                field = key[4:]
                rows = [row for row in rows if row.get(field) <= value]
            else:
                rows = [row for row in rows if row.get(key) == value]
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        return SimpleNamespace(data=[dict(row) for row in rows])


class FakeTable:
    def __init__(self, primary_key):
        self.primary_key = primary_key
        self.rows = {}


class FakeClient:
    def __init__(self):
        self.tables = {
            "wow_kalshi_weather_predictions": FakeTable("prediction_id"),
            "wow_kalshi_weather_outcomes": FakeTable("outcome_id"),
            "wow_kalshi_weather_probability_changes": FakeTable("probability_change_id"),
            "wow_runtime_capabilities": FakeTable("capability_key"),
        }

    def table(self, name):
        table = self.tables.setdefault(name, FakeTable("id"))
        return FakeQuery(table)


def test_content_id_is_deterministic_and_prefix_scoped():
    payload = {"b": 2, "a": 1}
    assert content_id("x", payload) == content_id("x", {"a": 1, "b": 2})
    assert content_id("x", payload) != content_id("y", payload)


def test_insert_exact_retry_returns_existing_identical_row():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    row = {"prediction_id": "p1", "ticker": "T", "can_execute": False}
    first = store._insert_exact("wow_kalshi_weather_predictions", "prediction_id", row)
    second = store._insert_exact("wow_kalshi_weather_predictions", "prediction_id", row)
    assert first == second
    assert len(client.tables["wow_kalshi_weather_predictions"].rows) == 1


def test_insert_exact_identity_collision_fails_closed():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    store._insert_exact(
        "wow_kalshi_weather_predictions",
        "prediction_id",
        {"prediction_id": "p1", "ticker": "T1", "can_execute": False},
    )
    with pytest.raises(KalshiWeatherPersistenceError) as exc:
        store._insert_exact(
            "wow_kalshi_weather_predictions",
            "prediction_id",
            {"prediction_id": "p1", "ticker": "T2", "can_execute": False},
        )
    assert exc.value.code == "KALSHI_WEATHER_IDENTITY_COLLISION"


def test_missing_runtime_capability_fails_closed():
    client = FakeClient()
    row = KalshiWeatherPersistence(client).load_runtime_capability()
    assert row["capability_status"] == "UNAVAILABLE"
    assert row["can_execute"] is False


def test_outcome_scoring_does_not_require_publication():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    row = store.persist_outcome(
        outcome_id="o1",
        prediction_id="p1",
        settled_at="2026-09-10T18:00:00Z",
        settlement_source_name="Synoptic Data",
        settlement_source_url=None,
        settled_value=82.1,
        yes_outcome=True,
        settlement_payload={"status": "normal"},
        p_yes=0.75,
    )
    assert abs(row["brier_score"] - 0.0625) < 1e-12
    assert row["log_loss"] > 0


def test_probability_change_persistence_is_idempotent_and_append_only_shaped():
    client = FakeClient()
    store = KalshiWeatherPersistence(client)
    component = ProbabilityAttributionComponent(
        component_id="station",
        domain=AttributionDomain.STATION_TRAJECTORY,
        label="Station trajectory",
        delta_probability=0.04,
        evidence_ids=("asos-1",),
        available_at="2026-10-04T16:01:00Z",
        method="POINT_IN_TIME_RECOMPUTE_V1",
    )
    record = build_probability_change_record(
        ticker="KXHIGHDFW-TEST",
        previous_prediction_id="p-before",
        current_prediction_id="p-after",
        before_decision_time="2026-10-04T15:55:00Z",
        after_decision_time="2026-10-04T16:05:00Z",
        p_yes_before=0.70,
        p_yes_after=0.74,
        components=(component,),
        market_context_snapshot_ids=("market-1",),
    )
    first = store.persist_probability_change(record)
    second = store.persist_probability_change(record)
    assert first == second
    assert first["can_execute"] is False
    assert first["attribution_components"][0]["domain"] == "STATION_TRAJECTORY"
    assert len(client.tables["wow_kalshi_weather_probability_changes"].rows) == 1
