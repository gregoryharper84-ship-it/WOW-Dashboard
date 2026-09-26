from __future__ import annotations

from datetime import datetime, timezone

import v17.ncaaf_model_maintenance as maintenance
from ncaaf_cfbd_client import CFBDUnavailable


class _CountResult:
    def __init__(self, count):
        self.count = count
        self.data = []


class _CountQuery:
    def __init__(self, counts):
        self.counts = counts
        self.season = None

    def select(self, *_args, **kwargs):
        assert kwargs.get("count") == "exact"
        return self

    def eq(self, name, value):
        assert name == "season"
        self.season = int(value)
        return self

    def limit(self, value):
        assert value == 1
        return self

    def execute(self):
        value = self.counts[self.season]
        if isinstance(value, Exception):
            raise value
        return _CountResult(value)


class _CountDB:
    def __init__(self, counts):
        self.counts = counts

    def table(self, name):
        assert name == "wow_ncaaf_training_games"
        return _CountQuery(self.counts)


def _now():
    return datetime(2026, 9, 26, tzinfo=timezone.utc)


def test_established_historical_corpus_refreshes_current_season_only():
    db = _CountDB({2022: 896, 2023: 910, 2024: 919, 2025: 934})
    seasons, plan = maintenance._default_season_plan(db, now=_now())

    assert seasons == (2026,)
    assert plan["mode"] == "CURRENT_SEASON_REFRESH"
    assert plan["historical_season_counts"] == {
        "2022": 896,
        "2023": 910,
        "2024": 919,
        "2025": 934,
    }
    assert plan["can_execute"] is False


def test_sparse_historical_corpus_keeps_five_season_bootstrap():
    db = _CountDB({2022: 896, 2023: 910, 2024: 120, 2025: 934})
    seasons, plan = maintenance._default_season_plan(db, now=_now())

    assert seasons == (2022, 2023, 2024, 2025, 2026)
    assert plan["mode"] == "BOOTSTRAP_FIVE_SEASON"
    assert plan["reason"] == "HISTORICAL_CORPUS_INSUFFICIENT"
    assert plan["can_execute"] is False


def test_corpus_inspection_failure_fails_safe_to_bootstrap():
    db = _CountDB({2022: RuntimeError("db unavailable"), 2023: 910, 2024: 919, 2025: 934})
    seasons, plan = maintenance._default_season_plan(db, now=_now())

    assert seasons == (2022, 2023, 2024, 2025, 2026)
    assert plan["mode"] == "BOOTSTRAP_FIVE_SEASON"
    assert plan["reason"] == "HISTORICAL_CORPUS_INSPECTION_UNAVAILABLE"
    assert plan["error_type"] == "RuntimeError"
    assert plan["can_execute"] is False


def test_explicit_seasons_remain_authoritative(monkeypatch):
    class DummyDB:
        pass

    def unavailable(_cls):
        raise CFBDUnavailable("CFBD_API_KEY_MISSING", "missing")

    monkeypatch.setattr(maintenance.CFBDClient, "from_environment", classmethod(unavailable))
    monkeypatch.setattr(
        maintenance,
        "materialize_complete_training_features",
        lambda _db: {
            "complete_feature_rows": 0,
            "market_features_used": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    monkeypatch.setattr(
        maintenance,
        "train_result_form_candidate",
        lambda _db, *, training_code_sha: {
            "ok": True,
            "lifecycle_state": "CANDIDATE",
            "probability_publishable": False,
            "can_execute": False,
        },
    )

    result = maintenance.run_ncaaf_model_maintenance(
        DummyDB(), seasons=[2024, 2026], weeks=[1], training_code_sha="a" * 40
    )

    assert result["seasons"] == [2024, 2026]
    assert result["acquisition_plan"]["mode"] == "EXPLICIT_SEASON_RANGE"
    assert result["acquisition_plan"]["seasons"] == [2024, 2026]
    assert result["can_execute"] is False
