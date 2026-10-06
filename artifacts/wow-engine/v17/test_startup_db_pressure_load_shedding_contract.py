from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / "v17" / "daily_async_runtime.py"
PICKEM = ROOT / "v17" / "nfl_pickem_async_runtime.py"
NCAAF_API = ROOT / "api_ncaaf_acceptance.py"
RUNTIME_ACCEPTANCE = ROOT / "v17" / "runtime_acceptance_probe.py"
V17_INIT = ROOT / "v17" / "__init__.py"
RUNDOWN = ROOT / "v17" / "rundown_market_startup_bootstrap.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_idle_durable_workers_no_longer_poll_supabase_every_two_seconds():
    daily = _text(DAILY)
    pickem = _text(PICKEM)

    assert 'DEFAULT_IDLE_POLL_SECONDS = 30' in daily
    assert 'DEFAULT_IDLE_POLL_SECONDS = 30' in pickem
    assert 'DEFAULT_DB_FAILURE_BACKOFF_MAX_SECONDS = 120' in daily
    assert 'DEFAULT_DB_FAILURE_BACKOFF_MAX_SECONDS = 120' in pickem
    assert 'failure_streak += 1' in daily
    assert 'failure_streak += 1' in pickem
    assert 'timeout=wait_seconds' in daily
    assert 'timeout=wait_seconds' in pickem
    assert 'loop.call_soon(wake.set)' in daily
    assert 'loop.call_soon(wake.set)' in pickem
    assert 'can_execute=false' in daily.lower()
    assert 'can_execute=false' in pickem.lower()


def test_noncritical_startup_db_consumers_run_after_spread_warm_window():
    ncaaf = _text(NCAAF_API)
    runtime = _text(RUNTIME_ACCEPTANCE)
    init = _text(V17_INIT)
    rundown = _text(RUNDOWN)

    assert 'WOW_NCAAF_SPREAD_WARM_STARTUP_DELAY_SECONDS", "60"' in ncaaf
    assert 'WOW_NCAAF_READINESS_STARTUP_DELAY_SECONDS", "180"' in ncaaf
    assert 'WOW_V17_SYNTHETIC_ACCEPTANCE_DELAY_SECONDS", "240"' in ncaaf
    assert 'WOW_V17_RUNTIME_ACCEPTANCE_INITIAL_DELAY_SECONDS", "180"' in runtime
    assert 'WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE_DELAY_SECONDS", "210"' in init
    assert '"WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE_MAX_MEMORY_RATIO",' in init
    assert '0.80,' in init
    assert '"WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE_MEMORY_RETRY_SECONDS",' in init
    assert '"WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE_MEMORY_RETRY_COUNT",' in init
    assert 'status=SKIPPED code=MEMORY_PRESSURE' in init
    assert 'WOW_RUNDOWN_MARKET_BOOTSTRAP_INITIAL_DELAY_SECONDS", "240"' in rundown

    assert 'global_terminal_authority=V17_TERMINAL_REDUCER' in ncaaf
    assert '"global_terminal_authority": "V17_TERMINAL_REDUCER"' in runtime
    assert 'can_execute=false' in ncaaf.lower()
    assert 'can_execute=false' in runtime.lower()
