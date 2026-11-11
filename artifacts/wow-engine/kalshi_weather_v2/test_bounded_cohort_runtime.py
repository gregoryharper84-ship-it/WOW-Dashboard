from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import kalshi_weather_v2.bounded_cohort as bounded
import kalshi_weather_v2.empirical_runtime as runtime
import kalshi_weather_v2.shadow_cohort as shadow
from kalshi_weather_v2.http_client import HttpAcquisitionError
from kalshi_weather_v2.shadow_cohort import HourlyCohortTarget


def _target(name: str = "miami") -> HourlyCohortTarget:
    return HourlyCohortTarget(
        index_city=name,
        expected_location=name.title(),
        forecast_latitude=25.0,
        forecast_longitude=-80.0,
        forecast_reference_note="test only",
    )


def test_capture_only_pass_does_not_settle_backlog(monkeypatch):
    closed = {"value": False}

    class FakeHttp:
        def close(self):
            closed["value"] = True

    monkeypatch.setattr(bounded, "ReadOnlyJsonClient", FakeHttp)
    monkeypatch.setattr(bounded, "_existing_sample_keys", lambda _db: set())
    monkeypatch.setattr(
        bounded,
        "_discover_target_contracts",
        lambda _http, _target, _decision_time: (),
    )

    result = bounded.run_bounded_capture_only_cohort_once(
        db_client_fn=lambda: SimpleNamespace(),
        targets=(_target(),),
        now="2026-09-14T14:00:00Z",
    )

    assert result.status == "EMPIRICAL_SHADOW_COHORT_PASS"
    assert result.targets_checked == 1
    assert result.contracts_discovered == 0
    assert result.predictions_settled == 0
    assert result.settlement_failures == ()
    assert closed["value"] is True


def test_scheduler_defaults_to_one_rotating_target(monkeypatch):
    monkeypatch.delenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_TARGETS", raising=False)
    selected = runtime._scheduled_shadow_targets()
    all_targets = runtime._automated_shadow_targets()

    assert len(all_targets) == 3
    assert len(selected) == 1
    assert selected[0] in all_targets


def test_scheduler_can_expand_target_slice_explicitly(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_TARGETS", "3")
    selected = runtime._scheduled_shadow_targets()

    assert len(selected) == 3
    assert {target.index_city for target in selected} == {
        "miami",
        "chicago",
        "la-coastal",
    }


def test_empirical_loop_staggers_first_cycle(monkeypatch):
    cycles = []
    sleeps = []

    def fake_cycle(*, db_client_fn):
        cycles.append(db_client_fn())
        raise AssertionError("empirical cycle must not run before startup delay")

    async def stop_on_initial_delay(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "_run_bounded_shadow_cycle", fake_cycle)
    monkeypatch.setattr(runtime.asyncio, "sleep", stop_on_initial_delay)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await runtime._run_bounded_shadow_loop(
                db_client_fn=lambda: "db-client",
                interval_seconds=900,
                initial_delay_seconds=45,
            )

    asyncio.run(exercise())
    assert sleeps == [45.0]
    assert cycles == []


def test_market_recorder_captures_all_siblings_even_when_calibration_sample_exists(monkeypatch):
    target = _target()
    target_time = "2026-09-14T15:00:00Z"
    contracts = (
        SimpleNamespace(
            ticker="KXTEMPMIAH-A",
            parsed=SimpleNamespace(observation_time_utc=target_time),
            rules=SimpleNamespace(market_rules=SimpleNamespace(raw_market={"ticker": "KXTEMPMIAH-A"})),
            series_ticker="KXTEMPMIAH",
        ),
        SimpleNamespace(
            ticker="KXTEMPMIAH-B",
            parsed=SimpleNamespace(observation_time_utc=target_time),
            rules=SimpleNamespace(market_rules=SimpleNamespace(raw_market={"ticker": "KXTEMPMIAH-B"})),
            series_ticker="KXTEMPMIAH",
        ),
    )
    seen = {}

    class FakeHttp:
        def close(self):
            pass

    monkeypatch.setattr(bounded, "ReadOnlyJsonClient", FakeHttp)
    monkeypatch.setattr(
        bounded,
        "_discover_target_contracts",
        lambda _http, _target, _decision_time: contracts,
    )
    existing = bounded.cohort_sample_key(target.index_city, target_time, "H1")
    monkeypatch.setattr(bounded, "_existing_sample_keys", lambda _db: {existing})

    def fake_recorder(**kwargs):
        seen["tickers"] = kwargs["tickers"]
        seen["series_by_ticker"] = kwargs["series_by_ticker"]
        seen["market_by_ticker"] = kwargs["market_by_ticker"]
        return SimpleNamespace(attempted=2, written=2, failures=())

    monkeypatch.setattr(bounded, "capture_market_microstructure_batch", fake_recorder)

    def should_not_capture_probability(**_kwargs):
        raise AssertionError("existing calibration sample should skip weather prediction capture")

    monkeypatch.setattr(bounded, "capture_hourly_shadow", should_not_capture_probability)

    result = bounded.run_bounded_capture_only_cohort_once(
        db_client_fn=lambda: SimpleNamespace(),
        targets=(target,),
        now="2026-09-14T14:00:00Z",
    )

    assert seen["tickers"] == ("KXTEMPMIAH-A", "KXTEMPMIAH-B")
    assert seen["series_by_ticker"] == {
        "KXTEMPMIAH-A": "KXTEMPMIAH",
        "KXTEMPMIAH-B": "KXTEMPMIAH",
    }
    assert seen["market_by_ticker"] == {
        "KXTEMPMIAH-A": {"ticker": "KXTEMPMIAH-A"},
        "KXTEMPMIAH-B": {"ticker": "KXTEMPMIAH-B"},
    }
    assert result.samples_captured == 0
    assert result.samples_skipped_existing == 1
    assert result.market_microstructure_snapshots_captured == 2
    assert result.market_microstructure_failures == ()


def test_http_failure_context_preserves_provider_and_path_without_query_string():
    exc = HttpAcquisitionError(
        "HTTP_RETRY_EXHAUSTED",
        "https://api.example.test/v1/data?token=secret&city=chi",
        "http_status=429",
    )
    context = bounded._failure_context(exc)
    assert context == "provider=api.example.test,path=/v1/data,detail=http_status=429"
    assert "secret" not in context
    assert "city=" not in context



def test_weather_shadow_detects_observed_high_memory_pressure(monkeypatch):
    values = {
        "/sys/fs/cgroup/memory.current": "509849600",
        "/sys/fs/cgroup/memory.max": "536870900",
    }

    class FakePath:
        def __init__(self, path):
            self.path = path

        def read_text(self, encoding="utf-8"):
            if self.path in values:
                return values[self.path]
            raise OSError(self.path)

    monkeypatch.setattr(runtime, "Path", FakePath)
    monkeypatch.delenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_MEMORY_RATIO", raising=False)

    under_pressure, ratio = runtime._weather_memory_pressure()

    assert under_pressure is True
    assert ratio is not None
    assert ratio > 0.94


def test_weather_shadow_loop_skips_cycle_under_memory_pressure(monkeypatch):
    cycles = []
    sleeps = []

    monkeypatch.setattr(runtime, "_weather_memory_pressure", lambda: (True, 0.95))

    def fake_cycle(*, db_client_fn):
        cycles.append(db_client_fn())
        raise AssertionError("shadow cycle must shed load under memory pressure")

    async def stop_after_shed(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "_run_bounded_shadow_cycle", fake_cycle)
    monkeypatch.setattr(runtime.asyncio, "sleep", stop_after_shed)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await runtime._run_bounded_shadow_loop(
                db_client_fn=lambda: "db-client",
                interval_seconds=900,
                initial_delay_seconds=0,
            )

    asyncio.run(exercise())
    assert cycles == []
    assert sleeps == [900]


def _empty_cohort_result():
    return shadow.CohortRunResult(
        status="EMPIRICAL_SHADOW_COHORT_PASS",
        targets_checked=1,
        contracts_discovered=1,
        samples_captured=1,
        samples_skipped_existing=0,
        supplemental_snapshots_captured=1,
        predictions_settled=0,
        capture_failures=(),
        supplemental_failures=(),
        settlement_failures=(),
    )


def test_explicit_station_focus_keeps_one_verified_index(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_TARGET_CITY", "miami")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_TARGETS", "3")
    chosen = runtime._scheduled_shadow_targets()
    assert len(chosen) == 1
    assert chosen[0].index_city == "miami"


def test_unknown_explicit_station_fails_closed(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_TARGET_CITY", "invalid-station")
    assert runtime._scheduled_shadow_targets() == ()


def test_settlement_opt_in_needs_isolated_owner(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_SETTLEMENT_ENABLED", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER", "0")
    monkeypatch.setattr(runtime, "run_bounded_capture_only_cohort_once", lambda **_kw: _empty_cohort_result())
    monkeypatch.setattr(runtime, "_settle_ready_predictions", lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("unauthorized settlement")))
    result = runtime._run_bounded_shadow_cycle(db_client_fn=lambda: object())
    assert result.samples_captured == 1
    assert result.predictions_settled == 0
    assert result.can_execute is False


def test_settlement_disabled_even_with_owner(monkeypatch):
    monkeypatch.delenv("WOW_KALSHI_WEATHER_ISOLATED_SETTLEMENT_ENABLED", raising=False)
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER", "1")
    monkeypatch.setattr(runtime, "run_bounded_capture_only_cohort_once", lambda **_kw: _empty_cohort_result())
    monkeypatch.setattr(runtime, "_settle_ready_predictions", lambda *_args, **_kw: (_ for _ in ()).throw(AssertionError("settlement not enabled")))
    assert runtime._run_bounded_shadow_cycle(db_client_fn=lambda: object()).predictions_settled == 0


def test_singleton_settlement_is_bounded_and_keeps_publishing_off(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_SETTLEMENT_ENABLED", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER", "1")
    monkeypatch.setattr(runtime, "run_bounded_capture_only_cohort_once", lambda **_kw: _empty_cohort_result())
    seen = {}
    class FakeHttp:
        def close(self):
            seen["closed"] = True
    monkeypatch.setattr(runtime, "ReadOnlyJsonClient", FakeHttp)
    def fake_settle(db, http, now, failures, *, max_attempts):
        seen["limit"] = max_attempts
        seen["timestamp"] = now
        seen["db"] = db
        return 1
    monkeypatch.setattr(runtime, "_settle_ready_predictions", fake_settle)
    db = object()
    result = runtime._run_bounded_shadow_cycle(db_client_fn=lambda: db)
    assert seen["limit"] == 2
    assert seen["db"] is db
    assert seen["closed"] is True
    assert seen["timestamp"].endswith("Z")
    assert result.predictions_settled == 1
    assert result.settlement_failures == ()
    assert result.probability_publishable is False
    assert result.can_execute is False


def test_settlement_attempt_limit_does_not_sweep_backlog(monkeypatch):
    now = "2026-09-14T16:00:00Z"
    predictions = [
        {
            "prediction_id": f"pred-{i}",
            "model_payload": {"contract": {
                "lane": "HOURLY_TEMPERATURE",
                "observation_window": "POINT_IN_TIME:2026-09-14T14:00:00Z",
                "settlement_location_code": "KALSHI_WEATHER_INDEX:miami",
            }},
        }
        for i in range(4)
    ]
    monkeypatch.setattr(
        shadow, "_select_rows",
        lambda _client, table, *, limit: predictions if table.endswith("predictions") else [],
    )
    settled = []
    monkeypatch.setattr(
        shadow, "settle_hourly_prediction",
        lambda **kwargs: settled.append(kwargs["prediction_id"]),
    )
    failures = []
    count = shadow._settle_ready_predictions(
        object(), object(), now, failures, max_attempts=2,
    )
    assert count == 2
    assert settled == ["pred-0", "pred-1"]
    assert failures == []
