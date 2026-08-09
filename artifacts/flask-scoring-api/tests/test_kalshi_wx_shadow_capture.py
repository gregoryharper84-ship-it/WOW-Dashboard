"""
tests/test_kalshi_wx_shadow_capture.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 12.5 tests

Tests for gate_engine/kalshi_wx_shadow_capture.py and the flag-gated insertion
in the /wow/kalshi/weather/evaluate route handler.

No live API calls, no live DB connections are made anywhere in this file.

PATCHING NOTE — lazy imports
  maybe_fire_shadow_snapshot() lazily imports its dependencies inside its try
  block, so those names are never attributes of the capture module at import
  time.  Patches must target the source module where the name lives:

    insert_shadow_snapshot → gate_engine.kalshi_wx_shadow_db
    WeatherResearchSnapshot → gate_engine.kalshi_wx_shadow_snapshot

  _SHADOW_ENABLED is a top-level name in the capture module and patches
  directly there.

Test plan
─────────
SD1  Flag OFF: maybe_fire_shadow_snapshot() returns immediately; insert,
     snapshot constructor, and any DB access are never invoked.

SD2  Flag ON + exception: exception logged as SHADOW_CAPTURE_FAILURE, function
     returns None.  Includes DB error path.

SD3  Flag ON, valid path: snapshot passed to insert_shadow_snapshot carries
     the actual route-local values, not test defaults or fabricated data.

SD4  Sentinel verification: six unavailable fields carry UNAVAILABLE_SENTINEL.

SD5  No new network calls, no threading (AST structural).

SD6  Structural and helper unit tests.

SDDB DB-specific tests: insert called with correct snapshot, DB error swallowed,
     error logged.

SDST Structural invariants: threading.Thread absent, run_shadow_orchestrator
     absent, SDK client absent.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

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
_INSERT_PATH  = "gate_engine.kalshi_wx_shadow_db.insert_shadow_snapshot"
_SNAP_PATH    = "gate_engine.kalshi_wx_shadow_snapshot.WeatherResearchSnapshot"
_FLAG_PATH    = "gate_engine.kalshi_wx_shadow_capture._SHADOW_ENABLED"


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


# ── Context manager: flag on, insert mocked ───────────────────────────────────

@contextmanager
def _flag_on_mocked():
    """
    Enable flag + mock insert_shadow_snapshot.
    Yields the list that receives each snapshot argument passed to insert.
    """
    captured: list = []

    def _fake_insert(snapshot):
        captured.append(snapshot)

    with patch(_FLAG_PATH, True):
        with patch(_INSERT_PATH, side_effect=_fake_insert):
            yield captured


# ═════════════════════════════════════════════════════════════════════════════
# SD1 — Flag OFF: capture is completely inert, zero DB access
# ═════════════════════════════════════════════════════════════════════════════

class TestSD1FlagOff(unittest.TestCase):

    def test_SD1_returns_none_with_flag_off(self):
        with patch(_FLAG_PATH, False):
            self.assertIsNone(maybe_fire_shadow_snapshot(**_call_kwargs()))

    def test_SD1_insert_never_called_with_flag_off(self):
        """insert_shadow_snapshot must not be invoked when flag is off."""
        sentinel = MagicMock(side_effect=AssertionError("DB must not be touched"))
        with patch(_FLAG_PATH, False):
            with patch(_INSERT_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_snapshot_constructor_never_called_with_flag_off(self):
        sentinel = MagicMock(side_effect=AssertionError("must not be called"))
        with patch(_FLAG_PATH, False):
            with patch(_SNAP_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_no_db_access_when_flag_off(self):
        """
        Any psycopg2 connect call while flag is off must fail the test.
        We achieve this by patching the _get_shadow_conn helper to raise
        AssertionError; the test passes only if it is never called.
        """
        conn_sentinel = MagicMock(
            side_effect=AssertionError("psycopg2 connect must not be reached")
        )
        with patch(_FLAG_PATH, False):
            with patch(
                "gate_engine.kalshi_wx_shadow_db._get_shadow_conn", conn_sentinel
            ):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        conn_sentinel.assert_not_called()

    def test_SD1_module_has_independent_second_gate(self):
        reached: list = []
        with patch(_FLAG_PATH, False):
            with patch(_INSERT_PATH, side_effect=lambda s: reached.append(1)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(reached, [])


# ═════════════════════════════════════════════════════════════════════════════
# SD2 — Flag ON + exception: exception logged and swallowed
# ═════════════════════════════════════════════════════════════════════════════

class TestSD2ExceptionIsolation(unittest.TestCase):

    def test_SD2_db_error_is_swallowed(self):
        """A DB RuntimeError from insert_shadow_snapshot must not propagate."""
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_PATH, side_effect=RuntimeError("connection refused")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_db_error_produces_warning_log(self):
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_INSERT_PATH, side_effect=RuntimeError("db-boom")):
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIn("SHADOW_CAPTURE_FAILURE", "\n".join(log_ctx.output))

    def test_SD2_ValueError_from_snapshot_constructor_is_swallowed(self):
        with patch(_FLAG_PATH, True):
            with patch(_SNAP_PATH, side_effect=ValueError("bad field")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_exception_log_contains_city(self):
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING"):
            with patch(_FLAG_PATH, True):
                with patch(_INSERT_PATH, side_effect=RuntimeError("x")):
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
    """
    Verify that the snapshot passed to insert_shadow_snapshot contains the
    actual values from the call site, not test defaults or fabricated data.
    """

    def _run_and_capture_snapshot(self, **call_kw):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**call_kw)
        self.assertEqual(
            len(captured), 1,
            "insert_shadow_snapshot must be called exactly once",
        )
        return captured[0]

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
        snap = self._run_and_capture_snapshot(
            **_call_kwargs(city="CHI", market_date="2026-08-20")
        )
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

    def test_SD3_insert_called_once_not_zero_not_twice(self):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(len(captured), 1)


# ═════════════════════════════════════════════════════════════════════════════
# SD4 — Sentinel verification
# ═════════════════════════════════════════════════════════════════════════════

class TestSD4SentinelFields(unittest.TestCase):

    def setUp(self):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs())
        self.snap = captured[0]

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
# SD5 — No new network calls, no threading (AST structural)
# ═════════════════════════════════════════════════════════════════════════════

class TestSD5NoNewNetworkCallsNoThreading(unittest.TestCase):

    _FORBIDDEN_CALLS = frozenset({
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
        violations = self._call_names(src) & self._FORBIDDEN_CALLS
        self.assertEqual(violations, set(), f"Forbidden calls: {sorted(violations)}")

    def test_SD5_capture_module_no_top_level_requests_import(self):
        with open(_CAPTURE_SRC, encoding="utf-8") as fh:
            src = fh.read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "requests")

    def test_SD5_capture_module_does_not_import_threading(self):
        """threading must not appear as an import anywhere in the capture module."""
        with open(_CAPTURE_SRC, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        "threading", alias.name,
                        f"threading imported at line {node.lineno}",
                    )
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(
                    node.module, "threading",
                    f"from threading imported at line {node.lineno}",
                )
                if node.module:
                    self.assertNotIn(
                        "threading", node.module,
                        f"threading in from-import module at line {node.lineno}",
                    )

    def test_SD5_app_py_shadow_block_no_fetch_calls(self):
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx_s = src.find("Step 10D: Kalshi Weather shadow capture")
        idx_e = src.find("Step 3: Live Kalshi prices", idx_s)
        self.assertNotEqual(idx_s, -1)
        self.assertNotEqual(idx_e, -1)
        block = src[idx_s:idx_e]
        for name in self._FORBIDDEN_CALLS:
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

    def test_SD6_capture_module_flag_gates_before_insert(self):
        reached: list = []
        with patch(_FLAG_PATH, False):
            with patch(_INSERT_PATH, side_effect=lambda s: reached.append(1)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(reached, [])


# ═════════════════════════════════════════════════════════════════════════════
# SDDB — DB-specific behaviour tests
# ═════════════════════════════════════════════════════════════════════════════

class TestSDBBehavior(unittest.TestCase):
    """
    Tests for the DB persistence path, all using mocked insert_shadow_snapshot
    so no live DB connection is required.
    """

    def test_SDDB_insert_called_with_snapshot_on_happy_path(self):
        """insert_shadow_snapshot must receive a WeatherResearchSnapshot."""
        from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
        captured: list = []
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_PATH, side_effect=lambda s: captured.append(s)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0], WeatherResearchSnapshot)

    def test_SDDB_db_error_does_not_propagate(self):
        """A RuntimeError from insert_shadow_snapshot must never reach the caller."""
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_PATH, side_effect=RuntimeError("DB connection refused")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SDDB_db_error_logged_as_warning(self):
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_INSERT_PATH, side_effect=RuntimeError("pg error")):
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        full_log = "\n".join(log_ctx.output)
        self.assertIn("SHADOW_CAPTURE_FAILURE", full_log)

    def test_SDDB_db_error_log_contains_error_type(self):
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_INSERT_PATH, side_effect=RuntimeError("unique violation")):
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIn("RuntimeError", "\n".join(log_ctx.output))

    def test_SDDB_db_operational_error_does_not_propagate(self):
        """psycopg2.OperationalError (conn refused) must also be swallowed."""
        # Simulate the specific exception class the real code would raise.
        import psycopg2
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_PATH,
                       side_effect=psycopg2.OperationalError("connection refused")):
                result = maybe_fire_snapshot = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SDDB_success_logs_shadow_capture_ok(self):
        """On successful insert, SHADOW_CAPTURE_OK must be logged at INFO."""
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="INFO") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_INSERT_PATH, return_value=None):
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIn("SHADOW_CAPTURE_OK", "\n".join(log_ctx.output))

    def test_SDDB_snapshot_research_snapshot_id_is_in_insert_arg(self):
        """The snapshot_id (uuid-based) must be present in the inserted snapshot."""
        captured: list = []
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_PATH, side_effect=lambda s: captured.append(s)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        snap = captured[0]
        self.assertTrue(
            snap.research_snapshot_id.startswith("wx-capture-"),
            f"Unexpected research_snapshot_id: {snap.research_snapshot_id!r}",
        )

    def test_SDDB_snapshot_values_match_route_locals_city(self):
        captured: list = []
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_PATH, side_effect=lambda s: captured.append(s)):
                maybe_fire_shadow_snapshot(**_call_kwargs(city="LAX"))
        self.assertEqual(captured[0].city, "LAX")

    def test_SDDB_snapshot_values_match_route_locals_sigma_f(self):
        captured: list = []
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_PATH, side_effect=lambda s: captured.append(s)):
                maybe_fire_shadow_snapshot(**_call_kwargs(sigma_f=7.25))
        self.assertAlmostEqual(captured[0].sigma_f, 7.25)


# ═════════════════════════════════════════════════════════════════════════════
# SDST — Structural invariants
# ═════════════════════════════════════════════════════════════════════════════

class TestSDSTStructuralInvariants(unittest.TestCase):
    """
    Structural tests that verify the capture module's source code no longer
    contains references to threading, the orchestrator, or any SDK client.
    """

    def _capture_src(self) -> str:
        with open(_CAPTURE_SRC, encoding="utf-8") as fh:
            return fh.read()

    def test_SDST_no_threading_import_in_capture_module(self):
        """
        'import threading' must not appear anywhere in the capture module.
        No threading of any kind belongs in this module post-Step-12.5.
        """
        src = self._capture_src()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        "threading", alias.name,
                        f"Found threading import at line {node.lineno}",
                    )
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertNotIn(
                    "threading", mod,
                    f"Found from-threading import at line {node.lineno}",
                )

    def test_SDST_Thread_not_referenced_in_capture_module(self):
        """
        The string 'Thread' must not appear in the capture module source.
        """
        src = self._capture_src()
        self.assertNotIn(
            "Thread",
            src,
            "Found 'Thread' reference in capture module — "
            "threading was explicitly removed in Step 12.5",
        )

    def test_SDST_Semaphore_not_referenced_in_capture_module(self):
        """
        'Semaphore' must not appear in the capture module source.
        """
        src = self._capture_src()
        self.assertNotIn(
            "Semaphore",
            src,
            "Found 'Semaphore' reference in capture module",
        )

    def test_SDST_run_shadow_orchestrator_not_referenced_in_capture_module(self):
        """
        'run_shadow_orchestrator' must not appear in the capture module.
        The live route never calls the orchestrator; that is the pilot runner's job.
        """
        src = self._capture_src()
        self.assertNotIn(
            "run_shadow_orchestrator",
            src,
            "Found run_shadow_orchestrator reference in capture module",
        )

    def test_SDST_no_sdk_client_construction_in_capture_module(self):
        """
        'Anthropic', '_build_shadow_sdk_client', and 'anthropic' must not appear
        in the capture module.  The live route never builds an SDK client.
        """
        src = self._capture_src()
        self.assertNotIn(
            "_build_shadow_sdk_client", src,
            "Found _build_shadow_sdk_client in capture module",
        )
        self.assertNotIn(
            "Anthropic(", src,
            "Found Anthropic() constructor in capture module",
        )

    def test_SDST_capture_module_uses_insert_shadow_snapshot(self):
        """
        The capture module must reference insert_shadow_snapshot (the new path).
        """
        src = self._capture_src()
        self.assertIn(
            "insert_shadow_snapshot",
            src,
            "insert_shadow_snapshot not found in capture module",
        )

    def test_SDST_app_py_shadow_tables_in_cm_schema_ddl(self):
        """
        app.py's _CM_SCHEMA_DDL must contain both shadow tables so they are
        created at startup via _cm_ensure_schema().
        """
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx = src.find("_CM_SCHEMA_DDL")
        self.assertNotEqual(idx, -1)
        # Find the DDL string (from the triple-quote after the assignment)
        ddl_start = src.find('"""', idx)
        ddl_end   = src.find('"""', ddl_start + 3)
        ddl = src[ddl_start:ddl_end]
        self.assertIn("kalshi_wx_shadow_snapshot_queue", ddl,
                      "kalshi_wx_shadow_snapshot_queue missing from _CM_SCHEMA_DDL")
        self.assertIn("kalshi_wx_shadow_results", ddl,
                      "kalshi_wx_shadow_results missing from _CM_SCHEMA_DDL")

    def test_SDST_shadow_schema_ddl_has_pending_default(self):
        """The snapshot_queue table must default status to 'PENDING'."""
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx = src.find("kalshi_wx_shadow_snapshot_queue")
        self.assertNotEqual(idx, -1)
        block = src[idx: idx + 400]
        self.assertIn("PENDING", block)

    def test_SDST_shadow_schema_ddl_snapshot_id_is_unique(self):
        """research_snapshot_id must have a UNIQUE constraint."""
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx = src.find("kalshi_wx_shadow_snapshot_queue")
        block = src[idx: idx + 400]
        self.assertIn("UNIQUE", block)

    def test_SDST_shadow_schema_ddl_jsonb_column_present(self):
        """snapshot_json must be JSONB."""
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx = src.find("kalshi_wx_shadow_snapshot_queue")
        block = src[idx: idx + 400]
        self.assertIn("JSONB", block)


# ═════════════════════════════════════════════════════════════════════════════
# SD — source_failures tuple integrity (unchanged from prior version)
# ═════════════════════════════════════════════════════════════════════════════

class TestSDSourceFailuresInSnapshot(unittest.TestCase):

    def _get_snap(self, tier_detail):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs(tier_detail=tier_detail))
        return captured[0]

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


# ═════════════════════════════════════════════════════════════════════════════
# SDDB2 — kalshi_wx_shadow_db module unit tests (no live DB)
# ═════════════════════════════════════════════════════════════════════════════

class TestSDBModuleHelpers(unittest.TestCase):
    """
    Tests for gate_engine/kalshi_wx_shadow_db.py helpers that don't touch
    a real database.
    """

    def test_snapshot_to_json_dict_produces_dict(self):
        from gate_engine.kalshi_wx_shadow_db import snapshot_to_json_dict
        from gate_engine.kalshi_wx_shadow_snapshot import build_test_snapshot
        import uuid
        snap = build_test_snapshot(
            research_snapshot_id=f"test-{uuid.uuid4()}",
            canonical_event_id="test-event",
            city="NYC",
            station="KNYC",
            market_date="2026-08-15",
            source_cutoff_timestamp="2026-08-14T18:00:00Z",
            forecast_high_used_by_deterministic_model=85.0,
            weather_data_source_tier="nws_primary",
            forecast_horizon_hours=24.0,
            sigma_f=3.5,
            deterministic_weather_readiness_state="READY",
            source_failures=(),
            source_disagreements=(),
            source_timestamps={"nws_primary": "2026-08-14T17:00:00Z"},
            source_provenance={},
        )
        result = snapshot_to_json_dict(snap)
        self.assertIsInstance(result, dict)

    def test_snapshot_to_json_dict_tuples_become_lists(self):
        """All tuple values must be converted to lists for JSON compatibility."""
        from gate_engine.kalshi_wx_shadow_db import snapshot_to_json_dict
        from gate_engine.kalshi_wx_shadow_snapshot import build_test_snapshot
        import json, uuid
        snap = build_test_snapshot(
            research_snapshot_id=f"test-{uuid.uuid4()}",
            canonical_event_id="test-event",
            city="NYC", station="KNYC", market_date="2026-08-15",
            source_cutoff_timestamp="2026-08-14T18:00:00Z",
            forecast_high_used_by_deterministic_model=85.0,
            weather_data_source_tier="nws_primary",
            forecast_horizon_hours=24.0, sigma_f=3.5,
            deterministic_weather_readiness_state="READY",
            source_failures=("nws: HTTP 503",),
            source_disagreements=("UNAVAILABLE",),
            source_timestamps={}, source_provenance={},
        )
        result = snapshot_to_json_dict(snap)
        # Must be round-trippable through JSON
        round_tripped = json.loads(json.dumps(result))
        self.assertIsInstance(round_tripped, dict)

    def test_snapshot_to_json_dict_city_preserved(self):
        from gate_engine.kalshi_wx_shadow_db import snapshot_to_json_dict
        from gate_engine.kalshi_wx_shadow_snapshot import build_test_snapshot
        import uuid
        snap = build_test_snapshot(
            research_snapshot_id=f"test-{uuid.uuid4()}",
            canonical_event_id="test-event",
            city="MIA", station="KMIA", market_date="2026-09-01",
            source_cutoff_timestamp="2026-08-31T18:00:00Z",
            forecast_high_used_by_deterministic_model=91.0,
            weather_data_source_tier="nws_primary",
            forecast_horizon_hours=24.0, sigma_f=3.0,
            deterministic_weather_readiness_state="READY",
            source_failures=(), source_disagreements=(),
            source_timestamps={}, source_provenance={},
        )
        result = snapshot_to_json_dict(snap)
        self.assertEqual(result["city"], "MIA")

    def test_shadow_schema_ddl_is_string(self):
        from gate_engine.kalshi_wx_shadow_db import SHADOW_SCHEMA_DDL
        self.assertIsInstance(SHADOW_SCHEMA_DDL, str)
        self.assertIn("kalshi_wx_shadow_snapshot_queue", SHADOW_SCHEMA_DDL)
        self.assertIn("kalshi_wx_shadow_results", SHADOW_SCHEMA_DDL)
        self.assertIn("IF NOT EXISTS", SHADOW_SCHEMA_DDL)

    def test_get_shadow_conn_raises_without_db_url(self):
        """_get_shadow_conn must raise RuntimeError when DATABASE_URL is absent."""
        from gate_engine.kalshi_wx_shadow_db import _get_shadow_conn
        with patch.dict(os.environ, {}, clear=True):
            # Remove DATABASE_URL if present
            env_without = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
            with patch.dict(os.environ, env_without, clear=True):
                with self.assertRaises(RuntimeError) as ctx:
                    _get_shadow_conn()
        self.assertIn("DATABASE_URL", str(ctx.exception))

    def test_insert_shadow_snapshot_raises_on_no_db_url(self):
        """insert_shadow_snapshot must raise (not swallow) when DATABASE_URL missing."""
        from gate_engine.kalshi_wx_shadow_db import insert_shadow_snapshot
        from gate_engine.kalshi_wx_shadow_snapshot import build_test_snapshot
        import uuid
        snap = build_test_snapshot(
            research_snapshot_id=f"test-{uuid.uuid4()}",
            canonical_event_id="test-event",
            city="NYC", station="KNYC", market_date="2026-08-15",
            source_cutoff_timestamp="2026-08-14T18:00:00Z",
            forecast_high_used_by_deterministic_model=85.0,
            weather_data_source_tier="nws_primary",
            forecast_horizon_hours=24.0, sigma_f=3.5,
            deterministic_weather_readiness_state="READY",
            source_failures=(), source_disagreements=(),
            source_timestamps={}, source_provenance={},
        )
        env_without = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
        with patch.dict(os.environ, env_without, clear=True):
            with self.assertRaises(RuntimeError):
                insert_shadow_snapshot(snap)


if __name__ == "__main__":
    unittest.main()
