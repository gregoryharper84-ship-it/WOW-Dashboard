from types import SimpleNamespace

from v17 import rundown_market_startup_bootstrap as bootstrap


class _Result:
    def __init__(self, data=None):
        self.data = data or []


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _Result(self.rows)


class _Client:
    def __init__(self, rows=None):
        self.rows = rows or []

    def table(self, _name):
        return _Query(self.rows)


def _env(monkeypatch, *, mode, token="boot-1"):
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_STARTUP_MODE", mode)
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_TOKEN", token)


def test_off_mode_is_disabled(monkeypatch):
    _env(monkeypatch, mode="OFF")
    out = bootstrap.run_bootstrap(_Client())
    assert out["status"] == "DISABLED"
    assert out["can_execute"] is False


def test_completed_marker_prevents_repeat(monkeypatch):
    _env(monkeypatch, mode="CATALOGS_ONLY")
    out = bootstrap.run_bootstrap(_Client(rows=[{"feed_key": "x", "last_success_at": "2026-09-23T00:00:00Z"}]))
    assert out["status"] == "ALREADY_COMPLETE"
    assert out["can_execute"] is False


def test_catalog_bootstrap_persists_marker_without_probability_authority(monkeypatch):
    _env(monkeypatch, mode="CATALOGS_ONLY")
    monkeypatch.setattr(bootstrap, "_already_completed", lambda *_args: False)
    monkeypatch.setattr(
        bootstrap.ingestor,
        "refresh_catalogs",
        lambda _client: {
            "status": "COMPLETE",
            "rows_written": {"SPORT": 12, "MARKET": 44, "AFFILIATE": 8},
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    seen = {}
    monkeypatch.setattr(
        bootstrap.ledger,
        "persist_sync_state",
        lambda _client, state: seen.update(state),
    )

    out = bootstrap.run_bootstrap(_Client())
    assert out["status"] == "COMPLETE"
    assert out["rows_written"] == 64
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False
    assert seen["feed_key"].startswith("RUNDOWN:BOOTSTRAP:CATALOGS_ONLY:")
    assert seen["can_execute"] is False if "can_execute" in seen else True


def test_snapshot_bootstrap_requires_explicit_filters(monkeypatch):
    _env(monkeypatch, mode="SNAPSHOT_ONCE")
    monkeypatch.setattr(bootstrap, "_already_completed", lambda *_args: False)
    out = bootstrap.run_bootstrap(_Client())
    assert out["status"] == "BLOCKED"
    assert out["reason_code"] == "RUNDOWN_BOOTSTRAP_FILTERS_REQUIRED"
    assert out["can_execute"] is False


def test_snapshot_bootstrap_calls_exactly_one_filtered_collection(monkeypatch):
    _env(monkeypatch, mode="SNAPSHOT_ONCE")
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_SPORT_KEY", "baseball_mlb")
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_SLATE_DATE", "2026-09-22")
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_MARKET_IDS", "1")
    monkeypatch.setenv("WOW_RUNDOWN_MARKET_BOOTSTRAP_AFFILIATE_IDS", "3,7")
    monkeypatch.setattr(bootstrap, "_already_completed", lambda *_args: False)
    calls = []

    def collect(_client, **kwargs):
        calls.append(kwargs)
        return {
            "status": "COMPLETE",
            "rows_written": 18,
            "events": 3,
            "datapoints": 36,
            "data_delay_seconds": 60,
            "delta_eligible": False,
            "next_acquisition_mode": "SNAPSHOT",
            "prediction_authority": False,
            "can_execute": False,
        }

    monkeypatch.setattr(bootstrap.ingestor, "collect_snapshot", collect)
    monkeypatch.setattr(bootstrap.ledger, "persist_sync_state", lambda *_args, **_kwargs: None)

    out = bootstrap.run_bootstrap(_Client())
    assert len(calls) == 1
    assert calls[0]["sport_key"] == "baseball_mlb"
    assert calls[0]["slate_date"] == "2026-09-22"
    assert calls[0]["market_ids"] == ("1",)
    assert calls[0]["affiliate_ids"] == ("3", "7")
    assert calls[0]["include_all_periods"] is False
    assert out["next_acquisition_mode"] == "SNAPSHOT"
    assert out["can_execute"] is False


def test_installer_is_inert_by_default(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_MARKET_STARTUP_MODE", raising=False)
    app = SimpleNamespace(state=SimpleNamespace())
    assert bootstrap.install_rundown_market_startup_bootstrap(app, db_client_fn=lambda: object()) is False
