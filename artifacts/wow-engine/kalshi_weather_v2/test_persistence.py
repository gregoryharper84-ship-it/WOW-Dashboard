from types import SimpleNamespace

import pytest

from kalshi_weather_v2.persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError, content_id


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
