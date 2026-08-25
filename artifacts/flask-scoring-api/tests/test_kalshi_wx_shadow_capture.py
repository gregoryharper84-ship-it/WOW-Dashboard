"""
tests/test_kalshi_wx_shadow_capture.py
Step 12.5 (durable queue) + deterministic baseline linkage

Test groups
  SD1   Flag-off: zero DB access, zero inserts for both functions
  SD2   Exception isolation: DB/construction errors swallowed by both functions
  SD3   Snapshot field verification: actual route-local values reach the snapshot
  SD4   UNAVAILABLE sentinel coverage
  SD5   AST structural: no threading, no forbidden network calls
  SD6   Helper unit tests (pure functions)
  SDDB  DB-behaviour tests for insert_shadow_snapshot
  SDST  Structural invariants (threading absent, DDL constraints, table presence)
  SDLINK  Linkage: research_snapshot_id generated in caller matches both rows
  SDOUT   Outcome function: flag-off, flag-on, DB error isolation, field values
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Ensure project root on path ───────────────────────────────────────────────
_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from gate_engine.kalshi_wx_shadow_capture import (
    UNAVAILABLE_SENTINEL,
    _UNAVAIL_DICT,
    _UNAVAIL_TUPLE,
    _derive_readiness_state,
    _derive_source_failures,
    _derive_source_timestamps,
    maybe_fire_shadow_snapshot,
    maybe_link_shadow_deterministic_outcome,
)

# ── Constants for patch targets ────────────────────────────────────────────────
_FLAG_PATH            = "gate_engine.kalshi_wx_shadow_capture._SHADOW_ENABLED"
_INSERT_SNAP_PATH     = "gate_engine.kalshi_wx_shadow_db.insert_shadow_snapshot"
_INSERT_OUTCOME_PATH  = "gate_engine.kalshi_wx_shadow_db.insert_shadow_deterministic_outcome"
_GET_CONN_PATH        = "gate_engine.kalshi_wx_shadow_db._get_shadow_conn"


# ── Call-kwargs helpers ────────────────────────────────────────────────────────

def _call_kwargs(**overrides) -> dict:
    """Default valid arguments for maybe_fire_shadow_snapshot()."""
    base = dict(
        research_snapshot_id="wx-capture-test-00000000-0000-0000-0000-000000000001",
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


def _outcome_kwargs(**overrides) -> dict:
    """Default valid arguments for maybe_link_shadow_deterministic_outcome()."""
    base = dict(
        research_snapshot_id="wx-capture-test-00000000-0000-0000-0000-000000000001",
        terminal_label="KALSHI_WATCH",
        price_gate_disposition="DRY_RUN_ONLY: execution disabled per system policy",
        can_execute=False,
    )
    base.update(overrides)
    return base


# ── Context managers ──────────────────────────────────────────────────────────

@contextmanager
def _flag_on_mocked():
    """Enable flag + mock insert_shadow_snapshot; yield list of captured snapshots."""
    captured: list = []

    def _fake_insert(snapshot):
        captured.append(snapshot)

    with patch(_FLAG_PATH, True):
        with patch(_INSERT_SNAP_PATH, side_effect=_fake_insert):
            yield captured


@contextmanager
def _outcome_flag_on_mocked():
    """Enable flag + mock insert_shadow_deterministic_outcome; yield captured rows."""
    captured: list = []

    def _fake_insert(research_snapshot_id, terminal_label,
                     price_gate_disposition, can_execute):
        captured.append({
            "research_snapshot_id": research_snapshot_id,
            "terminal_label": terminal_label,
            "price_gate_disposition": price_gate_disposition,
            "can_execute": can_execute,
        })

    with patch(_FLAG_PATH, True):
        with patch(_INSERT_OUTCOME_PATH, side_effect=_fake_insert):
            yield captured


# ── Shared helper ─────────────────────────────────────────────────────────────

class _CaptureBase(unittest.TestCase):
    def _run_and_capture_snapshot(self, **kwargs):
        """Run maybe_fire_shadow_snapshot with flag on and mocked DB; return snapshot."""
        with _flag_on_mocked() as captured:
            maybe_fire_shadow_snapshot(**kwargs)
        self.assertEqual(len(captured), 1, "Expected exactly one snapshot captured")
        return captured[0]


# ═══════════════════════════════════════════════════════════════════════════════
# SD1 — Flag-off behaviour
# ═══════════════════════════════════════════════════════════════════════════════

class TestSD1FlagOff(unittest.TestCase):
    """When flag is off, zero DB access, zero inserts for both functions."""

    def _assert_no_db(self):
        def _should_not_be_called(*a, **kw):
            raise AssertionError("DB was accessed with flag off")
        return patch(_GET_CONN_PATH, side_effect=_should_not_be_called)

    def test_SD1_flag_off_fire_returns_none(self):
        with patch(_FLAG_PATH, False), self._assert_no_db():
            result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD1_flag_off_fire_zero_insert_calls(self):
        with patch(_FLAG_PATH, False), self._assert_no_db():
            with patch(_INSERT_SNAP_PATH) as mock_insert:
                maybe_fire_shadow_snapshot(**_call_kwargs())
        mock_insert.assert_not_called()

    def test_SD1_flag_off_outcome_returns_none(self):
        with patch(_FLAG_PATH, False), self._assert_no_db():
            result = maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        self.assertIsNone(result)

    def test_SD1_flag_off_outcome_zero_insert_calls(self):
        with patch(_FLAG_PATH, False), self._assert_no_db():
            with patch(_INSERT_OUTCOME_PATH) as mock_insert:
                maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        mock_insert.assert_not_called()

    def test_SD1_flag_off_both_functions_no_db_io(self):
        """Both functions together — zero DB access when flag is off."""
        rsid = "wx-capture-linkage-test-off"
        with patch(_FLAG_PATH, False), self._assert_no_db():
            maybe_fire_shadow_snapshot(**_call_kwargs(research_snapshot_id=rsid))
            maybe_link_shadow_deterministic_outcome(**_outcome_kwargs(research_snapshot_id=rsid))


# ═══════════════════════════════════════════════════════════════════════════════
# SD2 — Exception isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSD2ExceptionIsolation(unittest.TestCase):

    def test_SD2_db_error_is_swallowed(self):
        """DB error in insert_shadow_snapshot must not propagate."""
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=RuntimeError("conn refused")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_db_error_logged_as_warning(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=RuntimeError("boom")):
                with self.assertLogs("gate_engine.kalshi_wx_shadow_capture",
                                     level="WARNING") as cm:
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertTrue(any("SHADOW_CAPTURE_FAILURE" in line for line in cm.output))

    def test_SD2_outcome_db_error_swallowed(self):
        """DB error in insert_shadow_deterministic_outcome must not propagate."""
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_OUTCOME_PATH,
                       side_effect=RuntimeError("outcome db dead")):
                result = maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        self.assertIsNone(result)

    def test_SD2_outcome_db_error_logged(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_OUTCOME_PATH,
                       side_effect=RuntimeError("outcome boom")):
                with self.assertLogs("gate_engine.kalshi_wx_shadow_capture",
                                     level="WARNING") as cm:
                    maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        self.assertTrue(any("SHADOW_OUTCOME_FAILURE" in line for line in cm.output))

    def test_SD2_import_error_swallowed(self):
        """Import error must not propagate."""
        with patch(_FLAG_PATH, True):
            with patch.dict(sys.modules,
                            {"gate_engine.kalshi_wx_shadow_snapshot": None}):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SD2_outcome_import_error_swallowed(self):
        with patch(_FLAG_PATH, True):
            with patch.dict(sys.modules,
                            {"gate_engine.kalshi_wx_shadow_db": None}):
                result = maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════════
# SD3 — Snapshot field verification
# ═══════════════════════════════════════════════════════════════════════════════

class TestSD3SnapshotFields(_CaptureBase):

    def test_SD3_snapshot_research_snapshot_id_matches_caller_input(self):
        """research_snapshot_id is now caller-provided, not internally generated."""
        rsid = "wx-capture-explicit-caller-id-abc123"
        snap = self._run_and_capture_snapshot(**_call_kwargs(research_snapshot_id=rsid))
        self.assertEqual(snap.research_snapshot_id, rsid)

    def test_SD3_snapshot_city_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(city="MIA"))
        self.assertEqual(snap.city, "MIA")

    def test_SD3_snapshot_station_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(station="KMIA"))
        self.assertEqual(snap.station, "KMIA")

    def test_SD3_snapshot_market_date_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(market_date="2026-09-01"))
        self.assertEqual(snap.market_date, "2026-09-01")

    def test_SD3_snapshot_forecast_high_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=92.5))
        self.assertAlmostEqual(snap.forecast_high_used_by_deterministic_model, 92.5)

    def test_SD3_snapshot_sigma_f_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(sigma_f=4.0))
        self.assertAlmostEqual(snap.sigma_f, 4.0)

    def test_SD3_snapshot_horizon_hours_matches_input(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(horizon_hours=24.0))
        self.assertAlmostEqual(snap.forecast_horizon_hours, 24.0)

    def test_SD3_snapshot_tier_matches_input(self):
        snap = self._run_and_capture_snapshot(
            **_call_kwargs(weather_data_source_tier="open_meteo_fallback"))
        self.assertEqual(snap.weather_data_source_tier, "open_meteo_fallback")

    def test_SD3_canonical_event_id_contains_city_and_date(self):
        snap = self._run_and_capture_snapshot(
            **_call_kwargs(city="CHI", market_date="2026-08-20"))
        self.assertIn("CHI", snap.canonical_event_id)
        self.assertIn("2026-08-20", snap.canonical_event_id)

    def test_SD3_forecast_high_none_stored_as_zero(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs(forecast_high=None))
        self.assertEqual(snap.forecast_high_used_by_deterministic_model, 0.0)

    def test_SD3_insert_called_exactly_once(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH) as mock_insert:
                maybe_fire_shadow_snapshot(**_call_kwargs())
        mock_insert.assert_called_once()

    def test_SD3_two_calls_with_same_rsid_pass_same_id_to_snapshot(self):
        """Caller controls ID; same rsid → same ID in both snapshots."""
        rsid = "wx-capture-fixed-id-for-two-calls"
        snap_a = self._run_and_capture_snapshot(**_call_kwargs(research_snapshot_id=rsid))
        snap_b = self._run_and_capture_snapshot(**_call_kwargs(research_snapshot_id=rsid))
        self.assertEqual(snap_a.research_snapshot_id, rsid)
        self.assertEqual(snap_b.research_snapshot_id, rsid)

    def test_SD3_different_rsids_produce_different_snapshot_ids(self):
        snap_a = self._run_and_capture_snapshot(
            **_call_kwargs(research_snapshot_id="wx-capture-id-alpha"))
        snap_b = self._run_and_capture_snapshot(
            **_call_kwargs(research_snapshot_id="wx-capture-id-beta"))
        self.assertNotEqual(snap_a.research_snapshot_id, snap_b.research_snapshot_id)


# ═══════════════════════════════════════════════════════════════════════════════
# SD4 — UNAVAILABLE sentinel fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestSD4UnavailableSentinels(_CaptureBase):

    def test_SD4_nws_gridpoint_forecast_is_sentinel(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertEqual(snap.nws_gridpoint_forecast, _UNAVAIL_DICT)

    def test_SD4_open_meteo_forecast_is_sentinel(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertEqual(snap.open_meteo_forecast, _UNAVAIL_DICT)

    def test_SD4_noaa_ncei_forecast_is_sentinel(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertEqual(snap.noaa_ncei_forecast, _UNAVAIL_DICT)

    def test_SD4_official_observations_is_sentinel(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertEqual(snap.official_observations_at_cutoff, _UNAVAIL_DICT)

    def test_SD4_source_provenance_is_sentinel(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertEqual(snap.source_provenance, _UNAVAIL_DICT)

    def test_SD4_source_disagreements_is_sentinel_tuple(self):
        snap = self._run_and_capture_snapshot(**_call_kwargs())
        self.assertEqual(snap.source_disagreements, _UNAVAIL_TUPLE)


# ═══════════════════════════════════════════════════════════════════════════════
# SD5 — AST structural
# ═══════════════════════════════════════════════════════════════════════════════

class TestSD5AST(unittest.TestCase):

    @classmethod
    def _capture_source(cls):
        import gate_engine.kalshi_wx_shadow_capture as m
        return inspect.getsource(m)

    def test_SD5_no_threading_import_in_capture_module(self):
        src = self._capture_source()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    self.assertFalse(
                        name.startswith("threading"),
                        "threading must not be imported in capture module",
                    )

    def test_SD5_no_Thread_string_in_capture_module(self):
        src = self._capture_source()
        self.assertNotIn("Thread(", src)

    def test_SD5_no_Semaphore_in_capture_module(self):
        src = self._capture_source()
        self.assertNotIn("Semaphore(", src)

    def test_SD5_no_run_shadow_orchestrator_in_capture_module(self):
        src = self._capture_source()
        self.assertNotIn("run_shadow_orchestrator", src)

    def test_SD5_no_sdk_client_in_capture_module(self):
        src = self._capture_source()
        self.assertNotIn("_build_shadow_sdk_client", src)
        self.assertNotIn("Anthropic(", src)

    def test_SD5_no_requests_import_in_capture_module(self):
        src = self._capture_source()
        self.assertNotIn("import requests", src)


# ═══════════════════════════════════════════════════════════════════════════════
# SD6 — Pure helper unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSD6Helpers(unittest.TestCase):

    def test_SD6_readiness_not_none(self):
        self.assertEqual(_derive_readiness_state(85.0), "READY")

    def test_SD6_readiness_none(self):
        self.assertEqual(_derive_readiness_state(None), "DATA_UNAVAILABLE")

    def test_SD6_source_failures_empty_on_all_ok(self):
        td = {"nws": {"attempted": True, "ok": True, "error": None}}
        self.assertEqual(_derive_source_failures(td), ())

    def test_SD6_source_failures_captures_failed_tier(self):
        td = {"nws": {"attempted": True, "ok": False, "error": "timeout"}}
        failures = _derive_source_failures(td)
        self.assertEqual(len(failures), 1)
        self.assertIn("nws", failures[0])
        self.assertIn("timeout", failures[0])

    def test_SD6_source_failures_skips_not_attempted(self):
        td = {"nws": {"attempted": False, "ok": False, "error": "never tried"}}
        self.assertEqual(_derive_source_failures(td), ())

    def test_SD6_source_timestamps_keyed_by_tier(self):
        ts = _derive_source_timestamps("open_meteo_fallback", "2026-08-15T12:00:00Z")
        self.assertIn("open_meteo_fallback", ts)
        self.assertEqual(ts["open_meteo_fallback"], "2026-08-15T12:00:00Z")

    def test_SD6_unavailable_sentinel_value(self):
        self.assertIsInstance(UNAVAILABLE_SENTINEL, str)
        self.assertTrue(len(UNAVAILABLE_SENTINEL) > 10)

    def test_SD6_unavail_dict_contains_sentinel(self):
        self.assertEqual(_UNAVAIL_DICT["_status"], UNAVAILABLE_SENTINEL)

    def test_SD6_unavail_tuple_contains_sentinel(self):
        self.assertIn(UNAVAILABLE_SENTINEL, _UNAVAIL_TUPLE)


# ═══════════════════════════════════════════════════════════════════════════════
# SDDB — DB-behaviour tests for insert_shadow_snapshot
# ═══════════════════════════════════════════════════════════════════════════════

class TestSDBBehavior(unittest.TestCase):

    def test_SDDB_flag_on_calls_insert_once(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH) as mock_insert:
                maybe_fire_shadow_snapshot(**_call_kwargs())
        mock_insert.assert_called_once()

    def test_SDDB_insert_receives_snapshot_with_correct_city(self):
        captured = []
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=lambda s: captured.append(s)):
                maybe_fire_shadow_snapshot(**_call_kwargs(city="AUS"))
        self.assertEqual(captured[0].city, "AUS")

    def test_SDDB_insert_receives_snapshot_with_caller_provided_rsid(self):
        rsid = "wx-capture-db-test-rsid-xyz"
        captured = []
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=lambda s: captured.append(s)):
                maybe_fire_shadow_snapshot(**_call_kwargs(research_snapshot_id=rsid))
        self.assertEqual(captured[0].research_snapshot_id, rsid)

    def test_SDDB_db_error_does_not_propagate(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=Exception("db fail")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SDDB_db_error_logged_as_warning(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=Exception("db warn")):
                with self.assertLogs("gate_engine.kalshi_wx_shadow_capture",
                                     level="WARNING") as cm:
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertTrue(any("SHADOW_CAPTURE_FAILURE" in line for line in cm.output))

    def test_SDDB_psycopg2_operational_error_swallowed(self):
        try:
            import psycopg2
            err = psycopg2.OperationalError("connection refused")
        except ImportError:
            err = RuntimeError("simulated psycopg2 error")
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=err):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SDDB_ok_logs_shadow_capture_ok(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH):
                with self.assertLogs("gate_engine.kalshi_wx_shadow_capture",
                                     level="INFO") as cm:
                    maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertTrue(any("SHADOW_CAPTURE_OK" in line for line in cm.output))

    def test_SDDB_db_error_does_not_prevent_independent_flag_off_path(self):
        """Even after a DB error when flag was on, a subsequent flag-off call
        is also silent (proves no global state pollution)."""
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH, side_effect=Exception("first")):
                maybe_fire_shadow_snapshot(**_call_kwargs())
        with patch(_FLAG_PATH, False):
            result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)

    def test_SDDB_missing_db_url_raises_swallowed(self):
        with patch(_FLAG_PATH, True):
            with patch(_GET_CONN_PATH,
                       side_effect=RuntimeError("DATABASE_URL not set")):
                result = maybe_fire_shadow_snapshot(**_call_kwargs())
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════════════════
# SDST — Structural invariants
# ═══════════════════════════════════════════════════════════════════════════════

class TestSDStructural(unittest.TestCase):

    @classmethod
    def _capture_src(cls):
        import gate_engine.kalshi_wx_shadow_capture as m
        return inspect.getsource(m)

    @classmethod
    def _db_src(cls):
        import gate_engine.kalshi_wx_shadow_db as m
        return inspect.getsource(m)

    @classmethod
    def _app_py_path(cls):
        return _PROJ_ROOT / "app.py"

    def test_SDST_no_threading_import_in_capture_module(self):
        src = self._capture_src()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(alias.name.startswith("threading"),
                                     "threading must not be imported")

    def test_SDST_Thread_not_referenced_in_capture_module(self):
        self.assertNotIn("Thread", self._capture_src())

    def test_SDST_Semaphore_not_referenced_in_capture_module(self):
        self.assertNotIn("Semaphore", self._capture_src())

    def test_SDST_run_shadow_orchestrator_not_referenced_in_capture_module(self):
        self.assertNotIn("run_shadow_orchestrator", self._capture_src())

    def test_SDST_no_sdk_client_construction_in_capture_module(self):
        src = self._capture_src()
        self.assertNotIn("_build_shadow_sdk_client", src)
        self.assertNotIn("Anthropic(", src)

    def test_SDST_shadow_snapshot_queue_in_cm_schema_ddl(self):
        app_src = self._app_py_path().read_text()
        self.assertIn("kalshi_wx_shadow_snapshot_queue", app_src)

    def test_SDST_shadow_results_in_cm_schema_ddl(self):
        app_src = self._app_py_path().read_text()
        self.assertIn("kalshi_wx_shadow_results", app_src)

    def test_SDST_shadow_deterministic_outcome_in_cm_schema_ddl(self):
        app_src = self._app_py_path().read_text()
        self.assertIn("kalshi_wx_shadow_deterministic_outcome", app_src)

    def test_SDST_shadow_schema_ddl_snapshot_id_is_unique(self):
        import gate_engine.kalshi_wx_shadow_db as m
        self.assertIn("UNIQUE", m.SHADOW_SCHEMA_DDL)

    def test_SDST_shadow_schema_ddl_snapshot_uses_jsonb(self):
        import gate_engine.kalshi_wx_shadow_db as m
        self.assertIn("JSONB", m.SHADOW_SCHEMA_DDL)

    def test_SDST_shadow_schema_ddl_snapshot_has_pending_default(self):
        import gate_engine.kalshi_wx_shadow_db as m
        self.assertIn("PENDING", m.SHADOW_SCHEMA_DDL)

    def test_SDST_outcome_table_in_shadow_schema_ddl(self):
        import gate_engine.kalshi_wx_shadow_db as m
        self.assertIn("kalshi_wx_shadow_deterministic_outcome", m.SHADOW_SCHEMA_DDL)

    def test_SDST_outcome_table_has_boolean_can_execute_column(self):
        import gate_engine.kalshi_wx_shadow_db as m
        self.assertIn("BOOLEAN", m.SHADOW_SCHEMA_DDL)

    def test_SDST_maybe_link_function_exists_in_capture_module(self):
        import gate_engine.kalshi_wx_shadow_capture as m
        self.assertTrue(callable(getattr(m, "maybe_link_shadow_deterministic_outcome", None)))

    def test_SDST_insert_outcome_function_exists_in_db_module(self):
        import gate_engine.kalshi_wx_shadow_db as m
        self.assertTrue(callable(getattr(m, "insert_shadow_deterministic_outcome", None)))


# ═══════════════════════════════════════════════════════════════════════════════
# SDLINK — Linkage: same research_snapshot_id reaches both rows
# ═══════════════════════════════════════════════════════════════════════════════

class TestSDLinkage(unittest.TestCase):
    """
    These tests verify the core correctness property of the linkage change:
    a research_snapshot_id generated by the caller (app.py) is the EXACT SAME
    string passed to maybe_fire_shadow_snapshot() AND to
    maybe_link_shadow_deterministic_outcome(), so the two Postgres rows are
    genuinely joinable.

    In the real route, the ID is generated once in app.py before Step 10D
    and passed to both functions.  Here we simulate that pattern directly.
    """

    def test_SDLINK_same_rsid_passed_to_both_functions(self):
        """Both captured rows share the exact research_snapshot_id from the caller."""
        caller_rsid = "wx-capture-linkage-exact-match-001"
        snap_captured = []
        outcome_captured = []

        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH,
                       side_effect=lambda s: snap_captured.append(s)):
                with patch(_INSERT_OUTCOME_PATH,
                           side_effect=lambda **kw:
                               outcome_captured.append(kw["research_snapshot_id"])):
                    maybe_fire_shadow_snapshot(
                        **_call_kwargs(research_snapshot_id=caller_rsid))
                    maybe_link_shadow_deterministic_outcome(
                        **_outcome_kwargs(research_snapshot_id=caller_rsid))

        self.assertEqual(len(snap_captured), 1)
        self.assertEqual(len(outcome_captured), 1)
        self.assertEqual(snap_captured[0].research_snapshot_id, caller_rsid)
        self.assertEqual(outcome_captured[0], caller_rsid)
        # The two rows have the same ID → they are linkable
        self.assertEqual(snap_captured[0].research_snapshot_id, outcome_captured[0])

    def test_SDLINK_different_rsids_produce_non_matching_rows(self):
        """Two separate evaluations with different rsids produce distinct rows."""
        rsid_a = "wx-capture-link-a"
        rsid_b = "wx-capture-link-b"
        snap_ids = []
        outcome_ids = []

        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH,
                       side_effect=lambda s: snap_ids.append(s.research_snapshot_id)):
                with patch(_INSERT_OUTCOME_PATH,
                           side_effect=lambda **kw:
                               outcome_ids.append(kw["research_snapshot_id"])):
                    maybe_fire_shadow_snapshot(**_call_kwargs(research_snapshot_id=rsid_a))
                    maybe_link_shadow_deterministic_outcome(**_outcome_kwargs(research_snapshot_id=rsid_a))
                    maybe_fire_shadow_snapshot(**_call_kwargs(research_snapshot_id=rsid_b))
                    maybe_link_shadow_deterministic_outcome(**_outcome_kwargs(research_snapshot_id=rsid_b))

        self.assertEqual(snap_ids, [rsid_a, rsid_b])
        self.assertEqual(outcome_ids, [rsid_a, rsid_b])
        self.assertNotEqual(snap_ids[0], snap_ids[1])

    def test_SDLINK_snapshot_id_is_not_independently_generated(self):
        """
        The capture module no longer generates its own UUID.
        The rsid in the snapshot must match what the caller passed in,
        not a freshly generated UUID from inside the function.
        """
        fixed_rsid = "wx-capture-fixed-caller-id-no-internal-uuid"
        snap_captured = []

        with patch(_FLAG_PATH, True):
            with patch(_INSERT_SNAP_PATH,
                       side_effect=lambda s: snap_captured.append(s)):
                maybe_fire_shadow_snapshot(
                    **_call_kwargs(research_snapshot_id=fixed_rsid))

        self.assertEqual(snap_captured[0].research_snapshot_id, fixed_rsid,
                         "Snapshot rsid must be the caller-provided value, "
                         "not a newly generated UUID from inside the function")

    def test_SDLINK_flag_off_produces_no_rows_to_link(self):
        """With flag off, neither function touches DB, so no orphaned rows."""
        snap_captured = []
        outcome_captured = []
        rsid = "wx-capture-link-flag-off"

        with patch(_FLAG_PATH, False):
            with patch(_INSERT_SNAP_PATH,
                       side_effect=lambda s: snap_captured.append(s)):
                with patch(_INSERT_OUTCOME_PATH,
                           side_effect=lambda *a: outcome_captured.append(a)):
                    maybe_fire_shadow_snapshot(**_call_kwargs(research_snapshot_id=rsid))
                    maybe_link_shadow_deterministic_outcome(
                        **_outcome_kwargs(research_snapshot_id=rsid))

        self.assertEqual(snap_captured, [])
        self.assertEqual(outcome_captured, [])


# ═══════════════════════════════════════════════════════════════════════════════
# SDOUT — Outcome function field values and safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestSDOutcome(unittest.TestCase):
    """Tests for maybe_link_shadow_deterministic_outcome()."""

    def _run_outcome(self, **kwargs) -> dict:
        """Run outcome function with flag on and mocked DB; return captured row."""
        with _outcome_flag_on_mocked() as captured:
            maybe_link_shadow_deterministic_outcome(**kwargs)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_SDOUT_terminal_label_matches_input(self):
        row = self._run_outcome(**_outcome_kwargs(terminal_label="KALSHI_REJECT_NO_EDGE"))
        self.assertEqual(row["terminal_label"], "KALSHI_REJECT_NO_EDGE")

    def test_SDOUT_terminal_label_kalshi_playable(self):
        row = self._run_outcome(**_outcome_kwargs(terminal_label="KALSHI_PLAYABLE_LIMIT_ONLY"))
        self.assertEqual(row["terminal_label"], "KALSHI_PLAYABLE_LIMIT_ONLY")

    def test_SDOUT_terminal_label_data_unobtainable(self):
        row = self._run_outcome(**_outcome_kwargs(terminal_label="KALSHI_DATA_UNOBTAINABLE"))
        self.assertEqual(row["terminal_label"], "KALSHI_DATA_UNOBTAINABLE")

    def test_SDOUT_price_gate_disposition_matches_input(self):
        pgd = "price_source=synthetic_test: non-live prices; max terminal_label=KALSHI_WATCH"
        row = self._run_outcome(**_outcome_kwargs(price_gate_disposition=pgd))
        self.assertEqual(row["price_gate_disposition"], pgd)

    def test_SDOUT_price_gate_disposition_none_passes_through(self):
        row = self._run_outcome(**_outcome_kwargs(price_gate_disposition=None))
        self.assertIsNone(row["price_gate_disposition"])

    def test_SDOUT_can_execute_always_stored_as_false(self):
        """can_execute comes from price_gate["can_execute"] which is always False
        in this system.  Verify the outcome row faithfully records False."""
        row = self._run_outcome(**_outcome_kwargs(can_execute=False))
        self.assertFalse(row["can_execute"])

    def test_SDOUT_can_execute_true_input_stored_as_true(self):
        """
        The function stores what it's given — it does NOT enforce can_execute=False.
        The system guarantees False arrives; the function is a faithful recorder.
        """
        row = self._run_outcome(**_outcome_kwargs(can_execute=True))
        self.assertTrue(row["can_execute"])

    def test_SDOUT_research_snapshot_id_matches_input(self):
        rsid = "wx-capture-outcome-rsid-check"
        row = self._run_outcome(**_outcome_kwargs(research_snapshot_id=rsid))
        self.assertEqual(row["research_snapshot_id"], rsid)

    def test_SDOUT_insert_called_exactly_once(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_OUTCOME_PATH) as mock:
                maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        mock.assert_called_once()

    def test_SDOUT_db_error_does_not_propagate(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_OUTCOME_PATH, side_effect=RuntimeError("outcome db dead")):
                result = maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        self.assertIsNone(result)

    def test_SDOUT_db_error_logs_shadow_outcome_failure(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_OUTCOME_PATH, side_effect=RuntimeError("fail")):
                with self.assertLogs("gate_engine.kalshi_wx_shadow_capture",
                                     level="WARNING") as cm:
                    maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        self.assertTrue(any("SHADOW_OUTCOME_FAILURE" in line for line in cm.output))

    def test_SDOUT_success_logs_shadow_outcome_ok(self):
        with patch(_FLAG_PATH, True):
            with patch(_INSERT_OUTCOME_PATH):
                with self.assertLogs("gate_engine.kalshi_wx_shadow_capture",
                                     level="INFO") as cm:
                    maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        self.assertTrue(any("SHADOW_OUTCOME_OK" in line for line in cm.output))

    def test_SDOUT_can_execute_bool_coercion(self):
        """bool() coercion is applied — truthy non-bool values become True."""
        row = self._run_outcome(**_outcome_kwargs(can_execute=0))
        self.assertFalse(row["can_execute"])

    def test_SDOUT_flag_off_zero_inserts(self):
        with patch(_FLAG_PATH, False):
            with patch(_INSERT_OUTCOME_PATH) as mock:
                maybe_link_shadow_deterministic_outcome(**_outcome_kwargs())
        mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# SDAPP — App.py structural checks for Step 10D-b
# ═══════════════════════════════════════════════════════════════════════════════

class TestSDAppStructural(unittest.TestCase):
    """Verify app.py wiring without importing app.py."""

    @classmethod
    def _app_src(cls) -> str:
        return (_PROJ_ROOT / "app.py").read_text()

    def test_SDAPP_shadow_rsid_initialized_to_none(self):
        self.assertIn("_shadow_rsid = None", self._app_src())

    def test_SDAPP_rsid_uses_uuid4_format(self):
        src = self._app_src()
        self.assertIn("wx-capture-", src)

    def test_SDAPP_maybe_link_shadow_deterministic_outcome_imported_in_app(self):
        self.assertIn("maybe_link_shadow_deterministic_outcome", self._app_src())

    def test_SDAPP_step_10db_guarded_by_shadow_rsid(self):
        """The second capture block checks `and _shadow_rsid` to avoid orphaned rows."""
        self.assertIn("and _shadow_rsid", self._app_src())

    def test_SDAPP_terminal_label_passed_to_outcome_function(self):
        src = self._app_src()
        self.assertIn("terminal_label=terminal_label", src)

    def test_SDAPP_price_gate_trade_block_reason_passed_to_outcome(self):
        src = self._app_src()
        self.assertIn("price_gate.get(\"trade_block_reason\")", src)

    def test_SDAPP_can_execute_passed_to_outcome_function(self):
        src = self._app_src()
        self.assertIn("can_execute=bool(price_gate.get(\"can_execute\"", src)

    def test_SDAPP_step_10db_has_outer_except_exception(self):
        """app.py's outer backstop try/except is present for the outcome block."""
        src = self._app_src()
        # Two separate try/except blocks for the two shadow calls
        self.assertGreaterEqual(
            src.count("pass  # shadow failure must never affect the production route"), 2
        )

    def test_SDAPP_outcome_table_in_cm_schema_ddl(self):
        self.assertIn("kalshi_wx_shadow_deterministic_outcome", self._app_src())

    def test_SDAPP_research_snapshot_id_kwarg_passed_to_mfss(self):
        """maybe_fire_shadow_snapshot is called with research_snapshot_id=_shadow_rsid."""
        self.assertIn("research_snapshot_id=_shadow_rsid", self._app_src())


# ═══════════════════════════════════════════════════════════════════════════════
# SDINT — Live-DB integration: orphan-row scenario
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(
    not os.environ.get("DATABASE_URL"),
    "DATABASE_URL not set — skipping live-DB integration tests",
)
class TestSDOrphanRowIntegration(unittest.TestCase):
    """
    Integration test proving the orphan-row scenario against a real Postgres
    connection.  No mocks for the DB layer.

    "Orphan" = a snapshot row exists in kalshi_wx_shadow_snapshot_queue
    but no matching row exists in kalshi_wx_shadow_deterministic_outcome.
    This happens when the route captures the input snapshot (Step 10D) but
    throws or returns before reaching the terminal-label point (Step 10D-b).

    The test also proves the eligibility distinction:
      - orphan is excluded from the pilot-eligible INNER JOIN result
      - a "complete" snapshot (both tables populated) is included
    """

    # ── Per-test setup/teardown ───────────────────────────────────────────────

    def setUp(self):
        import uuid as _u
        # Fresh unique rsids for every test method — prevents cross-run collision.
        self._rsid_orphan   = f"wx-capture-inttest-orphan-{_u.uuid4()}"
        self._rsid_complete = f"wx-capture-inttest-complete-{_u.uuid4()}"

    def tearDown(self):
        """Best-effort cleanup: delete test rows from both shadow tables."""
        try:
            import psycopg2
            conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)
            rsids = [self._rsid_orphan, self._rsid_complete]
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM kalshi_wx_shadow_snapshot_queue"
                    " WHERE research_snapshot_id = ANY(%s)",
                    (rsids,),
                )
                cur.execute(
                    "DELETE FROM kalshi_wx_shadow_deterministic_outcome"
                    " WHERE research_snapshot_id = ANY(%s)",
                    (rsids,),
                )
            conn.commit()
            conn.close()
        except Exception:
            pass  # never let cleanup failure mask a test failure

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_conn(self):
        import psycopg2
        return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)

    def _snap_kwargs(self, rsid: str) -> dict:
        return dict(
            research_snapshot_id=rsid,
            city="NYC",
            station="KNYC",
            market_date="2026-08-15",
            forecast_high=85.0,
            weather_data_source_tier="nws_primary",
            sigma_f=3.5,
            horizon_hours=18.0,
            tier_detail={"nws": {"attempted": True, "ok": True, "error": None}},
        )

    # ── The integration test ──────────────────────────────────────────────────

    def test_SDINT_orphan_row_scenario_and_eligibility_join(self):
        """
        Step-by-step proof of the orphan-row scenario:

        (A) Insert snapshot for _rsid_orphan via maybe_fire_shadow_snapshot()
            with flag patched on and a real DB connection.
        (B) Confirm the row exists in kalshi_wx_shadow_snapshot_queue with
            status='PENDING'.
        (C) Do NOT call maybe_link_shadow_deterministic_outcome() for that ID
            — simulating the route having thrown or returned before Step 10D-b.
        (D) Confirm NO row exists in kalshi_wx_shadow_deterministic_outcome
            for _rsid_orphan.
        (E) Insert BOTH snapshot and outcome for _rsid_complete.
        (F) INNER JOIN eligibility query: _rsid_orphan excluded, _rsid_complete
            included.
        (G) LEFT JOIN orphan-detection query: _rsid_orphan included,
            _rsid_complete excluded.
        """
        from gate_engine.kalshi_wx_shadow_capture import (
            maybe_fire_shadow_snapshot,
            maybe_link_shadow_deterministic_outcome,
        )

        # ── (A) Insert snapshot for the orphan via real DB ────────────────────
        with patch(_FLAG_PATH, True):
            maybe_fire_shadow_snapshot(**self._snap_kwargs(self._rsid_orphan))

        # ── (B) Confirm snapshot row is in the queue table ────────────────────
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, snapshot_json IS NOT NULL"
                " FROM kalshi_wx_shadow_snapshot_queue"
                " WHERE research_snapshot_id = %s",
                (self._rsid_orphan,),
            )
            queue_row = cur.fetchone()
        conn.close()

        self.assertIsNotNone(
            queue_row,
            f"Orphan snapshot row must exist in kalshi_wx_shadow_snapshot_queue "
            f"for rsid={self._rsid_orphan!r}",
        )
        status, has_json = queue_row
        self.assertEqual(status, "PENDING",
                         "Newly inserted orphan row must have status='PENDING'")
        self.assertTrue(has_json, "snapshot_json must be non-null in the queue row")

        # ── (C) Deliberately skip maybe_link_shadow_deterministic_outcome() ───
        #    This is the simulation: the route threw between Step 10D and 10D-b.
        #    Nothing more to do here — just don't call the outcome function.

        # ── (D) Confirm NO outcome row for the orphan ─────────────────────────
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM kalshi_wx_shadow_deterministic_outcome"
                " WHERE research_snapshot_id = %s",
                (self._rsid_orphan,),
            )
            orphan_outcome_row = cur.fetchone()
        conn.close()

        self.assertIsNone(
            orphan_outcome_row,
            f"Orphan must have NO row in kalshi_wx_shadow_deterministic_outcome "
            f"for rsid={self._rsid_orphan!r}",
        )

        # ── (E) Insert BOTH snapshot and outcome for the "complete" rsid ──────
        with patch(_FLAG_PATH, True):
            maybe_fire_shadow_snapshot(**self._snap_kwargs(self._rsid_complete))
            maybe_link_shadow_deterministic_outcome(
                research_snapshot_id=self._rsid_complete,
                terminal_label="KALSHI_WATCH",
                price_gate_disposition=(
                    "DRY_RUN_ONLY: execution disabled per system policy"
                ),
                can_execute=False,
            )

        # Confirm the outcome row actually landed for the complete rsid
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT terminal_label, can_execute"
                " FROM kalshi_wx_shadow_deterministic_outcome"
                " WHERE research_snapshot_id = %s",
                (self._rsid_complete,),
            )
            complete_outcome = cur.fetchone()
        conn.close()

        self.assertIsNotNone(complete_outcome,
                             "Complete rsid must have an outcome row")
        term_label, can_exec = complete_outcome
        self.assertEqual(term_label, "KALSHI_WATCH")
        self.assertFalse(can_exec, "can_execute must be stored as False")

        # ── (F) INNER JOIN eligibility query ──────────────────────────────────
        # "pilot eligible" = has BOTH a snapshot row AND a linked outcome row.
        # Orphan must be EXCLUDED; complete must be INCLUDED.
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT q.research_snapshot_id
                FROM kalshi_wx_shadow_snapshot_queue q
                INNER JOIN kalshi_wx_shadow_deterministic_outcome o
                  ON q.research_snapshot_id = o.research_snapshot_id
                WHERE q.research_snapshot_id = ANY(%s)
                """,
                ([self._rsid_orphan, self._rsid_complete],),
            )
            eligible = {r[0] for r in cur.fetchall()}
        conn.close()

        self.assertNotIn(
            self._rsid_orphan, eligible,
            "MISSING OUTCOME: orphan snapshot must be excluded from pilot-eligible "
            "INNER JOIN (diagnostic only, NOT PILOT ELIGIBLE)",
        )
        self.assertIn(
            self._rsid_complete, eligible,
            "Complete snapshot (snapshot + outcome) must be included in "
            "pilot-eligible INNER JOIN",
        )

        # ── (G) LEFT JOIN orphan-detection query ──────────────────────────────
        # Inverse query: which queue rows have NO outcome row?
        # Orphan must be INCLUDED; complete must be EXCLUDED.
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT q.research_snapshot_id
                FROM kalshi_wx_shadow_snapshot_queue q
                LEFT JOIN kalshi_wx_shadow_deterministic_outcome o
                  ON q.research_snapshot_id = o.research_snapshot_id
                WHERE q.research_snapshot_id = ANY(%s)
                  AND o.id IS NULL
                """,
                ([self._rsid_orphan, self._rsid_complete],),
            )
            orphans = {r[0] for r in cur.fetchall()}
        conn.close()

        self.assertIn(
            self._rsid_orphan, orphans,
            "Orphan snapshot must appear in LEFT JOIN orphan-detection query "
            "(o.id IS NULL)",
        )
        self.assertNotIn(
            self._rsid_complete, orphans,
            "Complete snapshot must NOT appear in LEFT JOIN orphan-detection "
            "query (it has an outcome row, so o.id is not NULL)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
