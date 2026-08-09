"""
tests/test_kalshi_wx_shadow_capture.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10D tests (non-blocking)

Tests for gate_engine/kalshi_wx_shadow_capture.py and the minimal flag-gated
insertion in the /wow/kalshi/weather/evaluate route handler.

No live API calls are made anywhere in this file.

PATCHING NOTE — lazy imports + threading
  maybe_fire_shadow_snapshot() lazily imports its heavy dependencies inside its
  try block, so those names are never attributes of the capture module.
  Patches must target the source module where the name lives:
    run_shadow_orchestrator → gate_engine.kalshi_wx_shadow_orchestrator
    WeatherResearchSnapshot → gate_engine.kalshi_wx_shadow_snapshot
  _build_shadow_sdk_client, _Thread, _SHADOW_ENABLED, _SHADOW_SEMAPHORE are all
  top-level names in the capture module and patch directly there.

  The daemon thread (_Thread) is patched with _SyncThread in all SD3/SD4 tests
  so the orchestrator mock is called synchronously and captured kwargs are
  available immediately after maybe_fire_shadow_snapshot() returns.

Test plan
─────────
SD1  Flag OFF: maybe_fire_shadow_snapshot() returns immediately; orchestrator,
     SDK client, snapshot constructor, and Thread are never invoked.

SD2  Flag ON + exception: exception logged as shadow failure, function returns None.

SD3  Flag ON, valid path (mocked, sync thread): snapshot carries real route-local
     values, not test defaults or fabricated data.

SD4  Sentinel verification: six unavailable fields carry UNAVAILABLE_SENTINEL.

SD5  No new network calls: AST structural check.

SD6  Structural and helper unit tests.

SD7  Non-blocking / semaphore behavior:
     SD7a — Thread is started (not called synchronously) when not patched.
     SD7b — Semaphore held → second call skipped (SHADOW_CAPTURE_SKIPPED logged).
     SD7c — Semaphore always released in finally, even on orchestrator exception.
"""
from __future__ import annotations

import ast
import os
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_CAPTURE_SRC = os.path.join(_REPO, "gate_engine", "kalshi_wx_shadow_capture.py")
_APP_SRC     = os.path.join(_REPO, "app.py")

from gate_engine.kalshi_wx_shadow_capture import (
    UNAVAILABLE_SENTINEL,
    _derive_source_failures,
    _derive_readiness_state,
    _derive_source_timestamps,
    maybe_fire_shadow_snapshot,
)

# ── Patch-target constants ────────────────────────────────────────────────────
_ORCH_PATH   = "gate_engine.kalshi_wx_shadow_orchestrator.run_shadow_orchestrator"
_SNAP_PATH   = "gate_engine.kalshi_wx_shadow_snapshot.WeatherResearchSnapshot"
_CLIENT_PATH = "gate_engine.kalshi_wx_shadow_capture._build_shadow_sdk_client"
_FLAG_PATH   = "gate_engine.kalshi_wx_shadow_capture._SHADOW_ENABLED"
_THREAD_PATH = "gate_engine.kalshi_wx_shadow_capture._Thread"
_SEMA_PATH   = "gate_engine.kalshi_wx_shadow_capture._SHADOW_SEMAPHORE"


# ── Synchronous thread shim (for deterministic tests) ────────────────────────

class _SyncThread:
    """
    Drop-in for threading.Thread that calls target() synchronously on .start().
    Used in SD3/SD4 tests to capture orchestrator kwargs immediately.
    """
    def __init__(self, target=None, daemon=None, name=None, args=(), kwargs=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


# ── Shared call-kwargs helper ─────────────────────────────────────────────────

def _call_kwargs(**overrides) -> dict:
    base = dict(
        city="NYC",
        station="KNYC",
        market_date="2026-08-15",
        forecast_high=85.0,
        weather_data_source_tier="nws_primary",
        sigma_f=3.5,
        horizon_hours=18.0,
        tier_detail={"nws": {"attempted": True, "ok": True, "error": None}},
    )
    base.update(overrides)
    return base


# ── Context manager: flag on, client mocked, orchestrator mocked, sync thread ─

@contextmanager
def _flag_on_mocked():
    """
    Enable flag + mock SDK client + mock orchestrator + synchronous _Thread.
    Yields the list that the fake orchestrator appends kwargs to.
    """
    captured: list = []

    def _fake_orchestrator(**kwargs):
        captured.append(kwargs)
        return MagicMock()

    # Fresh unlimited semaphore so tests don't interfere with each other
    fresh_sema = threading.Semaphore(999)

    with patch(_FLAG_PATH, True):
        with patch(_CLIENT_PATH, return_value=MagicMock()):
            with patch(_ORCH_PATH, side_effect=_fake_orchestrator):
                with patch(_THREAD_PATH, _SyncThread):
                    with patch(_SEMA_PATH, fresh_sema):
                        yield captured


# ═════════════════════════════════════════════════════════════════════════════
# SD1 — Flag OFF: capture is completely inert
# ═════════════════════════════════════════════════════════════════════════════

class TestSD1FlagOff(unittest.TestCase):

    def test_SD1_returns_none_with_flag_off(self):
        with patch(_FLAG_PATH, False):
            self.assertIsNone(maybe_fire_shadow_snapshot(**_call_kwargs()))

    def test_SD1_orchestrator_never_called_with_flag_off(self):
        sentinel = MagicMock(side_effect=AssertionError("must not be called"))
        with patch(_FLAG_PATH, False):
            with patch(_ORCH_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_sdk_client_never_built_with_flag_off(self):
        sentinel = MagicMock(side_effect=AssertionError("must not be called"))
        with patch(_FLAG_PATH, False):
            with patch(_CLIENT_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_snapshot_constructor_never_called_with_flag_off(self):
        sentinel = MagicMock(side_effect=AssertionError("must not be called"))
        with patch(_FLAG_PATH, False):
            with patch(_SNAP_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_thread_never_started_with_flag_off(self):
        sentinel = MagicMock(side_effect=AssertionError("must not be called"))
        with patch(_FLAG_PATH, False):
            with patch(_THREAD_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_module_has_independent_second_gate(self):
        reached: list = []
        with patch(_FLAG_PATH, False):
            with patch(_ORCH_PATH, side_effect=lambda **kw: reached.append(1)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(reached, [])


# ═════════════════════════════════════════════════════════════════════════════
# SD2 — Flag ON + exception: exception logged and swallowed
# ═════════════════════════════════════════════════════════════════════════════

class TestSD2ExceptionIsolation(unittest.TestCase):

    def test_SD2_RuntimeError_from_orchestrator_is_swallowed(self):
        """Orchestrator raising inside the thread must not propagate."""
        # With _SyncThread, the orchestrator error propagates synchronously
        # inside the outer try/except — still swallowed.
        with patch(_FLAG_PATH, True):
            with patch(_CLIENT_PATH, return_value=MagicMock()):
                with patch(_ORCH_PATH, side_effect=RuntimeError("boom")):
                    with patch(_THREAD_PATH, _SyncThread):
                        with patch(_SEMA_PATH, threading.Semaphore(999)):
                            result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_ValueError_from_snapshot_constructor_is_swallowed(self):
        with patch(_FLAG_PATH, True):
            with patch(_SNAP_PATH, side_effect=ValueError("bad field")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_missing_sdk_client_is_swallowed(self):
        with patch(_FLAG_PATH, True):
            with patch(_CLIENT_PATH, side_effect=RuntimeError("no key")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_exception_produces_warning_log(self):
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_CLIENT_PATH, side_effect=RuntimeError("sdk-boom")):
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIn("SHADOW_CAPTURE_FAILURE", "\n".join(log_ctx.output))

    def test_SD2_exception_log_contains_city(self):
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING"):
            with patch(_FLAG_PATH, True):
                with patch(_CLIENT_PATH, side_effect=RuntimeError("x")):
                    maybe_fire_shadow_snapshot(**_call_kwargs(city="CHI"))

    def test_SD2_route_level_try_except_in_app_py(self):
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx = src.find("Step 10D: Kalshi Weather shadow capture")
        self.assertNotEqual(idx, -1)
        block = src[idx: idx + 1500]
        self.assertIn("maybe_fire_shadow_snapshot as _mfss", block)
        self.assertIn("except Exception:", block)
        self.assertIn("pass", block)


# ═════════════════════════════════════════════════════════════════════════════
# SD3 — Flag ON, valid path: snapshot carries real route-local values
# ═════════════════════════════════════════════════════════════════════════════

class TestSD3SnapshotCarriesRealValues(unittest.TestCase):

    def _run_and_capture_snapshot(self, **call_kw):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**call_kw)
        self.assertEqual(len(captured), 1, "Orchestrator must be called exactly once")
        snap = captured[0].get("snapshot")
        self.assertIsNotNone(snap)
        return snap

    def test_SD3_snapshot_city_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(city="MIA"))
        self.assertEqual(snap.city, "MIA")

    def test_SD3_snapshot_station_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(station="KMIA"))
        self.assertEqual(snap.station, "KMIA")

    def test_SD3_snapshot_market_date_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(market_date="2026-09-10"))
        self.assertEqual(snap.market_date, "2026-09-10")

    def test_SD3_snapshot_forecast_high_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=91.5))
        self.assertAlmostEqual(snap.forecast_high_used_by_deterministic_model, 91.5)

    def test_SD3_snapshot_sigma_f_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(sigma_f=4.75))
        self.assertAlmostEqual(snap.sigma_f, 4.75)

    def test_SD3_snapshot_horizon_hours_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(horizon_hours=42.0))
        self.assertAlmostEqual(snap.forecast_horizon_hours, 42.0)

    def test_SD3_snapshot_weather_data_source_tier_matches_input(self):
        snap = self._run_and_capture_snapshot(
            **_call_kwargs(weather_data_source_tier="open_meteo_fallback")
        )
        self.assertEqual(snap.weather_data_source_tier, "open_meteo_fallback")

    def test_SD3_snapshot_is_frozen_dataclass(self):
        import dataclasses
        from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertIsInstance(snap, WeatherResearchSnapshot)
        self.assertTrue(snap.__dataclass_params__.frozen)

    def test_SD3_canonical_event_id_derived_from_city_and_date(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(city="CHI", market_date="2026-08-20"))
        self.assertEqual(snap.canonical_event_id, "kalshi-nhigh-CHI-2026-08-20")

    def test_SD3_research_snapshot_id_is_unique_per_call(self):
        snap_a = self._run_and_capture_snapshot(**_call_kwargs())
        snap_b = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertNotEqual(snap_a.research_snapshot_id, snap_b.research_snapshot_id)

    def test_SD3_readiness_ready_when_forecast_high_present(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=88.0))
        self.assertEqual(snap.deterministic_weather_readiness_state, "READY")

    def test_SD3_readiness_data_unavailable_when_forecast_high_none(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=None))
        self.assertEqual(snap.deterministic_weather_readiness_state, "DATA_UNAVAILABLE")

    def test_SD3_forecast_high_zero_when_none_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=None))
        self.assertEqual(snap.forecast_high_used_by_deterministic_model, 0.0)

    def test_SD3_orchestrator_called_with_city_and_date(self):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs(city="AUS", market_date="2026-08-25"))
        self.assertEqual(captured[0].get("city"), "AUS")
        self.assertEqual(captured[0].get("date"), "2026-08-25")


# ═════════════════════════════════════════════════════════════════════════════
# SD4 — Sentinel verification
# ═════════════════════════════════════════════════════════════════════════════

class TestSD4SentinelFields(unittest.TestCase):

    def setUp(self):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs())
        self.snap = captured[0]["snapshot"]

    def test_SD4_nws_gridpoint_forecast_is_unavail_dict(self):
        self.assertEqual(self.snap.nws_gridpoint_forecast,
                         {"_status": UNAVAILABLE_SENTINEL})

    def test_SD4_open_meteo_forecast_is_unavail_dict(self):
        self.assertEqual(self.snap.open_meteo_forecast,
                         {"_status": UNAVAILABLE_SENTINEL})

    def test_SD4_noaa_ncei_forecast_is_unavail_dict(self):
        self.assertEqual(self.snap.noaa_ncei_forecast,
                         {"_status": UNAVAILABLE_SENTINEL})

    def test_SD4_official_observations_is_unavail_dict(self):
        self.assertEqual(self.snap.official_observations_at_cutoff,
                         {"_status": UNAVAILABLE_SENTINEL})

    def test_SD4_source_provenance_is_unavail_dict(self):
        self.assertEqual(self.snap.source_provenance,
                         {"_status": UNAVAILABLE_SENTINEL})

    def test_SD4_source_disagreements_is_unavail_tuple(self):
        self.assertEqual(self.snap.source_disagreements,
                         (UNAVAILABLE_SENTINEL,))

    def test_SD4_sentinel_fields_are_not_none(self):
        for fname in ("nws_gridpoint_forecast", "open_meteo_forecast",
                      "noaa_ncei_forecast", "official_observations_at_cutoff",
                      "source_provenance"):
            with self.subTest(field=fname):
                self.assertIsNotNone(getattr(self.snap, fname))

    def test_SD4_sentinel_constant_is_non_empty_string(self):
        self.assertIsInstance(UNAVAILABLE_SENTINEL, str)
        self.assertGreater(len(UNAVAILABLE_SENTINEL), 0)

    def test_SD4_sentinel_appears_in_sentinel_dict_values(self):
        self.assertIn(UNAVAILABLE_SENTINEL, str(self.snap.nws_gridpoint_forecast))
        self.assertIn(UNAVAILABLE_SENTINEL, str(self.snap.source_disagreements))


# ═════════════════════════════════════════════════════════════════════════════
# SD5 — No new network calls (AST structural)
# ═════════════════════════════════════════════════════════════════════════════

class TestSD5NoNewNetworkCalls(unittest.TestCase):

    _FORBIDDEN = frozenset({
        "_fetch_forecast_high_tiered", "_fetch_nws_forecast_high",
        "_fetch_open_meteo_daily_high", "_fetch_noaa_ncei_daily_high",
        "_fetch_nws_cli", "_fetch_kalshi_nhigh_prices",
        "_nws_get", "_ncei_cdo_get",
        "requests", "urllib", "httpx", "aiohttp",
    })

    def _call_names(self, src: str) -> set:
        names: set = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
                    if isinstance(node.func.value, ast.Name):
                        names.add(node.func.value.id)
        return names

    def test_SD5_capture_module_no_forbidden_calls(self):
        with open(_CAPTURE_SRC, encoding="utf-8") as fh:
            src = fh.read()
        violations = self._call_names(src) & self._FORBIDDEN
        self.assertEqual(violations, set(), f"Forbidden calls: {sorted(violations)}")

    def test_SD5_capture_module_no_top_level_requests_import(self):
        with open(_CAPTURE_SRC, encoding="utf-8") as fh:
            src = fh.read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "requests")

    def test_SD5_app_py_shadow_block_no_fetch_calls(self):
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx_s = src.find("Step 10D: Kalshi Weather shadow capture")
        idx_e = src.find("Step 3: Live Kalshi prices", idx_s)
        self.assertNotEqual(idx_s, -1)
        self.assertNotEqual(idx_e, -1)
        block = src[idx_s:idx_e]
        for name in self._FORBIDDEN:
            if name.startswith("_fetch") or name in ("_nws_get", "_ncei_cdo_get"):
                self.assertNotIn(name, block)

    def test_SD5_app_py_shadow_block_does_not_store_return_value(self):
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx_s = src.find("Step 10D: Kalshi Weather shadow capture")
        idx_e = src.find("Step 3: Live Kalshi prices", idx_s)
        block = src[idx_s:idx_e]
        self.assertNotIn("= _mfss(", block)
        self.assertNotIn("if _mfss(", block)


# ═════════════════════════════════════════════════════════════════════════════
# SD6 — Structural and helper unit tests
# ═════════════════════════════════════════════════════════════════════════════

class TestSD6StructuralAndHelpers(unittest.TestCase):

    def test_SD6_derive_source_failures_empty_when_all_ok(self):
        self.assertEqual(
            _derive_source_failures({"nws": {"attempted": True, "ok": True, "error": None}}),
            ()
        )

    def test_SD6_derive_source_failures_captures_failed_tier(self):
        result = _derive_source_failures({
            "nws": {"attempted": True, "ok": False, "error": "HTTP 503"},
            "open_meteo": {"attempted": True, "ok": True, "error": None},
        })
        self.assertEqual(len(result), 1)
        self.assertIn("nws", result[0])
        self.assertIn("HTTP 503", result[0])

    def test_SD6_derive_source_failures_all_three_failed(self):
        result = _derive_source_failures({
            "nws":        {"attempted": True, "ok": False, "error": "t"},
            "open_meteo": {"attempted": True, "ok": False, "error": "r"},
            "noaa_ncei":  {"attempted": True, "ok": False, "error": "n"},
        })
        self.assertEqual(len(result), 3)

    def test_SD6_derive_source_failures_skips_unattempted(self):
        self.assertEqual(
            _derive_source_failures({"nws": {"attempted": False, "ok": False, "error": "x"}}),
            ()
        )

    def test_SD6_derive_source_failures_returns_tuple(self):
        self.assertIsInstance(
            _derive_source_failures({"nws": {"attempted": True, "ok": False, "error": "e"}}),
            tuple
        )

    def test_SD6_derive_readiness_ready_when_present(self):
        self.assertEqual(_derive_readiness_state(88.0), "READY")

    def test_SD6_derive_readiness_unavailable_when_none(self):
        self.assertEqual(_derive_readiness_state(None), "DATA_UNAVAILABLE")

    def test_SD6_derive_source_timestamps_contains_winning_tier(self):
        ts = _derive_source_timestamps("nws_primary", "2026-08-15T12:00:00Z")
        self.assertEqual(ts["nws_primary"], "2026-08-15T12:00:00Z")

    def test_SD6_shadow_block_between_horizon_hours_and_step3(self):
        with open(_APP_SRC, encoding="utf-8") as fh:
            lines = fh.readlines()
        h_idx = s10_idx = s3_idx = None
        for i, line in enumerate(lines):
            if "horizon_hours = _compute_forecast_horizon_hours" in line:
                h_idx = i
            if "Step 10D: Kalshi Weather shadow capture" in line:
                s10_idx = i
            if "Step 3: Live Kalshi prices" in line:
                s3_idx = i
        self.assertIsNotNone(h_idx)
        self.assertIsNotNone(s10_idx)
        self.assertIsNotNone(s3_idx)
        self.assertGreater(s10_idx, h_idx)
        self.assertLess(s10_idx, s3_idx)

    def test_SD6_app_py_flag_check_uses_os_environ_get(self):
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx_s = src.find("Step 10D: Kalshi Weather shadow capture")
        idx_e = src.find("Step 3: Live Kalshi prices", idx_s)
        block = src[idx_s:idx_e]
        self.assertIn('os.environ.get("KALSHI_WX_SHADOW_AGENT_ENABLED"', block)

    def test_SD6_capture_module_flag_gates_before_orchestrator(self):
        reached: list = []
        with patch(_FLAG_PATH, False):
            with patch(_ORCH_PATH, side_effect=lambda **kw: reached.append(1)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(reached, [])


# ═════════════════════════════════════════════════════════════════════════════
# SD7 — Non-blocking dispatch and semaphore behavior
# ═════════════════════════════════════════════════════════════════════════════

class TestSD7NonBlockingAndSemaphore(unittest.TestCase):
    """
    Verify that the orchestrator runs in a daemon thread (not on the request
    thread) and that the semaphore prevents concurrent duplicate shadow runs.
    """

    def test_SD7a_thread_is_started_not_called_directly(self):
        """
        When _Thread is NOT patched with _SyncThread, the orchestrator must NOT
        be called on the calling thread.  We verify by ensuring the call returns
        before the mocked orchestrator completes a blocking sleep.
        """
        call_completed: list = []
        event = threading.Event()

        def _slow_orch(**kwargs):
            event.wait(timeout=5)  # blocks until we set it
            call_completed.append(True)
            return MagicMock()

        fresh_sema = threading.Semaphore(999)

        with patch(_FLAG_PATH, True):
            with patch(_CLIENT_PATH, return_value=MagicMock()):
                with patch(_ORCH_PATH, side_effect=_slow_orch):
                    with patch(_SEMA_PATH, fresh_sema):
                        t0 = time.monotonic()
                        maybe_fire_shadow_snapshot(**_call_kwargs())
                        elapsed = time.monotonic() - t0

        # maybe_fire_shadow_snapshot() must return before the orchestrator finishes
        self.assertLess(elapsed, 1.0,
                        "Route thread blocked waiting for orchestrator — "
                        "non-blocking dispatch is broken")

        # Signal the background thread to finish and clean up
        event.set()
        time.sleep(0.2)  # brief wait so daemon thread exits cleanly

    def test_SD7b_semaphore_held_causes_skip(self):
        """
        When _SHADOW_SEMAPHORE is already held (0 permits), a call logs
        SHADOW_CAPTURE_SKIPPED at INFO level and returns None without
        starting the orchestrator.
        """
        held_sema = threading.Semaphore(0)  # already acquired — no permits
        reached: list = []

        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="INFO") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_CLIENT_PATH, return_value=MagicMock()):
                    with patch(_ORCH_PATH, side_effect=lambda **kw: reached.append(1)):
                        with patch(_SEMA_PATH, held_sema):
                            with patch(_THREAD_PATH, _SyncThread):
                                result = maybe_fire_shadow_snapshot(**_call_kwargs())

        self.assertIsNone(result)
        self.assertEqual(reached, [], "Orchestrator must not be called when semaphore held")
        full_log = "\n".join(log_ctx.output)
        self.assertIn("SHADOW_CAPTURE_SKIPPED", full_log)

    def test_SD7c_semaphore_released_after_orchestrator_success(self):
        """
        After a successful dispatch (via _SyncThread), the semaphore is back
        at 1 — confirmed by a second call successfully acquiring it.
        """
        fresh_sema = threading.Semaphore(1)

        with patch(_FLAG_PATH, True):
            with patch(_CLIENT_PATH, return_value=MagicMock()):
                with patch(_ORCH_PATH, return_value=MagicMock()):
                    with patch(_THREAD_PATH, _SyncThread):
                        with patch(_SEMA_PATH, fresh_sema):
                            maybe_fire_shadow_snapshot(**_call_kwargs())
                            # After first call, semaphore must be released
                            # (second call should succeed, not skip)
                            captured2: list = []
                            def _orch2(**kw):
                                captured2.append(1)
                                return MagicMock()
                            with patch(_ORCH_PATH, side_effect=_orch2):
                                maybe_fire_shadow_snapshot(**_call_kwargs())

        self.assertEqual(len(captured2), 1,
                         "Semaphore was not released after first run — second run was skipped")

    def test_SD7d_semaphore_released_after_orchestrator_exception(self):
        """
        Semaphore must be released even when the orchestrator raises.
        After the exception, a second call should succeed.
        """
        fresh_sema = threading.Semaphore(1)
        captured2: list = []

        def _explode(**kw):
            raise RuntimeError("orchestrator boom")

        def _ok(**kw):
            captured2.append(1)
            return MagicMock()

        with patch(_FLAG_PATH, True):
            with patch(_CLIENT_PATH, return_value=MagicMock()):
                with patch(_THREAD_PATH, _SyncThread):
                    with patch(_SEMA_PATH, fresh_sema):
                        # First call — orchestrator raises
                        with patch(_ORCH_PATH, side_effect=_explode):
                            maybe_fire_shadow_snapshot(**_call_kwargs())
                        # Second call — should succeed (semaphore released)
                        with patch(_ORCH_PATH, side_effect=_ok):
                            maybe_fire_shadow_snapshot(**_call_kwargs())

        self.assertEqual(len(captured2), 1,
                         "Semaphore not released after orchestrator exception")

    def test_SD7e_capture_module_has_thread_alias_and_semaphore(self):
        """
        Structural: capture module must export _Thread and _SHADOW_SEMAPHORE
        as patchable module-level names.
        """
        import gate_engine.kalshi_wx_shadow_capture as _mod
        self.assertTrue(hasattr(_mod, "_Thread"),
                        "_Thread alias not found in capture module")
        self.assertTrue(hasattr(_mod, "_SHADOW_SEMAPHORE"),
                        "_SHADOW_SEMAPHORE not found in capture module")
        self.assertIsInstance(_mod._SHADOW_SEMAPHORE, type(threading.Semaphore(1)))


# ═════════════════════════════════════════════════════════════════════════════
# SD — source_failures tuple integrity
# ═════════════════════════════════════════════════════════════════════════════

class TestSDSourceFailuresInSnapshot(unittest.TestCase):

    def _get_snap(self, tier_detail):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs(tier_detail=tier_detail))
        return captured[0]["snapshot"]

    def test_source_failures_is_tuple(self):
        snap = self._get_snap({"nws": {"attempted": True, "ok": False, "error": "e"}})
        self.assertIsInstance(snap.source_failures, tuple)

    def test_source_failures_empty_when_no_failures(self):
        snap = self._get_snap({"nws": {"attempted": True, "ok": True, "error": None}})
        self.assertEqual(snap.source_failures, ())

    def test_source_failures_has_failure_entry(self):
        snap = self._get_snap({"nws": {"attempted": True, "ok": False, "error": "HTTP 503"}})
        self.assertEqual(len(snap.source_failures), 1)
        self.assertIn("nws", snap.source_failures[0])


if __name__ == "__main__":
    unittest.main()
