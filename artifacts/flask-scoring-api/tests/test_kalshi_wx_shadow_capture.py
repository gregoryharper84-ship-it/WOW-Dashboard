"""
tests/test_kalshi_wx_shadow_capture.py
WOW-PATCH-2026-08-08-MULTI-AGENT-KALSHI-WX-SHADOW — Step 10D tests

Tests for gate_engine/kalshi_wx_shadow_capture.py and the minimal flag-gated
insertion in the /wow/kalshi/weather/evaluate route handler.

No live API calls are made anywhere in this file.  All network-touching code
is either mocked out or structurally verified to be absent.

PATCHING NOTE
  maybe_fire_shadow_snapshot() lazily imports its heavy dependencies (snapshot
  constructor, orchestrator, capability boundary, ledger) inside its try block,
  so those names are never attributes of the kalshi_wx_shadow_capture module.
  Patches must target the *source* module where the name actually lives:
    - run_shadow_orchestrator  → gate_engine.kalshi_wx_shadow_orchestrator
    - WeatherResearchSnapshot  → gate_engine.kalshi_wx_shadow_snapshot
    - CapabilityBoundary       → gate_engine.kalshi_wx_shadow_capability_boundary
    - get_default_ledger       → gate_engine.kalshi_wx_shadow_ledger
  _build_shadow_sdk_client IS a top-level function in the capture module, so
  it patches on gate_engine.kalshi_wx_shadow_capture directly.

Test plan
─────────
SD1  Flag OFF (default): maybe_fire_shadow_snapshot() returns immediately;
     the shadow capture module, orchestrator, and SDK client are never invoked.

SD2  Flag ON, shadow construction raises an exception: the exception is
     logged as a shadow failure and swallowed; the function still returns None.

SD3  Flag ON, valid path (mocked orchestrator): the WeatherResearchSnapshot
     instance received by the orchestrator carries the actual real route-local
     values (city, station, market_date, forecast_high, sigma_f, horizon_hours,
     weather_data_source_tier) — not test literals, not defaults.

SD4  Sentinel verification: source_provenance, source_disagreements,
     nws_gridpoint_forecast, open_meteo_forecast, noaa_ncei_forecast, and
     official_observations_at_cutoff are set to the explicit UNAVAILABLE
     sentinel — not None, not any other value.

SD5  No new network calls: structural AST check confirming that neither
     app.py's new shadow block nor kalshi_wx_shadow_capture.py contains
     any new call to the fetch functions, requests library, or HTTP client.

SD6  Structural and unit-level verification that existing behaviour is
     unchanged: pure-helper correctness, insertion-point position in app.py,
     and the per-module flag-gate invariant.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call

# ── Path setup ────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


# ── Source-file paths for structural checks ───────────────────────────────────
_CAPTURE_SRC = os.path.join(_REPO, "gate_engine", "kalshi_wx_shadow_capture.py")
_APP_SRC     = os.path.join(_REPO, "app.py")


# ── Imports from the module under test ───────────────────────────────────────
from gate_engine.kalshi_wx_shadow_capture import (
    UNAVAILABLE_SENTINEL,
    _derive_source_failures,
    _derive_readiness_state,
    _derive_source_timestamps,
    maybe_fire_shadow_snapshot,
)


# ── Shared helper: minimal valid call kwargs ──────────────────────────────────

def _call_kwargs(**overrides) -> dict:
    """Return a representative set of already-computed route-local values."""
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


# ── Patch-target constants (see PATCHING NOTE in module docstring) ────────────
_ORCH_PATH   = "gate_engine.kalshi_wx_shadow_orchestrator.run_shadow_orchestrator"
_SNAP_PATH   = "gate_engine.kalshi_wx_shadow_snapshot.WeatherResearchSnapshot"
_CLIENT_PATH = "gate_engine.kalshi_wx_shadow_capture._build_shadow_sdk_client"
_LEDGER_PATH = "gate_engine.kalshi_wx_shadow_ledger.get_default_ledger"
_BDRY_PATH   = "gate_engine.kalshi_wx_shadow_capability_boundary.CapabilityBoundary"
_FLAG_PATH   = "gate_engine.kalshi_wx_shadow_capture._SHADOW_ENABLED"


@contextmanager
def _flag_on_mocked():
    """
    Context manager: enable flag + mock SDK client builder + mock orchestrator.
    Yields the list that the mock orchestrator appends captured kwargs to.
    """
    captured: list = []

    def _fake_orchestrator(**kwargs):
        captured.append(kwargs)
        return MagicMock()

    with patch(_FLAG_PATH, True):
        with patch(_CLIENT_PATH, return_value=MagicMock()):
            with patch(_ORCH_PATH, side_effect=_fake_orchestrator):
                yield captured


# ═════════════════════════════════════════════════════════════════════════════
# SD1 — Flag OFF: capture is completely inert
# ═════════════════════════════════════════════════════════════════════════════

class TestSD1FlagOff(unittest.TestCase):
    """
    When _SHADOW_ENABLED is False, maybe_fire_shadow_snapshot() returns
    immediately without constructing a snapshot, importing the orchestrator,
    or touching any mock.
    """

    def test_SD1_returns_none_with_flag_off(self):
        with patch(_FLAG_PATH, False):
            result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD1_orchestrator_never_called_with_flag_off(self):
        """run_shadow_orchestrator must not be called when flag is off."""
        mock_orch = MagicMock(side_effect=AssertionError(
            "run_shadow_orchestrator must not be called when flag is off"
        ))
        with patch(_FLAG_PATH, False):
            with patch(_ORCH_PATH, mock_orch):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        mock_orch.assert_not_called()

    def test_SD1_sdk_client_never_built_with_flag_off(self):
        """_build_shadow_sdk_client must not be called when flag is off."""
        sentinel = MagicMock(side_effect=AssertionError(
            "_build_shadow_sdk_client must not run when flag is off"
        ))
        with patch(_FLAG_PATH, False):
            with patch(_CLIENT_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_snapshot_constructor_never_called_with_flag_off(self):
        """WeatherResearchSnapshot must not be constructed when flag is off."""
        sentinel = MagicMock(side_effect=AssertionError(
            "WeatherResearchSnapshot must not be constructed when flag is off"
        ))
        with patch(_FLAG_PATH, False):
            with patch(_SNAP_PATH, sentinel):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        sentinel.assert_not_called()

    def test_SD1_module_has_independent_second_gate(self):
        """
        Even if called directly (bypassing app.py's outer flag check),
        the module's own flag gate fires first and is a no-op when False.
        Verified by ensuring orchestrator is not reached.
        """
        reached: list = []
        with patch(_FLAG_PATH, False):
            with patch(_ORCH_PATH, side_effect=lambda **kw: reached.append(1)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(reached, [])


# ═════════════════════════════════════════════════════════════════════════════
# SD2 — Flag ON + exception in shadow: exception is logged and swallowed
# ═════════════════════════════════════════════════════════════════════════════

class TestSD2ExceptionIsolation(unittest.TestCase):
    """
    When the flag is on and something inside maybe_fire_shadow_snapshot()
    raises, the exception must be swallowed (not propagated) and the call
    must return None.
    """

    def test_SD2_RuntimeError_from_orchestrator_is_swallowed(self):
        with patch(_FLAG_PATH, True):
            with patch(_CLIENT_PATH, return_value=MagicMock()):
                with patch(_ORCH_PATH, side_effect=RuntimeError("boom")):
                    result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_ValueError_from_snapshot_constructor_is_swallowed(self):
        with patch(_FLAG_PATH, True):
            with patch(_SNAP_PATH, side_effect=ValueError("bad field")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_missing_sdk_client_is_swallowed(self):
        """RuntimeError from _build_shadow_sdk_client is caught and swallowed."""
        with patch(_FLAG_PATH, True):
            with patch(_CLIENT_PATH, side_effect=RuntimeError("no key")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_exception_produces_warning_log(self):
        """The caught exception must produce a WARNING-level log entry."""
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_CLIENT_PATH, side_effect=RuntimeError("sdk-boom")):
                    maybe_fire_shadow_snapshot(**_call_kwargs())

        full_log = "\n".join(log_ctx.output)
        self.assertIn("SHADOW_CAPTURE_FAILURE", full_log)

    def test_SD2_exception_log_contains_city(self):
        kw = _call_kwargs(city="CHI", market_date="2026-09-01")
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_CLIENT_PATH, side_effect=RuntimeError("key-missing")):
                    maybe_fire_shadow_snapshot(**kw)
        full_log = "\n".join(log_ctx.output)
        self.assertIn("CHI", full_log)

    def test_SD2_exception_log_contains_date(self):
        kw = _call_kwargs(market_date="2026-09-05")
        with self.assertLogs("gate_engine.kalshi_wx_shadow_capture", level="WARNING") as log_ctx:
            with patch(_FLAG_PATH, True):
                with patch(_CLIENT_PATH, side_effect=RuntimeError("key-missing")):
                    maybe_fire_shadow_snapshot(**kw)
        full_log = "\n".join(log_ctx.output)
        self.assertIn("2026-09-05", full_log)

    def test_SD2_route_level_try_except_in_app_py(self):
        """
        Structural check: app.py shadow insertion block has its own
        try/except that is independent of the capture module's internal guard.
        """
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()

        idx = src.find("Step 10D: Kalshi Weather shadow capture")
        self.assertNotEqual(idx, -1, "Step 10D comment not found in app.py")
        block = src[idx: idx + 1500]

        self.assertIn("maybe_fire_shadow_snapshot as _mfss", block,
                      "app.py must import maybe_fire_shadow_snapshot as _mfss")
        self.assertIn("except Exception:", block,
                      "app.py shadow block must have its own except Exception:")
        self.assertIn("pass", block,
                      "app.py shadow block except clause must be pass")


# ═════════════════════════════════════════════════════════════════════════════
# SD3 — Flag ON, valid path: snapshot carries real route-local values
# ═════════════════════════════════════════════════════════════════════════════

class TestSD3SnapshotCarriesRealValues(unittest.TestCase):
    """
    When the flag is on and the orchestrator is mocked (no real SDK calls),
    the WeatherResearchSnapshot instance received by the orchestrator must
    contain the actual values passed to maybe_fire_shadow_snapshot() —
    not test defaults, not fabricated data.
    """

    def _run_and_capture_snapshot(self, **call_kw):
        """Run capture with mocked orchestrator; return the captured snapshot."""
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
        """The captured snapshot is a frozen WeatherResearchSnapshot instance."""
        import dataclasses
        from gate_engine.kalshi_wx_shadow_snapshot import WeatherResearchSnapshot
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertIsInstance(snap, WeatherResearchSnapshot)
        self.assertTrue(snap.__dataclass_params__.frozen)

    def test_SD3_canonical_event_id_is_derived_from_city_and_date(self):
        snap = self._run_and_capture_snapshot(
            **_call_kwargs(city="CHI", market_date="2026-08-20")
        )
        self.assertEqual(snap.canonical_event_id, "kalshi-nhigh-CHI-2026-08-20")

    def test_SD3_research_snapshot_id_is_unique_per_call(self):
        """Each call generates a distinct research_snapshot_id."""
        snap_a = self._run_and_capture_snapshot(**_call_kwargs())
        snap_b = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertNotEqual(snap_a.research_snapshot_id, snap_b.research_snapshot_id)

    def test_SD3_readiness_state_ready_when_forecast_high_present(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=88.0))
        self.assertEqual(snap.deterministic_weather_readiness_state, "READY")

    def test_SD3_readiness_state_data_unavailable_when_forecast_high_none(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=None))
        self.assertEqual(snap.deterministic_weather_readiness_state, "DATA_UNAVAILABLE")

    def test_SD3_forecast_high_zero_float_when_none_input(self):
        """forecast_high_used_by_deterministic_model stores 0.0 when input is None."""
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=None))
        self.assertEqual(snap.forecast_high_used_by_deterministic_model, 0.0)

    def test_SD3_orchestrator_called_with_city_and_date(self):
        """run_shadow_orchestrator receives city= and date= from the route locals."""
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs(city="AUS", market_date="2026-08-25"))
        self.assertEqual(len(captured), 1)
        kw = captured[0]
        self.assertEqual(kw.get("city"), "AUS")
        self.assertEqual(kw.get("date"), "2026-08-25")


# ═════════════════════════════════════════════════════════════════════════════
# SD4 — Sentinel verification
# ═════════════════════════════════════════════════════════════════════════════

class TestSD4SentinelFields(unittest.TestCase):
    """
    Fields not exposed at the capture insertion point must carry the explicit
    UNAVAILABLE sentinel — not None, not fabricated, not inferred.
    """

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
        """Every sentinel dict field must be not-None."""
        for field_name in (
            "nws_gridpoint_forecast",
            "open_meteo_forecast",
            "noaa_ncei_forecast",
            "official_observations_at_cutoff",
            "source_provenance",
        ):
            with self.subTest(field=field_name):
                self.assertIsNotNone(
                    getattr(self.snap, field_name),
                    f"{field_name} must not be None — use the UNAVAILABLE sentinel",
                )

    def test_SD4_sentinel_constant_is_non_empty_string(self):
        self.assertIsInstance(UNAVAILABLE_SENTINEL, str)
        self.assertGreater(len(UNAVAILABLE_SENTINEL), 0)

    def test_SD4_sentinel_appears_in_sentinel_dict_values(self):
        """UNAVAILABLE_SENTINEL appears as a value in each sentinel dict."""
        self.assertIn(UNAVAILABLE_SENTINEL, str(self.snap.nws_gridpoint_forecast))
        self.assertIn(UNAVAILABLE_SENTINEL, str(self.snap.source_provenance))
        self.assertIn(UNAVAILABLE_SENTINEL, str(self.snap.source_disagreements))


# ═════════════════════════════════════════════════════════════════════════════
# SD5 — No new network calls in app.py insertion block or capture module
# ═════════════════════════════════════════════════════════════════════════════

class TestSD5NoNewNetworkCalls(unittest.TestCase):
    """
    Structural AST-level verification that neither the app.py shadow insertion
    block nor kalshi_wx_shadow_capture.py introduces any call to fetch
    functions, the requests library, or any HTTP client.
    """

    _FORBIDDEN_FETCH_NAMES: frozenset = frozenset({
        "_fetch_forecast_high_tiered",
        "_fetch_nws_forecast_high",
        "_fetch_open_meteo_daily_high",
        "_fetch_noaa_ncei_daily_high",
        "_fetch_nws_cli",
        "_fetch_kalshi_nhigh_prices",
        "_nws_get",
        "_ncei_cdo_get",
    })
    _FORBIDDEN_HTTP_NAMES: frozenset = frozenset({
        "requests",
        "urllib",
        "httpx",
        "aiohttp",
    })

    def _all_call_names(self, source_text: str) -> set:
        """Return the set of all call name strings in parsed source_text."""
        try:
            tree = ast.parse(source_text)
        except SyntaxError:
            return set()
        names: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
                    if isinstance(node.func.value, ast.Name):
                        names.add(node.func.value.id)
        return names

    def test_SD5_capture_module_has_no_forbidden_fetch_calls(self):
        with open(_CAPTURE_SRC, encoding="utf-8") as fh:
            src = fh.read()
        call_names = self._all_call_names(src)
        violations = call_names & (self._FORBIDDEN_FETCH_NAMES | self._FORBIDDEN_HTTP_NAMES)
        self.assertEqual(violations, set(),
                         f"kalshi_wx_shadow_capture.py contains forbidden call(s): "
                         f"{sorted(violations)}")

    def test_SD5_capture_module_no_top_level_requests_import(self):
        """requests must not be imported at module level."""
        with open(_CAPTURE_SRC, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name, "requests",
                        "requests must not be imported at top level in capture module",
                    )

    def test_SD5_app_py_shadow_block_has_no_fetch_function_calls(self):
        """
        The app.py shadow insertion block must not reference any fetch function.
        """
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx_start = src.find("Step 10D: Kalshi Weather shadow capture")
        idx_end   = src.find("Step 3: Live Kalshi prices")
        self.assertNotEqual(idx_start, -1)
        self.assertNotEqual(idx_end, -1)
        block = src[idx_start:idx_end]
        for name in self._FORBIDDEN_FETCH_NAMES:
            self.assertNotIn(name, block,
                             f"app.py shadow block must not reference {name!r}")

    def test_SD5_app_py_shadow_block_does_not_store_return_value(self):
        """
        The app.py insertion discards the return value of _mfss(...) — it must
        not be assigned to a variable or used in any conditional.
        """
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx_start = src.find("Step 10D: Kalshi Weather shadow capture")
        idx_end   = src.find("Step 3: Live Kalshi prices")
        block = src[idx_start:idx_end]
        self.assertNotIn("= _mfss(", block,
                         "app.py must not assign the return value of _mfss()")
        self.assertNotIn("if _mfss(", block,
                         "app.py must not use _mfss() return value in a conditional")


# ═════════════════════════════════════════════════════════════════════════════
# SD6 — Structural and helper unit tests
# ═════════════════════════════════════════════════════════════════════════════

class TestSD6StructuralAndHelpers(unittest.TestCase):
    """
    1. Pure helper functions produce correct outputs (unit tests).
    2. Insertion-point position in app.py is exactly after horizon_hours
       and before Step 3.
    3. app.py flag check uses os.environ.get (not a module-level cached bool).
    4. Capture module's independent flag gate fires even on direct calls.
    """

    # ── Pure helper unit tests ────────────────────────────────────────────────

    def test_SD6_derive_source_failures_empty_when_all_ok(self):
        td = {"nws": {"attempted": True, "ok": True, "error": None}}
        self.assertEqual(_derive_source_failures(td), ())

    def test_SD6_derive_source_failures_captures_one_failed_tier(self):
        td = {
            "nws":        {"attempted": True, "ok": False, "error": "HTTP 503"},
            "open_meteo": {"attempted": True, "ok": True,  "error": None},
        }
        result = _derive_source_failures(td)
        self.assertEqual(len(result), 1)
        self.assertIn("nws", result[0])
        self.assertIn("HTTP 503", result[0])

    def test_SD6_derive_source_failures_all_three_tiers_failed(self):
        td = {
            "nws":        {"attempted": True, "ok": False, "error": "timeout"},
            "open_meteo": {"attempted": True, "ok": False, "error": "rate_limit"},
            "noaa_ncei":  {"attempted": True, "ok": False, "error": "no_token"},
        }
        result = _derive_source_failures(td)
        self.assertEqual(len(result), 3)

    def test_SD6_derive_source_failures_uses_unknown_error_when_error_is_none(self):
        td = {"nws": {"attempted": True, "ok": False, "error": None}}
        result = _derive_source_failures(td)
        self.assertIn("unknown_error", result[0])

    def test_SD6_derive_source_failures_skips_unattempted_tiers(self):
        td = {"nws": {"attempted": False, "ok": False, "error": "skipped"}}
        self.assertEqual(_derive_source_failures(td), ())

    def test_SD6_derive_source_failures_returns_tuple(self):
        td = {"nws": {"attempted": True, "ok": False, "error": "err"}}
        self.assertIsInstance(_derive_source_failures(td), tuple)

    def test_SD6_derive_readiness_ready_when_forecast_present(self):
        self.assertEqual(_derive_readiness_state(88.0), "READY")

    def test_SD6_derive_readiness_data_unavailable_when_none(self):
        self.assertEqual(_derive_readiness_state(None), "DATA_UNAVAILABLE")

    def test_SD6_derive_readiness_ready_for_zero_value(self):
        # 0.0 is not None → READY (readiness state reflects value presence, not plausibility)
        self.assertEqual(_derive_readiness_state(0.0), "READY")

    def test_SD6_derive_source_timestamps_contains_winning_tier(self):
        ts = _derive_source_timestamps("nws_primary", "2026-08-15T12:00:00Z")
        self.assertIn("nws_primary", ts)
        self.assertEqual(ts["nws_primary"], "2026-08-15T12:00:00Z")

    # ── Structural position check ─────────────────────────────────────────────

    def test_SD6_shadow_block_is_between_horizon_hours_and_step3(self):
        """
        In app.py, the Step 10D block appears between the horizon_hours
        assignment and the Step 3 Kalshi prices block.
        """
        with open(_APP_SRC, encoding="utf-8") as fh:
            lines = fh.readlines()

        horizon_idx = step10d_idx = step3_idx = None
        for i, line in enumerate(lines):
            if "horizon_hours = _compute_forecast_horizon_hours" in line:
                horizon_idx = i
            if "Step 10D: Kalshi Weather shadow capture" in line:
                step10d_idx = i
            if "Step 3: Live Kalshi prices" in line:
                step3_idx = i

        self.assertIsNotNone(horizon_idx,  "horizon_hours assignment not found in app.py")
        self.assertIsNotNone(step10d_idx,  "Step 10D block not found in app.py")
        self.assertIsNotNone(step3_idx,    "Step 3 marker not found in app.py")
        self.assertGreater(step10d_idx, horizon_idx,
                           "Step 10D block must come after horizon_hours assignment")
        self.assertLess(step10d_idx, step3_idx,
                        "Step 10D block must come before Step 3 Kalshi prices block")

    def test_SD6_app_py_flag_check_uses_os_environ_get(self):
        """
        The app.py shadow block gates the import/call with os.environ.get
        (not a module-level cached bool evaluated at import time).
        """
        with open(_APP_SRC, encoding="utf-8") as fh:
            src = fh.read()
        idx_start = src.find("Step 10D: Kalshi Weather shadow capture")
        self.assertNotEqual(idx_start, -1, "Step 10D marker not found in app.py")
        # Search for the end marker AFTER idx_start (avoid earlier occurrences)
        idx_end = src.find("Step 3: Live Kalshi prices", idx_start)
        self.assertNotEqual(idx_end, -1, "Step 3 marker not found after Step 10D in app.py")
        block = src[idx_start:idx_end]
        self.assertGreater(len(block), 0, "Shadow block between Step 10D and Step 3 is empty")
        self.assertIn('os.environ.get("KALSHI_WX_SHADOW_AGENT_ENABLED"', block,
                      "app.py must check env var directly with os.environ.get")

    def test_SD6_capture_module_flag_gates_before_orchestrator(self):
        """
        With _SHADOW_ENABLED=False in the capture module, the orchestrator
        is never reached — the module's independent gate fires.
        """
        reached: list = []
        with patch(_FLAG_PATH, False):
            with patch(_ORCH_PATH, side_effect=lambda **kw: reached.append(1)):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertEqual(reached, [])


# ═════════════════════════════════════════════════════════════════════════════
# SD — source_failures tuple integrity
# ═════════════════════════════════════════════════════════════════════════════

class TestSDSourceFailuresInSnapshot(unittest.TestCase):
    """source_failures on the snapshot is always a tuple, never a list."""

    def _get_snap(self, tier_detail):
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**_call_kwargs(tier_detail=tier_detail))
        return captured[0]["snapshot"]

    def test_source_failures_is_tuple_on_snapshot(self):
        snap = self._get_snap({"nws": {"attempted": True, "ok": False, "error": "e"}})
        self.assertIsInstance(snap.source_failures, tuple)

    def test_source_failures_empty_tuple_when_no_failures(self):
        snap = self._get_snap({"nws": {"attempted": True, "ok": True, "error": None}})
        self.assertEqual(snap.source_failures, ())

    def test_source_failures_contains_failure_entry(self):
        snap = self._get_snap({"nws": {"attempted": True, "ok": False, "error": "HTTP 503"}})
        self.assertEqual(len(snap.source_failures), 1)
        self.assertIn("nws", snap.source_failures[0])


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main()
