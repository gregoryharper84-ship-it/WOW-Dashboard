"""Runtime contract tests for durable Kalshi Weather dispatch consumption."""
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI

from kalshi_weather_v2 import empirical_runtime
from kalshi_weather_v2.isolated_capture_wiring import install_isolated_capture_if_owned


def test_claim_next_dispatch_uses_service_role_rpc_and_validates_single_row():
    """Claim exactly one well-formed row through the narrow RPC."""
    class Rpc:
        def execute(self):
            return SimpleNamespace(
                data=[{"dispatch_id": 17, "scheduled_slot": "2026-11-08T14:30:00Z", "attempts": 1}]
            )

    class Client:
        def rpc(self, name):
            assert name == "claim_wow_kalshi_weather_collection_dispatch"
            return Rpc()

    assert empirical_runtime._claim_next_dispatch(lambda: Client()) == {
        "dispatch_id": 17,
        "scheduled_slot": "2026-11-08T14:30:00Z",
        "attempts": 1,
    }


def test_claim_next_dispatch_returns_none_for_empty_queue():
    """An empty durable queue is a normal idle poll."""
    class Rpc:
        def execute(self):
            return SimpleNamespace(data=[])

    class Client:
        def rpc(self, _name):
            return Rpc()

    assert empirical_runtime._claim_next_dispatch(lambda: Client()) is None


def test_claim_next_dispatch_rejects_multiple_or_malformed_rows():
    """The RPC contract must never silently process an ambiguous claim."""
    class Rpc:
        def execute(self):
            return SimpleNamespace(data=[{"dispatch_id": 1}, {"dispatch_id": 2}])

    class Client:
        def rpc(self, _name):
            return Rpc()

    try:
        empirical_runtime._claim_next_dispatch(lambda: Client())
    except RuntimeError as exc:
        assert str(exc) == "KALSHI_WEATHER_DISPATCH_CLAIM_INVALID"
    else:
        raise AssertionError("multiple claimed rows must fail closed")


def test_finish_dispatch_uses_claimed_status_compare_and_set():
    """A worker can only finish the row it currently owns."""
    captured = {}

    class Query:
        def update(self, payload):
            captured["payload"] = payload
            return self

        def eq(self, key, value):
            captured.setdefault("filters", []).append((key, value))
            return self

        def select(self, fields):
            captured["select"] = fields
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": 17}])

    class Client:
        def table(self, name):
            captured["table"] = name
            return Query()

    empirical_runtime._finish_dispatch(
        lambda: Client(),
        17,
        "COMPLETED",
        result={"status": "SHADOW", "probability_publishable": False, "can_execute": False},
    )

    assert captured["table"] == "wow_kalshi_weather_collection_dispatch_queue"
    assert captured["filters"] == [("id", 17), ("status", "CLAIMED")]
    assert captured["select"] == "id"
    assert captured["payload"]["status"] == "COMPLETED"
    assert captured["payload"]["result"]["probability_publishable"] is False
    assert captured["payload"]["result"]["can_execute"] is False


def test_dispatch_result_summary_persists_counts_not_provider_payloads():
    """Queue evidence is compact and excludes raw provider failure strings."""
    result = SimpleNamespace(
        status="SHADOW",
        targets_checked=1,
        contracts_discovered=4,
        samples_captured=2,
        samples_skipped_existing=1,
        supplemental_snapshots_captured=3,
        market_microstructure_snapshots_captured=2,
        predictions_settled=0,
        capture_failures=("SECRET_URL_AND_PROVIDER_BODY",),
        supplemental_failures=("RAW_TOKEN_MUST_NOT_PERSIST",),
        market_microstructure_failures=(),
        settlement_failures=(),
    )
    summary = empirical_runtime._dispatch_result_summary(result)
    assert summary["capture_failure_count"] == 1
    assert summary["supplemental_failure_count"] == 1
    assert summary["samples_captured"] == 2
    assert summary["probability_publishable"] is False
    assert summary["can_execute"] is False
    assert "SECRET_URL_AND_PROVIDER_BODY" not in repr(summary)
    assert "RAW_TOKEN_MUST_NOT_PERSIST" not in repr(summary)


def test_process_dispatch_once_completes_one_capture_pass(monkeypatch):
    """A claimed dispatch runs the bounded cycle and records only its summary."""
    finished = []
    result = SimpleNamespace(
        status="SHADOW",
        targets_checked=1,
        contracts_discovered=2,
        samples_captured=1,
        samples_skipped_existing=0,
        supplemental_snapshots_captured=0,
        market_microstructure_snapshots_captured=0,
        predictions_settled=0,
        capture_failures=(),
        supplemental_failures=(),
        market_microstructure_failures=(),
        settlement_failures=(),
    )
    monkeypatch.setattr(
        empirical_runtime,
        "_claim_next_dispatch",
        lambda _client_fn: {"dispatch_id": 19, "attempts": 1},
    )
    monkeypatch.setattr(empirical_runtime, "_weather_memory_pressure", lambda: (False, 0.2))
    monkeypatch.setattr(
        empirical_runtime,
        "_run_bounded_shadow_cycle",
        lambda *, db_client_fn: result,
    )
    monkeypatch.setattr(
        empirical_runtime,
        "_finish_dispatch",
        lambda *args, **kwargs: finished.append((args, kwargs)),
    )

    assert asyncio.run(empirical_runtime._process_dispatch_once(lambda: None)) is True
    assert len(finished) == 1
    assert finished[0][0][1:3] == (19, "COMPLETED")
    assert finished[0][1]["result"]["samples_captured"] == 1


def test_memory_pressure_requeues_without_consuming_an_attempt(monkeypatch):
    """Memory pressure defers a dispatch instead of treating it as a failure."""
    finished = []
    monkeypatch.setattr(
        empirical_runtime,
        "_claim_next_dispatch",
        lambda _client_fn: {"dispatch_id": 23, "attempts": 1},
    )
    monkeypatch.setattr(empirical_runtime, "_weather_memory_pressure", lambda: (True, 0.93))
    monkeypatch.setattr(
        empirical_runtime,
        "_finish_dispatch",
        lambda *args, **kwargs: finished.append((args, kwargs)),
    )
    monkeypatch.setattr(
        empirical_runtime,
        "_run_bounded_shadow_cycle",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not collect under memory pressure")),
    )

    assert asyncio.run(empirical_runtime._process_dispatch_once(lambda: None)) is True
    assert finished[0][0][1:3] == (23, "PENDING")
    assert finished[0][1]["attempts"] == 0
    assert finished[0][1]["error_code"] == "MEMORY_PRESSURE"


def test_dispatch_queue_and_legacy_loop_cannot_both_be_enabled(monkeypatch):
    """Mutually exclusive scheduler flags fail closed before installing a writer."""
    app = FastAPI()
    called = []
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED", "1")

    result = install_isolated_capture_if_owned(
        app,
        db_client_fn=lambda: None,
        installer=lambda *_args, **_kwargs: called.append(True),
    )
    assert result["status"] == "MULTIPLE_SCHEDULERS_BLOCKED"
    assert result["dispatch_queue_worker_installed"] is False
    assert called == []


def test_isolated_wiring_reports_queue_consumer_without_http_trigger(monkeypatch):
    """Ownership wiring reports the durable consumer, not a credentialed route."""
    app = FastAPI()
    called = []
    monkeypatch.setenv("WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED", "1")
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED", "0")

    result = install_isolated_capture_if_owned(
        app,
        db_client_fn=lambda: None,
        installer=lambda *_args, **_kwargs: called.append(True),
    )
    assert called == [True]
    assert result["dispatch_queue_worker_installed"] is True
    assert result["dispatch_queue_enabled"] is True
    assert result["trigger_installed"] is False
    assert result["can_execute"] is False
