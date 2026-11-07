"""Regression: a full page does not prove the settlement ledger is exhausted."""
import kalshi_weather_v2.shadow_cohort as shadow
from types import SimpleNamespace


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.n = None

    def select(self, *_a, **_kw): return self
    def order(self, *args, **kwargs): return self
    def limit(self, n):
        self.n = n
        return self
    def execute(self): return SimpleNamespace(data=self.rows[:self.n])


class DB:
    def table(self, name):
        if name.endswith("outcomes"):
            return Query([])
        return Query([{
            "prediction_id": str(i),
            "model_payload": {"contract": {"lane": "DAILY_TEMPERATURE"}}
        } for i in range(501)])


def test_extra_row_causes_incomplete_backlog(monkeypatch):
    monkeypatch.setattr(shadow, "ReadOnlyJsonClient",
                        lambda: SimpleNamespace(close=lambda: None))
    result = shadow.run_bounded_settlement_backlog_once(
        client=DB(), now="2026-11-02T19:00:00Z",
    )
    assert result.status == shadow.SETTLEMENT_BACKLOG_PARTIAL
    assert result.truncated is True
    assert result.attempted == 0
    assert "SETTLEMENT_BACKLOG_SCAN_TRUNCATED" in result.failures


def test_exact_boundary_is_exhaustive(monkeypatch):
    monkeypatch.setattr(shadow, "ReadOnlyJsonClient",
                        lambda: SimpleNamespace(close=lambda: None))
    class ExactDB(DB):
        def table(self, name):
            if name.endswith("outcomes"):
                return Query([])
            return Query([{"prediction_id": str(i), "model_payload": {"contract": {"lane": "DAILY_TEMPERATURE"}}} for i in range(500)])
    result = shadow.run_bounded_settlement_backlog_once(
        client=ExactDB(), now="2026-11-02T19:00:00Z"
    )
    assert result.status == shadow.SETTLEMENT_BACKLOG_CLEARED
    assert result.truncated is False
