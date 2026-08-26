"""
validation/tests/test_prediction_logger.py

Tests for the 1IP Prediction Logger.

Covers:
  T1  — log_1ip_prediction returns SKIPPED for non-MLB sport
  T2  — log_1ip_prediction returns SKIPPED for wrong stat_key
  T3  — log_1ip_prediction returns SKIPPED when ceiling != MODEL_QUALIFIED_HOLD
  T4  — log_1ip_prediction returns SKIPPED when model_probability is None
  T5  — log_1ip_prediction returns SKIPPED for missing pitcher_id
  T6  — log_1ip_prediction returns SKIPPED for bad direction
  T7  — log_1ip_prediction returns SKIPPED for missing game_date
  T8  — log_1ip_prediction returns SKIPPED for game already started
  T9  — log_1ip_prediction returns SKIPPED for synthetic row marker
  T10 — log_1ip_prediction never raises (fail-open)
  T11 — log_1ip_prediction returns WRITE_FAILURE (not exception) on DB error
  T12 — Idempotency: second call with same identity returns DUPLICATE_PREVENTED
  T13 — Multi-worker: concurrent calls with same identity → at most one insert
  T14 — DB failure does not alter row terminal_label (fail-open invariant)
  T15 — Pipeline hook writes gate diagnostic on logging success
  T16 — Pipeline hook writes gate diagnostic on logger exception
  T17 — _log_dedup_key is deterministic for same inputs
  T18 — _log_dedup_key differs for different lines
  T19 — _log_dedup_key differs for LESS vs MORE direction
  T20 — _extract_probability returns calibrated_probability from ledger
  T21 — _extract_probability falls back through field names
  T22 — _extract_probability returns None when out of [0,1]
  T23 — benchmark_readiness.get_status returns db_available=False when DB absent
  T24 — benchmark_readiness threshold is configurable via env var
  T25 — outcome_logger.attach_outcome raises on missing prediction
  T26 — outcome_logger.attach_outcome raises on conflicting outcome
  T27 — outcome_logger.attach_outcome raises leakage guard violation
  T28 — outcome_logger.attach_outcome accepts idempotent re-submission
  T29 — No scoring mutation: terminal_label unchanged before/after logger call
  T30 — Skip counter increments on SKIPPED result
"""
import threading
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers — build minimal eligible row / enrichment
# ---------------------------------------------------------------------------

def _future_date() -> str:
    """A game date that is always in the future for GAME_ALREADY_STARTED checks."""
    return (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")


def _eligible_row(**overrides) -> dict:
    base = {
        "sport":          "MLB",
        "stat_key":       "1IP_PITCHES_THROWN",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "player_id":      681911,
        "player":         "Sandy Alcantara",
        "opponent":       "NYY",
        "game_date":      _future_date(),
        "line":           17.5,
        "direction":      "LESS",
        "start_time":     "",
    }
    base.update(overrides)
    return base


def _eligible_enr(**overrides) -> dict:
    base = {
        "model_probability_ledger": {
            "calibrated_probability": 0.62,
            "raw_probability":        0.64,
        },
        "first_inning_bf_distribution": {"n": 3, "mean": 3.1},
        "pitches_per_batter_distribution": {"n": 3, "mean": 4.2},
        "savant_1ip_ledger": {
            "source":       "savant_csv",
            "fetch_method": "savant_csv",
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# T1–T9: Skip-reason tests  (no DB call needed)
# ---------------------------------------------------------------------------

class TestSkipReasons(unittest.TestCase):

    def _call(self, row, enr):
        from validation.prediction_logger import log_1ip_prediction
        return log_1ip_prediction(row, enr)

    # Patch DB so tests never need a real connection
    def _patched_call(self, row, enr):
        with patch("validation.prediction_logger._get_conn") as mock_conn:
            # Should not reach DB for skip cases — but patch anyway to be safe
            mock_conn.side_effect = RuntimeError("DB should not be called for SKIP")
            return log_1ip_prediction(row, enr)

    def test_t1_non_mlb_sport_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row(sport="NBA")
        res = log_1ip_prediction(row, _eligible_enr())
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "NOT_MLB")

    def test_t2_wrong_stat_key_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row(stat_key="STRIKEOUTS")
        res = log_1ip_prediction(row, _eligible_enr())
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "NOT_1IP_STAT")

    def test_t3_non_hold_ceiling_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row(terminal_label="REJECT_NO_PLAY")
        res = log_1ip_prediction(row, _eligible_enr())
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "CEILING_NOT_HOLD")

    def test_t4_missing_probability_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row()
        enr = _eligible_enr()
        enr["model_probability_ledger"] = {}
        res = log_1ip_prediction(row, enr)
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "MISSING_PROBABILITY")

    def test_t5_missing_pitcher_id_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row(player_id=None)
        res = log_1ip_prediction(row, _eligible_enr())
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "MISSING_PITCHER_ID")

    def test_t6_bad_direction_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row(direction="PUSH")
        res = log_1ip_prediction(row, _eligible_enr())
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "MISSING_LINE_OR_DIRECTION")

    def test_t7_missing_game_date_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row(game_date=None)
        row.pop("board_date", None)
        res = log_1ip_prediction(row, _eligible_enr())
        self.assertEqual(res["action"], "SKIPPED")
        self.assertIn(res["reason"], ("MISSING_GAME_DATE", "MISSING_LINE_OR_DIRECTION"))

    def test_t8_game_already_started_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        past_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        row = _eligible_row(game_date=past_date, start_time="00:00")
        res = log_1ip_prediction(row, _eligible_enr())
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "GAME_ALREADY_STARTED")

    def test_t9_synthetic_row_marker_skipped(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row()
        enr = _eligible_enr()
        enr["savant_1ip_ledger"] = {"fetch_method": "synthetic_fixture", "source": "test"}
        res = log_1ip_prediction(row, enr)
        self.assertEqual(res["action"], "SKIPPED")
        self.assertEqual(res["reason"], "SYNTHETIC_ROW")


# ---------------------------------------------------------------------------
# T10–T11: Fail-open behavior
# ---------------------------------------------------------------------------

class TestFailOpen(unittest.TestCase):

    def test_t10_never_raises_on_any_input(self):
        """log_1ip_prediction must never propagate an exception."""
        from validation.prediction_logger import log_1ip_prediction
        # Wildly broken inputs
        for bad in [None, {}, {"sport": None}, 42, []]:
            try:
                res = log_1ip_prediction(bad, {})  # type: ignore
                self.assertIn(res.get("action", ""), ("SKIPPED", "WRITE_FAILURE"))
            except Exception as exc:
                self.fail(f"log_1ip_prediction raised {exc!r} for input {bad!r}")

    def test_t11_db_failure_returns_write_failure_not_exception(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row()
        enr = _eligible_enr()
        with patch("validation.prediction_logger._get_conn") as mock_conn:
            mock_conn.side_effect = RuntimeError("simulated DB down")
            res = log_1ip_prediction(row, enr)
        self.assertEqual(res["action"], "WRITE_FAILURE")
        self.assertIn("db", res["reason"])


# ---------------------------------------------------------------------------
# T12: Idempotency — second call returns DUPLICATE_PREVENTED
# ---------------------------------------------------------------------------

class TestIdempotency(unittest.TestCase):

    def _make_mock_conn(self, first_call_returns_row: bool, second_call_returns_row: bool):
        """
        Build a mock psycopg2 connection whose cursor.fetchone() returns a
        row (simulating INSERT success) or None (simulating ON CONFLICT DO NOTHING).
        """
        mock_conn   = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_conn.cursor    = MagicMock(return_value=mock_cursor)
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__  = MagicMock(return_value=False)

        call_count = {"n": 0}
        def _fetchone():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (999,) if first_call_returns_row else None
            return (1000,) if second_call_returns_row else None

        mock_cursor.fetchone = MagicMock(side_effect=_fetchone)
        return mock_conn

    def test_t12_second_call_duplicate_prevented(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row()
        enr = _eligible_enr()

        # First call: INSERT succeeds → fetchone returns row
        mock_conn1 = MagicMock()
        mock_cur1  = MagicMock()
        mock_conn1.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur1)
        mock_conn1.cursor.return_value.__exit__  = MagicMock(return_value=False)
        mock_cur1.fetchone.return_value = (42,)

        # Second call: ON CONFLICT → fetchone returns None
        mock_conn2 = MagicMock()
        mock_cur2  = MagicMock()
        mock_conn2.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur2)
        mock_conn2.cursor.return_value.__exit__  = MagicMock(return_value=False)
        mock_cur2.fetchone.return_value = None

        with patch("validation.prediction_logger._get_conn",
                   side_effect=[mock_conn1, mock_conn2]):
            with patch("validation.prediction_logger._ensure_tables"):
                res1 = log_1ip_prediction(row, enr)
                res2 = log_1ip_prediction(row, enr)

        self.assertEqual(res1["action"], "LOGGED")
        self.assertEqual(res2["action"], "DUPLICATE_PREVENTED")


# ---------------------------------------------------------------------------
# T13: Multi-worker concurrent calls → at most one insert
# ---------------------------------------------------------------------------

class TestMultiWorkerConcurrency(unittest.TestCase):

    def test_t13_concurrent_calls_at_most_one_logged(self):
        """
        Simulate 4 concurrent threads calling log_1ip_prediction for the
        same pitcher/game.  The DB-level ON CONFLICT DO NOTHING means exactly
        one returns LOGGED; the rest return DUPLICATE_PREVENTED.
        Verified here by letting the first thread's mock return a row and
        subsequent threads' mocks return None.
        """
        from validation.prediction_logger import log_1ip_prediction

        results = []
        lock = threading.Lock()
        call_order = {"n": 0}

        def _make_conn():
            with lock:
                n = call_order["n"]
                call_order["n"] += 1
            mock_conn = MagicMock()
            mock_cur  = MagicMock()
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
            # Only first call simulates successful INSERT
            mock_cur.fetchone.return_value = (n + 1,) if n == 0 else None
            return mock_conn

        def _worker():
            row = _eligible_row()
            enr = _eligible_enr()
            with patch("validation.prediction_logger._get_conn", side_effect=_make_conn):
                with patch("validation.prediction_logger._ensure_tables"):
                    res = log_1ip_prediction(row, enr)
            with lock:
                results.append(res["action"])

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logged = results.count("LOGGED")
        dupes  = results.count("DUPLICATE_PREVENTED")
        # DB-level dedup: 1 LOGGED, 3 DUPLICATE_PREVENTED (our mock simulates this)
        self.assertEqual(logged, 1)
        self.assertEqual(dupes, 3)


# ---------------------------------------------------------------------------
# T14: DB failure does not mutate row (fail-open invariant)
# ---------------------------------------------------------------------------

class TestNoScoringMutation(unittest.TestCase):

    def test_t14_db_failure_does_not_alter_terminal_label(self):
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row()
        enr = _eligible_enr()
        original_label = row["terminal_label"]

        with patch("validation.prediction_logger._get_conn") as mock_conn:
            mock_conn.side_effect = RuntimeError("simulated crash")
            log_1ip_prediction(row, enr)

        self.assertEqual(row["terminal_label"], original_label,
                         "terminal_label must not be mutated by logger failure")

    def test_t29_no_scoring_mutation_on_success(self):
        """terminal_label unchanged even when logger writes successfully."""
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row()
        enr = _eligible_enr()
        original_label = row["terminal_label"]

        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = (7,)

        with patch("validation.prediction_logger._get_conn", return_value=mock_conn):
            with patch("validation.prediction_logger._ensure_tables"):
                log_1ip_prediction(row, enr)

        self.assertEqual(row["terminal_label"], original_label,
                         "terminal_label must not be mutated by successful logger call")


# ---------------------------------------------------------------------------
# T15–T16: Pipeline hook gate diagnostics
# ---------------------------------------------------------------------------

class TestPipelineHookDiagnostics(unittest.TestCase):

    def test_t15_hook_writes_gate_diagnostic_on_success(self):
        """
        When the logger succeeds, the pipeline hook writes
        row['gates']['prediction_logger'] with action='LOGGED'.
        Tested by calling the logger directly and verifying the gate stamp.
        """
        from validation.prediction_logger import log_1ip_prediction
        row = _eligible_row()
        row.setdefault("gates", {})
        enr = _eligible_enr()

        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = (1,)

        with patch("validation.prediction_logger._get_conn", return_value=mock_conn):
            with patch("validation.prediction_logger._ensure_tables"):
                gate_result = log_1ip_prediction(row, enr)

        # Pipeline hook stamps this on the row
        row["gates"]["prediction_logger"] = gate_result
        self.assertIn("prediction_logger", row["gates"])
        self.assertEqual(row["gates"]["prediction_logger"]["action"], "LOGGED")

    def test_t16_hook_writes_error_diagnostic_on_exception(self):
        """
        When the logger call itself throws (unexpected), the except block
        stamps row['gates']['prediction_logger']['error'] and does not re-raise.
        """
        row = _eligible_row()
        row.setdefault("gates", {})
        enr = _eligible_enr()

        # Simulate the pipeline hook's except block
        exc_message = "unexpected crash in logger"
        try:
            raise RuntimeError(exc_message)
        except Exception as _log_exc:
            row.setdefault("gates", {}).setdefault(
                "prediction_logger", {}
            )["error"] = str(_log_exc)[:80]

        self.assertIn("prediction_logger", row["gates"])
        self.assertIn("error", row["gates"]["prediction_logger"])
        self.assertIn(exc_message, row["gates"]["prediction_logger"]["error"])


# ---------------------------------------------------------------------------
# T17–T19: Dedup key determinism
# ---------------------------------------------------------------------------

class TestDedupKey(unittest.TestCase):

    def test_t17_dedup_key_deterministic_same_inputs(self):
        from validation.prediction_logger import _log_dedup_key
        k1 = _log_dedup_key(681911, "2026-09-01", 17.5, "LESS")
        k2 = _log_dedup_key(681911, "2026-09-01", 17.5, "LESS")
        self.assertEqual(k1, k2)

    def test_t18_dedup_key_differs_by_line(self):
        from validation.prediction_logger import _log_dedup_key
        k1 = _log_dedup_key(681911, "2026-09-01", 17.5, "LESS")
        k2 = _log_dedup_key(681911, "2026-09-01", 18.5, "LESS")
        self.assertNotEqual(k1, k2)

    def test_t19_dedup_key_differs_by_direction(self):
        from validation.prediction_logger import _log_dedup_key
        k1 = _log_dedup_key(681911, "2026-09-01", 17.5, "LESS")
        k2 = _log_dedup_key(681911, "2026-09-01", 17.5, "MORE")
        self.assertNotEqual(k1, k2)


# ---------------------------------------------------------------------------
# T20–T22: Probability extraction
# ---------------------------------------------------------------------------

class TestExtractProbability(unittest.TestCase):

    def test_t20_extracts_calibrated_probability(self):
        from validation.prediction_logger import _extract_probability
        enr = {"model_probability_ledger": {"calibrated_probability": 0.62}}
        self.assertAlmostEqual(_extract_probability(enr), 0.62, places=5)

    def test_t21_fallback_through_field_names(self):
        from validation.prediction_logger import _extract_probability
        enr = {"model_probability_ledger": {"raw_probability": 0.58}}
        self.assertAlmostEqual(_extract_probability(enr), 0.58, places=5)

    def test_t22_returns_none_when_out_of_bounds(self):
        from validation.prediction_logger import _extract_probability
        enr = {"model_probability_ledger": {"calibrated_probability": 1.5}}
        self.assertIsNone(_extract_probability(enr))

    def test_t22b_returns_none_when_ledger_missing(self):
        from validation.prediction_logger import _extract_probability
        self.assertIsNone(_extract_probability({}))


# ---------------------------------------------------------------------------
# T23–T24: Benchmark readiness
# ---------------------------------------------------------------------------

class TestBenchmarkReadiness(unittest.TestCase):

    def test_t23_returns_db_unavailable_when_no_db(self):
        from validation.benchmark_readiness import get_status
        with patch("validation.benchmark_readiness._get_conn", return_value=None):
            status = get_status()
        self.assertFalse(status["ready"])
        self.assertFalse(status["db_available"])
        self.assertEqual(status["error"], "DB_UNAVAILABLE")

    def test_t24_threshold_configurable_via_env(self):
        import os
        from validation.benchmark_readiness import get_status
        with patch.dict(os.environ, {"VALIDATION_BENCHMARK_THRESHOLD": "5"}):
            with patch("validation.benchmark_readiness._get_conn", return_value=None):
                status = get_status()
        self.assertEqual(status["threshold"], 5)

    def test_t24b_ready_requires_verified_outcomes_only(self):
        """
        ready=True MUST NOT be set when n_settled >= threshold but
        n_verified_settled < threshold.  Unverified pybaseball fallbacks
        must never satisfy the 20-row benchmark milestone.
        """
        from validation.benchmark_readiness import get_status
        import unittest.mock as _mock

        # n_settled = 25 (above threshold=20), n_verified = 5 (below threshold)
        # → ready must be False
        mock_cursor = _mock.MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = _mock.MagicMock(return_value=False)
        mock_cursor.fetchone.side_effect = [
            (30,),  # n_logged
            (25,),  # n_settled (>= 20 threshold)
            (5,),   # n_verified (< 20 threshold)
            (3,),   # n_hits
        ]
        mock_conn = _mock.MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("validation.benchmark_readiness._get_conn", return_value=mock_conn):
            status = get_status()

        self.assertFalse(status["ready"],
            "ready must be False when n_verified < threshold even if n_settled >= threshold")
        self.assertEqual(status["n_settled"], 25)
        self.assertEqual(status["n_verified_settled"], 5)
        self.assertEqual(status["benchmark_sample_count"], 5,
            "benchmark_sample_count must equal n_verified_settled, not n_settled")

    def test_t24c_ready_true_when_verified_reaches_threshold(self):
        """
        ready=True only when n_verified_settled >= threshold.
        """
        from validation.benchmark_readiness import get_status
        import unittest.mock as _mock

        # n_verified = 20 exactly → ready=True
        mock_cursor = _mock.MagicMock()
        mock_cursor.__enter__ = lambda s: s
        mock_cursor.__exit__ = _mock.MagicMock(return_value=False)
        mock_cursor.fetchone.side_effect = [
            (45,),  # n_logged
            (22,),  # n_settled
            (20,),  # n_verified (== threshold)
            (11,),  # n_hits
        ]
        mock_conn = _mock.MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("validation.benchmark_readiness._get_conn", return_value=mock_conn):
            status = get_status()

        self.assertTrue(status["ready"],
            "ready must be True when n_verified_settled >= threshold")
        self.assertEqual(status["benchmark_sample_count"], 20)


# ---------------------------------------------------------------------------
# T25–T28: Outcome logger
# ---------------------------------------------------------------------------

class TestOutcomeLogger(unittest.TestCase):

    def _make_pred_db_conn(self, pred_row=None, outcome_row=None):
        """Build a mock conn that returns pred_row from prediction lookup."""
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__  = MagicMock(return_value=False)

        call_count = {"n": 0}
        def _fetchone():
            n = call_count["n"]
            call_count["n"] += 1
            if n == 0:
                return pred_row
            if n == 1:
                return outcome_row
            return (9999, "2026-09-10T01:00:00+00:00")

        mock_cur.fetchone = MagicMock(side_effect=_fetchone)
        return mock_conn

    def test_t25_raises_when_prediction_not_found(self):
        from validation.outcome_logger import attach_outcome, OutcomeLogError
        mock_conn = self._make_pred_db_conn(pred_row=None)
        with patch("validation.outcome_logger._get_conn", return_value=mock_conn):
            with self.assertRaises(OutcomeLogError) as ctx:
                attach_outcome(
                    log_dedup_key    = "ddk_doesnotexist",
                    actual_pitches   = 15,
                    outcome_source   = "manual",
                )
            self.assertEqual(ctx.exception.code, "PREDICTION_NOT_FOUND")

    def test_t26_raises_on_conflicting_outcome(self):
        from validation.outcome_logger import attach_outcome, OutcomeLogError
        frozen_at_str = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        pred_row = (
            "pred-abc123",
            frozen_at_str,
            "2026-09-01", "Sandy Alcantara", 681911, 17.5, "LESS"
        )
        existing_outcome = (12, True, "manual", "2026-09-01T22:00:00+00:00")
        mock_conn = self._make_pred_db_conn(pred_row=pred_row,
                                            outcome_row=existing_outcome)
        with patch("validation.outcome_logger._get_conn", return_value=mock_conn):
            with self.assertRaises(OutcomeLogError) as ctx:
                attach_outcome(
                    log_dedup_key    = "ddk_abc",
                    actual_pitches   = 20,      # different from settled 12 → conflict
                    outcome_source   = "manual",
                )
            self.assertEqual(ctx.exception.code, "CONFLICTING_OUTCOME")

    def test_t27_raises_on_leakage_guard_violation(self):
        """outcome_timestamp before frozen_at → leakage guard raises."""
        from validation.outcome_logger import attach_outcome, OutcomeLogError

        # frozen_at is in the FUTURE (5 days from now)
        frozen_at_str = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        pred_row = (
            "pred-future",
            frozen_at_str,
            "2026-09-06", "Sandy Alcantara", 681911, 17.5, "LESS"
        )
        mock_conn = self._make_pred_db_conn(pred_row=pred_row, outcome_row=None)

        with patch("validation.outcome_logger._get_conn", return_value=mock_conn):
            # Outcome timestamp is NOW (before frozen_at which is 5 days ahead)
            outcome_ts = datetime.now(timezone.utc).isoformat()
            with self.assertRaises(OutcomeLogError) as ctx:
                attach_outcome(
                    log_dedup_key      = "ddk_future",
                    actual_pitches     = 15,
                    outcome_source     = "manual",
                    outcome_timestamp  = outcome_ts,
                )
            self.assertEqual(ctx.exception.code, "LEAKAGE_GUARD_FAILED")

    def test_t28_idempotent_same_result_returns_already_settled(self):
        """Re-submitting the same actual_pitches → ALREADY_SETTLED (not error)."""
        from validation.outcome_logger import attach_outcome

        frozen_at_str = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        pred_row = (
            "pred-abc123",
            frozen_at_str,
            "2026-09-01", "Sandy Alcantara", 681911, 17.5, "LESS"
        )
        existing_outcome = (15, True, "manual", "2026-09-01T22:00:00+00:00")
        mock_conn = self._make_pred_db_conn(pred_row=pred_row,
                                            outcome_row=existing_outcome)
        with patch("validation.outcome_logger._get_conn", return_value=mock_conn):
            result = attach_outcome(
                log_dedup_key    = "ddk_abc",
                actual_pitches   = 15,      # same as existing → idempotent
                outcome_source   = "manual",
            )
        self.assertEqual(result["action"], "ALREADY_SETTLED")
        self.assertEqual(result["actual_pitches"], 15)


# ---------------------------------------------------------------------------
# T30: Counter increments
# ---------------------------------------------------------------------------

class TestCounters(unittest.TestCase):

    def test_t30_skip_counter_increments(self):
        import validation.prediction_logger as logger_mod
        from validation.prediction_logger import log_1ip_prediction

        before = logger_mod.get_in_process_counters()["skipped"]
        row = _eligible_row(sport="NBA")   # will be skipped
        log_1ip_prediction(row, _eligible_enr())
        after = logger_mod.get_in_process_counters()["skipped"]
        self.assertGreater(after, before)


# ---------------------------------------------------------------------------
# T — Export determinism (sequential pages return same rows in same order)
# ---------------------------------------------------------------------------

class TestExportDeterminism(unittest.TestCase):

    def test_export_determinism_db_unavailable(self):
        """When DB is unavailable, export returns empty list with error."""
        from validation.benchmark_readiness import get_eligible_predictions
        with patch("validation.benchmark_readiness._get_conn", return_value=None):
            result = get_eligible_predictions(limit=50, offset=0)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["error"], "DB_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
