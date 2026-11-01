"""Regression coverage for isolated 512MiB low-memory shadow capture.

Disabled by default; no paid infrastructure, probability publication, or trading.
"""
from __future__ import annotations

from types import SimpleNamespace

import kalshi_weather_v2.bounded_cohort as bounded
import kalshi_weather_v2.empirical_runtime as runtime
from kalshi_weather_v2.shadow_cohort import CohortRunResult, HourlyCohortTarget


def target():
    return HourlyCohortTarget(
        index_city="miami", expected_location="Miami",
        forecast_latitude=25.7617, forecast_longitude=-80.1918,
        forecast_reference_note="forecast-grid only",
    )


def sample(ticker, target_time):
    return SimpleNamespace(
        ticker=ticker, parsed=SimpleNamespace(observation_time_utc=target_time),
        rules=SimpleNamespace(market_rules=SimpleNamespace(raw_market={"ticker": ticker})),
        series_ticker="KXTEMPMIAH",
    )


def test_low_memory_mode_captures_one_h0_core_only(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_LOW_MEMORY_MODE", "1")
    seen = {"captured": [], "closed": False}
    class FakeHttp:
        def close(self):
            seen["closed"] = True
    monkeypatch.setattr(bounded, "ReadOnlyJsonClient", FakeHttp)
    monkeypatch.setattr(bounded, "_existing_sample_keys", lambda _db: set())
    contracts = (
        sample("MIA-A", "2026-11-01T14:15:00Z"),
        sample("MIA-B", "2026-11-01T14:45:00Z"),
        sample("MIA-C", "2026-11-01T15:30:00Z"),
    )
    monkeypatch.setattr(
        bounded, "_discover_target_contracts",
        lambda _http, _target, _now: contracts,
    )
    def forbidden(**_kw):
        raise AssertionError("low-memory mode must not execute side feeds")
    monkeypatch.setattr(bounded, "capture_market_microstructure_batch", forbidden)
    monkeypatch.setattr(bounded, "capture_hourly_supplemental_sources", forbidden)
    def capture(**kwargs):
        seen["captured"].append(kwargs["ticker"])
        return {"prediction_id": "p1", "rule_snapshot_id": "r1"}
    monkeypatch.setattr(bounded, "capture_hourly_shadow", capture)
    result = bounded.run_bounded_capture_only_cohort_once(
        db_client_fn=lambda: object(), targets=(target(),),
        now="2026-11-01T14:00:00Z",
    )
    assert seen["captured"] == ["MIA-A"]
    assert seen["closed"]
    assert result.samples_captured == 1
    assert result.market_microstructure_snapshots_captured == 0
    assert result.supplemental_snapshots_captured == 0
    assert result.predictions_settled == 0
    assert result.probability_publishable is False
    assert result.can_execute is False


def test_low_memory_mode_excludes_non_h0_even_without_current_sample(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_LOW_MEMORY_MODE", "1")
    class FakeHttp:
        def close(self):
            pass
    monkeypatch.setattr(bounded, "ReadOnlyJsonClient", FakeHttp)
    monkeypatch.setattr(bounded, "_existing_sample_keys", lambda _db: set())
    monkeypatch.setattr(
        bounded, "_discover_target_contracts",
        lambda _http, _target, _now: (sample("MIA-H1", "2026-11-01T15:30:00Z"),),
    )
    monkeypatch.setattr(
        bounded, "capture_hourly_shadow",
        lambda **kw: (_ for _ in ()).throw(AssertionError("unexpected H1 capture")),
    )
    monkeypatch.setattr(
        bounded, "capture_market_microstructure_batch",
        lambda **kw: (_ for _ in ()).throw(AssertionError("unexpected quote capture")),
    )
    result = bounded.run_bounded_capture_only_cohort_once(
        db_client_fn=lambda: object(), targets=(target(),),
        now="2026-11-01T14:00:00Z",
    )
    assert result.samples_captured == 0
    assert result.predictions_settled == 0


def test_low_memory_suppresses_settlement_even_when_owner_flag_enabled(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_LOW_MEMORY_MODE", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_SETTLEMENT_ENABLED", "1")
    expected = CohortRunResult(
        status="EMPIRICAL_SHADOW_COHORT_PASS", targets_checked=1,
        contracts_discovered=1, samples_captured=1, samples_skipped_existing=0,
        supplemental_snapshots_captured=0, predictions_settled=0,
        capture_failures=(), supplemental_failures=(), settlement_failures=(),
    )
    monkeypatch.setattr(runtime, "run_bounded_capture_only_cohort_once", lambda **kw: expected)
    monkeypatch.setattr(runtime, "_scheduled_shadow_targets", lambda: (target(),))
    monkeypatch.setattr(
        runtime, "_settle_ready_predictions",
        lambda *args, **kw: (_ for _ in ()).throw(AssertionError("settlement must be isolated")),
    )
    assert runtime._run_bounded_shadow_cycle(db_client_fn=lambda: object()) == expected


def test_default_mode_remains_unchanged_when_low_memory_flag_absent(monkeypatch):
    monkeypatch.delenv("WOW_KALSHI_WEATHER_EMPIRICAL_LOW_MEMORY_MODE", raising=False)
    seen = {"recorder": 0}
    class FakeHttp:
        def close(self):
            pass
    monkeypatch.setattr(bounded, "ReadOnlyJsonClient", FakeHttp)
    monkeypatch.setattr(bounded, "_existing_sample_keys", lambda _db: set())
    monkeypatch.setattr(
        bounded, "_discover_target_contracts",
        lambda _http, _target, _now: (sample("MIA-A", "2026-11-01T14:15:00Z"),),
    )
    def recorder(**kw):
        seen["recorder"] += 1
        return SimpleNamespace(written=1, failures=(), attempted=1)
    monkeypatch.setattr(bounded, "capture_market_microstructure_batch", recorder)
    monkeypatch.setattr(
        bounded, "capture_hourly_shadow",
        lambda **kw: {"prediction_id": "p1", "rule_snapshot_id": "r1"},
    )
    monkeypatch.setattr(
        bounded, "capture_hourly_supplemental_sources",
        lambda **kw: {"source_snapshot_ids": ["s1"], "failures": []},
    )
    result = bounded.run_bounded_capture_only_cohort_once(
        db_client_fn=lambda: object(), targets=(target(),),
        now="2026-11-01T14:00:00Z",
    )
    assert seen["recorder"] == 1
    assert result.market_microstructure_snapshots_captured == 1
    assert result.supplemental_snapshots_captured == 1
    assert result.can_execute is False
