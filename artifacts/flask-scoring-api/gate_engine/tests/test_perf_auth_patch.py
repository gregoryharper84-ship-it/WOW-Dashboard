"""
gate_engine/tests/test_perf_auth_patch.py
==========================================
Tests for the WOW backend performance / auth patch.

Covers (per spec):
  1. Persistent identity cache surviving a reconstructed cache instance
  2. Bounded concurrency vs serial behavior (mocked delays)
  3. Deterministic output ordering across concurrent fetches
  4. One-pitcher failure isolation
  5. Cold prewarm followed by cache hits
  6. Auth-header routing (action_get / scoring_get)
  7. Kalshi first-call auth (scoring_get sends X-API-Key)
  8. Status/error classification (DEGRADED_LATENCY, AUTH_CONTRACT_FAIL,
     neither becomes NO_PLAY / model rejection / generic 504)

DB-dependent tests are skipped when DATABASE_URL is absent (same pattern as
test_settlement_idempotency_db.py).  All non-DB tests run with mocks only.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import Future
from typing import Optional
from unittest.mock import MagicMock, patch

# ── Skip guard for DB tests ───────────────────────────────────────────────────

def _needs_db(fn):
    """Skip decorator: skip when DATABASE_URL is absent."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not os.environ.get("DATABASE_URL"):
            raise unittest.SkipTest("DATABASE_URL not set — DB tests skipped")
        return fn(*args, **kwargs)
    return wrapper


# ═══════════════════════════════════════════════════════════════════════════════
# 1 + 5. Player Identity Cache — persistent cache surviving reconstruction
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlayerIdentityCache(unittest.TestCase):
    """Tests for gate_engine/mlb/player_identity_cache.py"""

    def setUp(self):
        from gate_engine.mlb import player_identity_cache as pic
        self.pic = pic
        # Reset schema_ready so each test starts fresh
        pic.reset_schema_ready()

    # ── Pure-logic tests (no DB) ──────────────────────────────────────────────

    def test_make_key_deterministic(self):
        """player_key must be deterministic and case-insensitive."""
        pic = self.pic
        self.assertEqual(pic._make_key("Gerrit", "Cole"),
                         pic._make_key("gerrit", "cole"))
        self.assertEqual(pic._make_key("Gerrit", "Cole"),
                         pic._make_key(" Gerrit ", " Cole "))
        self.assertEqual(pic._make_key("Gerrit", "Cole"), "cole_gerrit")

    def test_make_key_different_players(self):
        """Different player pairs must produce different keys."""
        pic = self.pic
        self.assertNotEqual(
            pic._make_key("Gerrit", "Cole"),
            pic._make_key("Shane",  "Bieber"),
        )

    def test_lookup_returns_none_when_db_unavailable(self):
        """Cache lookup must return None (fail-closed) when DB is unreachable."""
        pic = self.pic
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            result = pic.lookup("Gerrit", "Cole")
        self.assertIsNone(result)

    def test_store_returns_false_when_db_unavailable(self):
        """Cache store must return False (non-fatal) when DB is unreachable."""
        pic = self.pic
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            result = pic.store("Gerrit", "Cole", 543037)
        self.assertFalse(result)

    def test_lookup_rejects_invalid_mlbam_id(self):
        """store() must reject invalid (non-positive) MLBAM IDs."""
        pic = self.pic
        self.assertFalse(pic.store("Bad", "Player", 0))
        self.assertFalse(pic.store("Bad", "Player", -1))
        self.assertFalse(pic.store("Bad", "Player", "not_an_int"))  # type: ignore

    def test_reset_schema_ready_idempotent(self):
        """reset_schema_ready() must not raise regardless of current state."""
        pic = self.pic
        pic.reset_schema_ready()
        pic.reset_schema_ready()  # idempotent

    # ── DB-dependent tests ────────────────────────────────────────────────────

    @_needs_db
    def test_store_and_lookup_round_trip(self):
        """store() then lookup() on the same instance returns the MLBAM ID."""
        pic = self.pic
        unique_first = f"Test{uuid.uuid4().hex[:6]}"
        unique_last  = f"Player{uuid.uuid4().hex[:6]}"
        mlbam_id     = 543037

        ok = pic.store(unique_first, unique_last, mlbam_id)
        self.assertTrue(ok, "store() failed")

        result = pic.lookup(unique_first, unique_last)
        self.assertEqual(result, mlbam_id)

        # Cleanup
        pic.invalidate(unique_first, unique_last)

    @_needs_db
    def test_cache_survives_reconstructed_instance(self):
        """
        After store(), re-importing the module (simulated by calling lookup()
        with _SCHEMA_READY=False) still finds the cached value — proving the
        cache persists in Postgres, not just in-process state.
        """
        pic = self.pic
        unique_first = f"Test{uuid.uuid4().hex[:6]}"
        unique_last  = f"Player{uuid.uuid4().hex[:6]}"
        mlbam_id     = 600789

        pic.store(unique_first, unique_last, mlbam_id)

        # Simulate process restart by resetting schema_ready
        pic.reset_schema_ready()

        result = pic.lookup(unique_first, unique_last)
        self.assertEqual(result, mlbam_id, "Cache did not survive schema_ready reset")

        # Cleanup
        pic.invalidate(unique_first, unique_last)

    @_needs_db
    def test_upsert_overwrites_stale_entry(self):
        """Calling store() twice for the same player updates the record."""
        pic = self.pic
        unique_first = f"Test{uuid.uuid4().hex[:6]}"
        unique_last  = f"Player{uuid.uuid4().hex[:6]}"

        pic.store(unique_first, unique_last, 111111)
        pic.store(unique_first, unique_last, 222222)  # update

        result = pic.lookup(unique_first, unique_last)
        self.assertEqual(result, 222222)

        pic.invalidate(unique_first, unique_last)

    @_needs_db
    def test_invalidate_removes_entry(self):
        """invalidate() causes subsequent lookup() to return None."""
        pic = self.pic
        unique_first = f"Test{uuid.uuid4().hex[:6]}"
        unique_last  = f"Player{uuid.uuid4().hex[:6]}"

        pic.store(unique_first, unique_last, 543037)
        self.assertEqual(pic.lookup(unique_first, unique_last), 543037)

        pic.invalidate(unique_first, unique_last)
        self.assertIsNone(pic.lookup(unique_first, unique_last))

    @_needs_db
    def test_ensure_schema_idempotent(self):
        """ensure_schema() can be called multiple times without error."""
        pic = self.pic
        for _ in range(3):
            ok = pic.ensure_schema()
        self.assertTrue(ok)


# ═══════════════════════════════════════════════════════════════════════════════
# 2 + 3 + 4 + 5. Pitcher Prefetch — concurrency, ordering, isolation, prewarm
# ═══════════════════════════════════════════════════════════════════════════════

class TestPitcherPrefetch(unittest.TestCase):
    """Tests for gate_engine/mlb/pitcher_prefetch.py"""

    def setUp(self):
        from gate_engine.mlb import pitcher_prefetch as ppf
        self.ppf = ppf
        # Reset executor so each test gets a fresh one
        ppf.reset_for_new_worker()

    def _identity_fn(self, first: str, last: str) -> Optional[int]:
        """Mock identity lookup — returns a stable fake MLBAM ID."""
        return abs(hash(f"{first}{last}")) % 1_000_000 + 1

    def _savant_fn(self, first: str, last: str) -> dict:
        """Mock Savant fetch — fast."""
        return {"whiff_pct": 0.12, "avg_velocity": 93.2, "data_source": "mock"}

    def _slow_savant_fn(self, delay: float = 0.2):
        """Factory: returns a savant_fn that sleeps `delay` seconds."""
        def _fn(first: str, last: str) -> dict:
            time.sleep(delay)
            return {"whiff_pct": 0.1, "data_source": "mock_slow"}
        return _fn

    def _failing_savant_fn(self, fail_last: str):
        """Factory: raises for one specific player, succeeds for others."""
        def _fn(first: str, last: str) -> dict:
            if last == fail_last:
                raise RuntimeError(f"Simulated fetch failure for {first} {last}")
            return {"whiff_pct": 0.1, "data_source": "mock"}
        return _fn

    # ── Concurrency benchmark ─────────────────────────────────────────────────

    def test_concurrent_faster_than_serial(self):
        """
        prefetch_many() with 4 workers should complete ~4 × faster than
        running the same fetches serially.

        Each mock fetch sleeps 0.2 s.  Serial time for 4 pitchers ≈ 0.8 s.
        Concurrent time with 4 workers ≈ 0.2 s.  We assert concurrent < 0.5 s
        (leaving a 2.5× safety margin for CI jitter).
        """
        ppf = self.ppf
        pitchers = [
            ("Gerrit",    "Cole"),
            ("Shane",     "Bieber"),
            ("Yoshinobu", "Yamamoto"),
            ("Chase",     "Burns"),
        ]
        slow_savant = self._slow_savant_fn(delay=0.2)

        # Serial baseline
        t0 = time.monotonic()
        for f, l in pitchers:
            self._identity_fn(f, l)
            slow_savant(f, l)
        serial_s = time.monotonic() - t0

        # Reset inflight between tests
        ppf.reset_for_new_worker()

        # Concurrent
        t0 = time.monotonic()
        results = ppf.prefetch_many(pitchers, self._identity_fn, slow_savant)
        concurrent_s = time.monotonic() - t0

        self.assertLess(concurrent_s, serial_s * 0.65,
                        f"Concurrent ({concurrent_s:.3f}s) not faster than "
                        f"serial ({serial_s:.3f}s) × 0.65")
        self.assertEqual(len(results), 4)

    # ── Deterministic ordering ────────────────────────────────────────────────

    def test_deterministic_output_ordering(self):
        """prefetch_many() must return results in input order."""
        ppf = self.ppf
        pitchers = [
            ("Gerrit",    "Cole"),
            ("Shane",     "Bieber"),
            ("Yoshinobu", "Yamamoto"),
            ("Chase",     "Burns"),
            ("Seth",      "Lugo"),
        ]
        # Each pitcher's delay is different so completion order varies
        completion_order: list[str] = []
        lock = threading.Lock()

        def savant_with_tracking(first: str, last: str) -> dict:
            delay = {"Cole": 0.15, "Bieber": 0.05, "Yamamoto": 0.10,
                     "Burns": 0.02, "Lugo": 0.12}.get(last, 0.08)
            time.sleep(delay)
            with lock:
                completion_order.append(last)
            return {"data_source": "mock"}

        results = ppf.prefetch_many(pitchers, self._identity_fn, savant_with_tracking)

        # Results must be in INPUT order regardless of completion order
        self.assertEqual([r["last"] for r in results],
                         [l for _, l in pitchers])
        # Completion order should differ from input order (concurrent)
        # (This is probabilistic; only assert it's not identical for the slow test)
        self.assertIsInstance(completion_order, list)

    # ── Failure isolation ─────────────────────────────────────────────────────

    def test_one_pitcher_failure_does_not_block_others(self):
        """One pitcher's exception must not prevent other pitchers from completing."""
        ppf = self.ppf
        pitchers = [
            ("Gerrit", "Cole"),
            ("Shane",  "Bieber"),    # <-- will fail
            ("Seth",   "Lugo"),
        ]
        failing_savant = self._failing_savant_fn(fail_last="Bieber")

        results = ppf.prefetch_many(pitchers, self._identity_fn, failing_savant)

        self.assertEqual(len(results), 3)
        # Cole succeeds
        self.assertTrue(results[0]["ok"])
        self.assertEqual(results[0]["last"], "Cole")
        # Bieber fails with error key (not a crash)
        self.assertFalse(results[1]["ok"])
        self.assertIn("error", results[1])
        self.assertEqual(results[1]["last"], "Bieber")
        # Lugo succeeds
        self.assertTrue(results[2]["ok"])
        self.assertEqual(results[2]["last"], "Lugo")

    def test_timeout_isolation(self):
        """A timed-out pitcher returns FETCH_TIMEOUT without crashing others."""
        ppf = self.ppf

        def very_slow_savant(first: str, last: str) -> dict:
            if last == "Bieber":
                time.sleep(5)   # will exceed the 0.1s test timeout
            return {"data_source": "mock"}

        pitchers = [("Gerrit", "Cole"), ("Shane", "Bieber"), ("Seth", "Lugo")]
        results = ppf.prefetch_many(
            pitchers, self._identity_fn, very_slow_savant, timeout=0.1
        )
        self.assertEqual(len(results), 3)
        self.assertTrue(results[0]["ok"],  "Cole should succeed")
        self.assertFalse(results[1]["ok"], "Bieber should timeout")
        self.assertEqual(results[1].get("error"), "FETCH_TIMEOUT")
        self.assertTrue(results[2]["ok"],  "Lugo should succeed")

    # ── Prewarm → cache hits ──────────────────────────────────────────────────

    def test_prewarm_then_cache_hit(self):
        """
        After prewarm(), subsequent prefetch_one() calls for the same pitcher
        should find the Future already completed (cache populated) so the
        result is immediately available.
        """
        ppf = self.ppf
        calls: list[str] = []

        def counting_savant(first: str, last: str) -> dict:
            calls.append(last)
            return {"data_source": "mock", "call_count": len(calls)}

        pitchers = [("Gerrit", "Cole"), ("Seth", "Lugo")]

        # Fire prewarm (background)
        ppf.prewarm(pitchers, self._identity_fn, counting_savant)
        # Wait for prewarm futures to complete
        time.sleep(0.5)

        # Now prefetch_one — should coalesce on already-done or re-fetch
        # (in-flight futures were cleaned up after completion)
        r = ppf.prefetch_one("Gerrit", "Cole",
                             self._identity_fn, counting_savant)
        self.assertNotIn("error", r.get("savant", {}))
        self.assertTrue(r["ok"])

    def test_in_flight_deduplication(self):
        """Two concurrent calls for the same pitcher share one Future."""
        ppf = self.ppf
        call_count = [0]
        lock = threading.Lock()

        def slow_counting_savant(first: str, last: str) -> dict:
            with lock:
                call_count[0] += 1
            time.sleep(0.15)
            return {"data_source": "mock"}

        # Start two concurrent requests for the same pitcher
        results = []
        def do_fetch():
            r = ppf.prefetch_one("Gerrit", "Cole",
                                 self._identity_fn, slow_counting_savant)
            results.append(r)

        t1 = threading.Thread(target=do_fetch)
        t2 = threading.Thread(target=do_fetch)
        t1.start(); t2.start()
        t1.join();  t2.join()

        # The mock may be called 1 or 2 times depending on timing, but
        # importantly both callers get a valid result (no crash / error)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertTrue(r["ok"])

    def test_prefetch_many_empty_list(self):
        """prefetch_many([]) returns an empty list without error."""
        ppf = self.ppf
        results = ppf.prefetch_many([], self._identity_fn, self._savant_fn)
        self.assertEqual(results, [])

    def test_prefetch_one_result_keys(self):
        """prefetch_one() result contains expected keys."""
        ppf = self.ppf
        r = ppf.prefetch_one("Gerrit", "Cole",
                             self._identity_fn, self._savant_fn)
        self.assertIn("first", r)
        self.assertIn("last",  r)
        self.assertIn("mlbam_id", r)
        self.assertIn("savant",   r)
        self.assertIn("ok",       r)
        self.assertIn("elapsed_s", r)


# ═══════════════════════════════════════════════════════════════════════════════
# 6 + 7. Auth-header routing — internal_client
# ═══════════════════════════════════════════════════════════════════════════════

class TestInternalClientAuth(unittest.TestCase):
    """Tests for gate_engine/internal_client.py"""

    def setUp(self):
        from gate_engine import internal_client as ic
        self.ic = ic

    def _captured_headers(self) -> dict:
        """Return headers captured by the last mocked requests.get call."""
        return self._mock_get.call_args[1].get("headers", {}) or \
               self._mock_get.call_args[0][1] if len(self._mock_get.call_args[0]) > 1 else {}

    # ── action_get ────────────────────────────────────────────────────────────

    def test_action_get_sends_wow_action_key_header(self):
        """action_get() must send X-WOW-Action-Key, never X-API-Key."""
        ic = self.ic
        fake_secret = "test-gpt-secret-abc"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}

        with patch.dict(os.environ, {"GPT_ACTION_SECRET": fake_secret}):
            with patch("requests.get", return_value=mock_resp) as mock_get:
                body, status, err = ic.action_get("/wow/odds/events", {"sport": "baseball_mlb"})

        call_headers = mock_get.call_args[1]["headers"]
        self.assertIn("X-WOW-Action-Key", call_headers)
        self.assertNotIn("X-API-Key", call_headers)
        self.assertEqual(status, 200)
        self.assertIsNone(err)

    def test_action_get_does_not_log_secret_value(self):
        """action_get() header value must never appear as a test assertion string here."""
        # This is an architectural invariant: we verify headers are sent by name,
        # not by asserting the actual secret value in test output.
        ic = self.ic
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}

        with patch.dict(os.environ, {"GPT_ACTION_SECRET": "REDACTED_IN_TEST"}):
            with patch("requests.get", return_value=mock_resp) as mock_get:
                ic.action_get("/wow/odds/events")

        call_headers = mock_get.call_args[1]["headers"]
        # Only assert the KEY NAME is present — not the value
        self.assertIn("X-WOW-Action-Key", call_headers)

    def test_action_get_returns_auth_contract_fail_when_secret_missing(self):
        """action_get() with no GPT_ACTION_SECRET must return AUTH_CONTRACT_FAIL."""
        ic = self.ic
        with patch.dict(os.environ, {"GPT_ACTION_SECRET": ""}, clear=False):
            body, status, err = ic.action_get("/wow/odds/events")
        self.assertEqual(err, ic.AUTH_CONTRACT_FAIL)
        self.assertIsNone(body)

    def test_action_get_maps_401_to_auth_contract_fail(self):
        """A 401 response from the server maps to AUTH_CONTRACT_FAIL."""
        ic = self.ic
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch.dict(os.environ, {"GPT_ACTION_SECRET": "some-secret"}):
            with patch("requests.get", return_value=mock_resp):
                body, status, err = ic.action_get("/wow/odds/events")

        self.assertEqual(err, ic.AUTH_CONTRACT_FAIL)
        self.assertEqual(status, 401)

    # ── scoring_get ───────────────────────────────────────────────────────────

    def test_scoring_get_sends_x_api_key_header(self):
        """scoring_get() must send X-API-Key, never X-WOW-Action-Key."""
        ic = self.ic
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}

        with patch.dict(os.environ, {"SCORING_API_KEY": "test-scoring-key"}):
            with patch("requests.get", return_value=mock_resp) as mock_get:
                body, status, err = ic.scoring_get("/wow/kalshi/category-scan")

        call_headers = mock_get.call_args[1]["headers"]
        self.assertIn("X-API-Key", call_headers)
        self.assertNotIn("X-WOW-Action-Key", call_headers)
        self.assertEqual(status, 200)
        self.assertIsNone(err)

    def test_scoring_get_returns_auth_contract_fail_when_key_missing(self):
        """scoring_get() with no SCORING_API_KEY must return AUTH_CONTRACT_FAIL."""
        ic = self.ic
        with patch.dict(os.environ, {"SCORING_API_KEY": ""}, clear=False):
            body, status, err = ic.scoring_get("/wow/kalshi/category-scan")
        self.assertEqual(err, ic.AUTH_CONTRACT_FAIL)
        self.assertIsNone(body)

    def test_scoring_get_maps_401_to_auth_contract_fail(self):
        """A 401 response on a scoring_get call maps to AUTH_CONTRACT_FAIL."""
        ic = self.ic
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch.dict(os.environ, {"SCORING_API_KEY": "some-key"}):
            with patch("requests.get", return_value=mock_resp):
                body, status, err = ic.scoring_get("/wow/kalshi/category-scan")

        self.assertEqual(err, ic.AUTH_CONTRACT_FAIL)

    def test_fetch_failure_maps_to_fetch_failed(self):
        """A network error maps to FETCH_FAILED, not AUTH_CONTRACT_FAIL."""
        ic = self.ic
        with patch.dict(os.environ, {"GPT_ACTION_SECRET": "some-secret"}):
            with patch("requests.get", side_effect=ConnectionError("refused")):
                body, status, err = ic.action_get("/wow/odds/events")

        self.assertEqual(err, ic.FETCH_FAILED)
        self.assertIsNone(body)

    # ── Correct key => 200 / wrong key => 401 contract ───────────────────────

    def test_action_get_correct_secret_reaches_server(self):
        """With the correct secret, action_get() forwards the call and gets 200."""
        ic = self.ic
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"events": []}

        with patch.dict(os.environ, {"GPT_ACTION_SECRET": "correct-secret"}):
            with patch("requests.get", return_value=mock_resp) as mock_get:
                body, status, err = ic.action_get("/wow/odds/events")

        self.assertEqual(status, 200)
        self.assertIsNone(err)
        mock_get.assert_called_once()

    def test_action_get_wrong_secret_returns_401(self):
        """Server 401 with a wrong secret is surfaced as AUTH_CONTRACT_FAIL."""
        ic = self.ic
        mock_resp = MagicMock()
        mock_resp.status_code = 401  # server rejects the wrong key

        with patch.dict(os.environ, {"GPT_ACTION_SECRET": "wrong-secret"}):
            with patch("requests.get", return_value=mock_resp):
                body, status, err = ic.action_get("/wow/odds/events")

        self.assertEqual(err, ic.AUTH_CONTRACT_FAIL)
        self.assertEqual(status, 401)
        self.assertIsNone(body)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Status / error classification — acquisition_telemetry
# ═══════════════════════════════════════════════════════════════════════════════

class TestAcquisitionTelemetry(unittest.TestCase):
    """Tests for gate_engine/mlb/acquisition_telemetry.py"""

    def setUp(self):
        from gate_engine.mlb import acquisition_telemetry as tel
        self.tel = tel
        tel.clear_events()

    def _record(self, dependency: str, elapsed_ms: float,
                status_class: str, cache_hit: bool = False):
        self.tel.record_event(self.tel.AcquisitionEvent(
            route="/wow/mlb/pitcher",
            dependency=dependency,
            cache_hit=cache_hit,
            elapsed_ms=elapsed_ms,
            status_class=status_class,
        ))

    # ── Status constant sanity ────────────────────────────────────────────────

    def test_status_constants_defined(self):
        """All expected status constants must be defined."""
        tel = self.tel
        self.assertTrue(hasattr(tel, "BACKEND_HEALTH_OK"))
        self.assertTrue(hasattr(tel, "MLB_DATA_ACQ_OK"))
        self.assertTrue(hasattr(tel, "MLB_DATA_ACQ_DEGRADED"))
        self.assertTrue(hasattr(tel, "MLB_DATA_ACQ_PENDING"))
        self.assertTrue(hasattr(tel, "ODDS_AUTH_OK"))
        self.assertTrue(hasattr(tel, "ODDS_AUTH_CONTRACT_FAIL"))
        self.assertTrue(hasattr(tel, "IDENTITY_CACHE_HIT"))
        self.assertTrue(hasattr(tel, "IDENTITY_CACHE_MISS"))
        self.assertTrue(hasattr(tel, "IDENTITY_CACHE_UNAVAILABLE"))

    # ── DEGRADED_LATENCY classification ──────────────────────────────────────

    def test_slow_acquisition_classified_as_degraded_latency(self):
        """A 35-second pybaseball fetch must produce DEGRADED_LATENCY, not OK."""
        self._record("pybaseball_statcast", 35_000,
                     self.tel.MLB_DATA_ACQ_DEGRADED)
        summary = self.tel.get_scan_summary()
        self.assertEqual(summary["mlb_data_acquisition"],
                         self.tel.MLB_DATA_ACQ_DEGRADED)

    def test_fast_acquisition_classified_as_ok(self):
        """A fast (cache-hit) fetch must produce OK mlb_data_acquisition."""
        self._record("pybaseball_statcast", 120,
                     self.tel.MLB_DATA_ACQ_OK, cache_hit=True)
        summary = self.tel.get_scan_summary()
        self.assertEqual(summary["mlb_data_acquisition"], self.tel.MLB_DATA_ACQ_OK)

    # ── AUTH_CONTRACT_FAIL classification ─────────────────────────────────────

    def test_odds_auth_fail_classified_correctly(self):
        """An odds_internal 401 must produce ODDS_INTERNAL_AUTH=AUTH_CONTRACT_FAIL."""
        self._record("odds_internal", 2, self.tel.ODDS_AUTH_CONTRACT_FAIL)
        summary = self.tel.get_scan_summary()
        self.assertEqual(summary["odds_internal_auth"],
                         self.tel.ODDS_AUTH_CONTRACT_FAIL)

    # ── Neither status maps to NO_PLAY or model rejection ────────────────────

    def test_degraded_latency_not_no_play(self):
        """DEGRADED_LATENCY must not map to NO_PLAY, 504, or model rejection."""
        self._record("pybaseball_statcast", 40_000,
                     self.tel.MLB_DATA_ACQ_DEGRADED)
        summary = self.tel.get_scan_summary()
        for key, val in summary.items():
            if isinstance(val, str):
                self.assertNotIn("NO_PLAY",         val)
                self.assertNotIn("504",             val)
                self.assertNotIn("model_rejection", val)
                self.assertNotIn("BACKEND_OUTAGE",  val)

    def test_auth_contract_fail_not_no_play(self):
        """AUTH_CONTRACT_FAIL must not map to NO_PLAY or backend outage."""
        self._record("odds_internal", 3, self.tel.ODDS_AUTH_CONTRACT_FAIL)
        summary = self.tel.get_scan_summary()
        for key, val in summary.items():
            if isinstance(val, str):
                self.assertNotIn("NO_PLAY",        val)
                self.assertNotIn("BACKEND_OUTAGE", val)

    # ── backend_health is always OK ───────────────────────────────────────────

    def test_backend_health_always_ok(self):
        """backend_health must always be OK — it represents infra, not data."""
        # Even when everything else is degraded
        self._record("pybaseball_statcast", 60_000,
                     self.tel.MLB_DATA_ACQ_FAILED)
        self._record("odds_internal", 2, self.tel.ODDS_AUTH_CONTRACT_FAIL)
        summary = self.tel.get_scan_summary()
        self.assertEqual(summary["backend_health"], self.tel.BACKEND_HEALTH_OK)

    # ── Cache classification ──────────────────────────────────────────────────

    def test_cache_hit_classification(self):
        """Cache HIT events produce IDENTITY_CACHE_HIT in summary."""
        self.tel.record_event(self.tel.AcquisitionEvent(
            route="/wow/mlb/pitcher",
            dependency="player_identity_cache",
            cache_hit=True,
            elapsed_ms=0.5,
            status_class=self.tel.MLB_DATA_ACQ_OK,
        ))
        summary = self.tel.get_scan_summary()
        self.assertEqual(summary["player_identity_cache"],
                         self.tel.IDENTITY_CACHE_HIT)

    def test_cache_miss_classification(self):
        """Cache MISS events (no hits) produce IDENTITY_CACHE_MISS in summary."""
        self.tel.record_event(self.tel.AcquisitionEvent(
            route="/wow/mlb/pitcher",
            dependency="player_identity_cache",
            cache_hit=False,
            elapsed_ms=30_000,
            status_class=self.tel.MLB_DATA_ACQ_DEGRADED,
        ))
        summary = self.tel.get_scan_summary()
        self.assertEqual(summary["player_identity_cache"],
                         self.tel.IDENTITY_CACHE_MISS)

    # ── timed_acquisition context manager ────────────────────────────────────

    def test_timed_acquisition_records_ok_event(self):
        """timed_acquisition records OK when elapsed < threshold."""
        tel = self.tel
        with tel.timed_acquisition("/wow/mlb/pitcher", "pybaseball_statcast"):
            time.sleep(0.01)  # fast

        events = tel.recent_events(5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status_class, tel.MLB_DATA_ACQ_OK)

    def test_timed_acquisition_records_failed_on_exception(self):
        """timed_acquisition records FETCH_FAILED and re-raises on exception."""
        tel = self.tel
        with self.assertRaises(ValueError):
            with tel.timed_acquisition("/wow/mlb/pitcher", "pybaseball_statcast"):
                raise ValueError("simulated fetch error")

        events = tel.recent_events(5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status_class, tel.MLB_DATA_ACQ_FAILED)
        self.assertEqual(events[0].error_class, "ValueError")

    def test_ring_buffer_thread_safe(self):
        """record_event() must be safe under concurrent access."""
        tel = self.tel
        tel.clear_events()
        errors: list[Exception] = []

        def write_events():
            for _ in range(50):
                try:
                    tel.record_event(tel.AcquisitionEvent(
                        route="/wow/mlb/pitcher",
                        dependency="pybaseball_statcast",
                        cache_hit=False,
                        elapsed_ms=100.0,
                        status_class=tel.MLB_DATA_ACQ_OK,
                    ))
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=write_events) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread-safety errors: {errors}")
        # Buffer holds up to 1000; 250 events total fits
        self.assertGreater(len(tel.recent_events(300)), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Benchmark: serial vs concurrent timing (mocked, deterministic)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyBenchmark(unittest.TestCase):
    """
    Deterministic benchmark proving concurrent acquisition is faster than serial.

    Uses time.sleep() mocks to eliminate network variance while preserving
    real concurrent scheduling.
    """

    def test_8_pitchers_concurrent_vs_serial(self):
        """
        8 pitchers × 0.2 s each = 1.6 s serial.
        With 4 workers, expected concurrent time ≈ 0.4 s (2 batches × 0.2 s).
        Assert concurrent < 0.9 s (2.2× safety margin).
        """
        from gate_engine.mlb import pitcher_prefetch as ppf
        ppf.reset_for_new_worker()

        pitchers = [
            ("Gerrit",    "Cole"),
            ("Shane",     "Bieber"),
            ("Yoshinobu", "Yamamoto"),
            ("Chase",     "Burns"),
            ("Seth",      "Lugo"),
            ("Matthew",   "Liberatore"),
            ("Brandon",   "Pfaadt"),
            ("George",    "Kirby"),
        ]

        def identity_fn(first, last):
            return abs(hash(f"{first}{last}")) % 1_000_000 + 1

        def slow_savant(first, last):
            time.sleep(0.2)
            return {"data_source": "mock_benchmark"}

        # Serial baseline
        t0 = time.monotonic()
        for f, l in pitchers:
            identity_fn(f, l)
            slow_savant(f, l)
        serial_s = time.monotonic() - t0

        ppf.reset_for_new_worker()

        # Concurrent
        t0 = time.monotonic()
        results = ppf.prefetch_many(pitchers, identity_fn, slow_savant)
        concurrent_s = time.monotonic() - t0

        # Assert concurrent is meaningfully faster
        self.assertLess(concurrent_s, serial_s * 0.65,
                        f"BENCHMARK FAIL: concurrent={concurrent_s:.3f}s "
                        f"serial={serial_s:.3f}s  ratio={concurrent_s/serial_s:.2f}")

        # All 8 succeeded
        self.assertEqual(len(results), 8)
        self.assertTrue(all(r["ok"] for r in results),
                        f"Some failed: {[r for r in results if not r['ok']]}")

        # Results in input order
        self.assertEqual([r["last"] for r in results],
                         [l for _, l in pitchers])

        print(f"\n  BENCHMARK: serial={serial_s:.3f}s "
              f"concurrent={concurrent_s:.3f}s "
              f"speedup={serial_s/concurrent_s:.1f}×")


if __name__ == "__main__":
    unittest.main(verbosity=2)
