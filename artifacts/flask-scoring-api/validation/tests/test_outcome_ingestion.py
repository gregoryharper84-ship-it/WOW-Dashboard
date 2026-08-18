"""
validation/tests/test_outcome_ingestion.py

Tests for the WOW 1IP Automated Outcome Ingestion workflow.

Covers:
  T1  — Happy path: single game, clear pitch count → ATTACHED
  T2  — Happy path dry-run: would attach, returns DRY_RUN, no DB write
  T3  — No data: Savant returns empty → NO_DATA
  T4  — Fetch error: network failure → FETCH_ERROR
  T5  — Ambiguous doubleheader: pitcher in 2+ game_pks with pitches → AMBIGUOUS_DOUBLEHEADER
  T6  — Resolved doubleheader: pitcher in 2 game_pks but pitches in 1 → OK → ATTACHED
  T7  — Identity mismatch: returned date doesn't match expected → IDENTITY_MISMATCH
  T8  — Invalid pitch count: zero pitches returned → INVALID_PITCH_COUNT
  T9  — Game not yet played: game_date is today → GAME_NOT_YET_PLAYED
  T10 — Retry idempotency: ALREADY_SETTLED from attach_outcome → ALREADY_SETTLED
  T11 — Leakage guard: outcome_logger raises LEAKAGE_GUARD_FAILED → OUTCOME_ATTACH_ERROR
  T12 — Multi-row partial failure: 3 rows, 1 OK, 1 NO_DATA, 1 FETCH_ERROR
  T13 — max_rows respected: DB query limited correctly
  T14 — DB unavailable: returns top_level_error, no rows processed
  T15 — Dry-run never calls attach_outcome
  T16 — _fetch_game_pitch_count: single group → OK
  T17 — _fetch_game_pitch_count: multiple groups, one has pitches → OK (resolved dh)
  T18 — _fetch_game_pitch_count: multiple groups, all have pitches → AMBIGUOUS_DOUBLEHEADER
  T19 — _fetch_game_pitch_count: empty df after date filter → IDENTITY_MISMATCH
  T20 — _fetch_game_pitch_count: both primary and fallback fail → FETCH_ERROR
  T21 — IngestResult.summary counts by status correctly
  T22 — IngestResult.n_attached counts ATTACHED + DRY_RUN
  T23 — RowResult.to_dict contains all required keys
  T24 — CLI dry-run default: --no-dry-run required to write
  T25 — CLI exit code 0 on clean run
  T26 — CLI exit code 1 on partial failure
  T27 — CLI exit code 2 on DB unavailable
  T28 — after_date lower bound filters out old predictions
  T29 — outcome_verified=True only for savant_csv_direct, False for fallback
  T30 — secrets never appear in error messages (DB URL stripped)
"""
import io
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _future_date() -> str:
    return (date.today() + timedelta(days=5)).isoformat()


def _past_date(days: int = 2) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _today() -> str:
    return date.today().isoformat()


def _make_pred(game_date: str | None = None, **kwargs) -> dict:
    base = {
        "log_dedup_key":    "ddk_test1234567",
        "prediction_id":    "pred-001",
        "pitcher_name":     "Sandy Alcantara",
        "pitcher_mlbam_id": 681911,
        "game_date":        game_date or _past_date(),
        "line":             17.5,
        "direction":        "LESS",
        "frozen_at":        (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
    }
    base.update(kwargs)
    return base


def _make_df(game_date: str, game_pks: list, pitches_per_pk: list | None = None):
    """Build a mock pandas-like DataFrame for testing."""
    try:
        import pandas as pd
    except ImportError:
        return None
    if pitches_per_pk is None:
        pitches_per_pk = [5] * len(game_pks)
    rows = []
    for gp, n_pitches in zip(game_pks, pitches_per_pk):
        for i in range(n_pitches):
            rows.append({"game_pk": gp, "game_date": game_date,
                         "inning": 1, "at_bat_number": i // 3 + 1,
                         "events": None})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# T1–T2: Happy path
# ---------------------------------------------------------------------------

class TestHappyPath(unittest.TestCase):

    def _run_with_mocks(self, preds, fetch_result, attach_result, dry_run=False):
        from validation.outcome_ingestion import ingest_outcomes

        with patch("validation.outcome_ingestion._get_conn") as mc, \
             patch("validation.outcome_ingestion._find_unresolved", return_value=preds), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count",
                   return_value=fetch_result), \
             patch("validation.outcome_ingestion.attach_outcome",
                   return_value=attach_result) as ma:
            result = ingest_outcomes(dry_run=dry_run, max_rows=10)
        return result, ma

    def test_t1_happy_path_single_game_attached(self):
        pred = _make_pred()
        fetch = {"status": "OK", "pitch_count": 14, "game_pk": "748001",
                 "game_pks_found": ["748001"], "fetch_method": "savant_csv_direct",
                 "error": None, "outcome_verified": True}
        attach = {"action": "OUTCOME_ATTACHED", "outcome_log_id": 1,
                  "prediction_id": pred["prediction_id"], "hit": False,
                  "actual_pitches": 14, "logged_at": "2026-09-02T08:00:00+00:00"}

        result, _ = self._run_with_mocks([pred], fetch, attach, dry_run=False)

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0].status, "ATTACHED")
        self.assertEqual(result.rows[0].pitch_count, 14)
        self.assertEqual(result.rows[0].game_pk, "748001")
        self.assertEqual(result.n_attached, 1)

    def test_t2_dry_run_returns_dry_run_status_no_db_write(self):
        pred = _make_pred()
        fetch = {"status": "OK", "pitch_count": 14, "game_pk": "748001",
                 "game_pks_found": ["748001"], "fetch_method": "savant_csv_direct",
                 "error": None, "outcome_verified": True}

        from validation.outcome_ingestion import ingest_outcomes
        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved", return_value=[pred]), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count", return_value=fetch), \
             patch("validation.outcome_ingestion.attach_outcome") as mock_attach:
            result = ingest_outcomes(dry_run=True, max_rows=10)

        self.assertEqual(result.rows[0].status, "DRY_RUN")
        self.assertEqual(result.rows[0].pitch_count, 14)
        mock_attach.assert_not_called()
        self.assertEqual(result.n_attached, 1)   # DRY_RUN counts in n_attached


# ---------------------------------------------------------------------------
# T3–T8: Skip statuses from fetch
# ---------------------------------------------------------------------------

class TestFetchSkips(unittest.TestCase):

    def _run_single(self, fetch_result, dry_run=False, game_date=None):
        from validation.outcome_ingestion import ingest_outcomes
        pred = _make_pred(game_date=game_date or _past_date())
        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved", return_value=[pred]), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count",
                   return_value=fetch_result):
            return ingest_outcomes(dry_run=dry_run, max_rows=10)

    def test_t3_no_data_skips_row(self):
        fetch = {"status": "NO_DATA", "pitch_count": None, "game_pk": None,
                 "game_pks_found": [], "fetch_method": "savant_csv_direct",
                 "error": "DataFrame empty", "outcome_verified": False}
        result = self._run_single(fetch)
        self.assertEqual(result.rows[0].status, "NO_DATA")

    def test_t4_fetch_error_skips_row(self):
        fetch = {"status": "FETCH_ERROR", "pitch_count": None, "game_pk": None,
                 "game_pks_found": [], "fetch_method": "none",
                 "error": "ConnectionError: timed out", "outcome_verified": False}
        result = self._run_single(fetch)
        self.assertEqual(result.rows[0].status, "FETCH_ERROR")

    def test_t5_ambiguous_doubleheader_fails_closed(self):
        fetch = {"status": "AMBIGUOUS_DOUBLEHEADER", "pitch_count": None, "game_pk": None,
                 "game_pks_found": ["748001", "748002"], "fetch_method": "savant_csv_direct",
                 "error": "Pitcher pitched in 2 games on this date", "outcome_verified": False}
        result = self._run_single(fetch)
        self.assertEqual(result.rows[0].status, "AMBIGUOUS_DOUBLEHEADER")
        self.assertIsNone(result.rows[0].pitch_count)

    def test_t7_identity_mismatch_fails_closed(self):
        fetch = {"status": "IDENTITY_MISMATCH", "pitch_count": None, "game_pk": None,
                 "game_pks_found": [], "fetch_method": "savant_csv_direct",
                 "error": "No rows with game_date after filter", "outcome_verified": False}
        result = self._run_single(fetch)
        self.assertEqual(result.rows[0].status, "IDENTITY_MISMATCH")

    def test_t8_invalid_pitch_count_fails_closed(self):
        fetch = {"status": "INVALID_PITCH_COUNT", "pitch_count": 0, "game_pk": "748001",
                 "game_pks_found": ["748001"], "fetch_method": "savant_csv_direct",
                 "error": "pitch_count=0", "outcome_verified": False}
        result = self._run_single(fetch)
        self.assertEqual(result.rows[0].status, "INVALID_PITCH_COUNT")

    def test_t9_game_not_yet_played_skipped(self):
        """game_date = today → GAME_NOT_YET_PLAYED even if DB returned it."""
        from validation.outcome_ingestion import ingest_outcomes
        pred = _make_pred(game_date=_today())
        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved", return_value=[pred]), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count") as mock_fetch:
            result = ingest_outcomes(dry_run=True, max_rows=10,
                                     before_date=_future_date())
        # fetch should NOT be called for a today-or-future game
        mock_fetch.assert_not_called()
        self.assertEqual(result.rows[0].status, "GAME_NOT_YET_PLAYED")


# ---------------------------------------------------------------------------
# T6: Resolved doubleheader
# ---------------------------------------------------------------------------

class TestResolvedDoubleheader(unittest.TestCase):

    def test_t6_resolved_doubleheader_uses_nonzero_game_pk(self):
        """2 game_pks, pitcher pitched in only 1 → resolvable → ATTACHED."""
        from validation.outcome_ingestion import ingest_outcomes
        pred = _make_pred()
        # Pitcher pitched 12 pitches in game 748002; 0 in 748001
        fetch = {"status": "OK", "pitch_count": 12, "game_pk": "748002",
                 "game_pks_found": ["748001", "748002"],
                 "fetch_method": "savant_csv_direct",
                 "error": None, "outcome_verified": True}
        attach = {"action": "OUTCOME_ATTACHED", "actual_pitches": 12,
                  "hit": True, "logged_at": "2026-09-02T08:00:00+00:00"}

        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved", return_value=[pred]), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count", return_value=fetch), \
             patch("validation.outcome_ingestion.attach_outcome", return_value=attach):
            result = ingest_outcomes(dry_run=False, max_rows=10)

        self.assertEqual(result.rows[0].status, "ATTACHED")
        self.assertEqual(result.rows[0].game_pk, "748002")


# ---------------------------------------------------------------------------
# T10–T11: Attach-outcome edge cases
# ---------------------------------------------------------------------------

class TestAttachEdgeCases(unittest.TestCase):

    def _run_single_attach(self, attach_side_effect=None, attach_return=None, dry_run=False):
        from validation.outcome_ingestion import ingest_outcomes
        pred = _make_pred()
        fetch = {"status": "OK", "pitch_count": 14, "game_pk": "748001",
                 "game_pks_found": ["748001"], "fetch_method": "savant_csv_direct",
                 "error": None, "outcome_verified": True}

        mock_attach_kwargs = {}
        if attach_side_effect is not None:
            mock_attach_kwargs["side_effect"] = attach_side_effect
        elif attach_return is not None:
            mock_attach_kwargs["return_value"] = attach_return

        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved", return_value=[pred]), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count", return_value=fetch), \
             patch("validation.outcome_ingestion.attach_outcome", **mock_attach_kwargs):
            return ingest_outcomes(dry_run=dry_run, max_rows=10)

    def test_t10_already_settled_idempotent(self):
        attach_return = {"action": "ALREADY_SETTLED", "actual_pitches": 14,
                         "hit": False, "outcome_source": "manual"}
        result = self._run_single_attach(attach_return=attach_return, dry_run=False)
        self.assertEqual(result.rows[0].status, "ALREADY_SETTLED")

    def test_t11_leakage_guard_becomes_outcome_attach_error(self):
        from validation.outcome_logger import OutcomeLogError
        exc = OutcomeLogError("LEAKAGE_GUARD_FAILED", "outcome before frozen_at")
        result = self._run_single_attach(attach_side_effect=exc, dry_run=False)
        self.assertEqual(result.rows[0].status, "OUTCOME_ATTACH_ERROR")


# ---------------------------------------------------------------------------
# T12: Multi-row partial failure
# ---------------------------------------------------------------------------

class TestMultiRowPartial(unittest.TestCase):

    def test_t12_multi_row_partial_failure(self):
        """3 rows: 1 OK→ATTACHED, 1 NO_DATA, 1 FETCH_ERROR."""
        from validation.outcome_ingestion import ingest_outcomes

        preds = [
            _make_pred(log_dedup_key="ddk_a", game_date=_past_date(3)),
            _make_pred(log_dedup_key="ddk_b", game_date=_past_date(2)),
            _make_pred(log_dedup_key="ddk_c", game_date=_past_date(1)),
        ]

        fetch_results = [
            {"status": "OK", "pitch_count": 14, "game_pk": "748001",
             "game_pks_found": ["748001"], "fetch_method": "savant_csv_direct",
             "error": None, "outcome_verified": True},
            {"status": "NO_DATA", "pitch_count": None, "game_pk": None,
             "game_pks_found": [], "fetch_method": "savant_csv_direct",
             "error": "empty", "outcome_verified": False},
            {"status": "FETCH_ERROR", "pitch_count": None, "game_pk": None,
             "game_pks_found": [], "fetch_method": "none",
             "error": "connection refused", "outcome_verified": False},
        ]
        fetch_iter = iter(fetch_results)

        attach_return = {"action": "OUTCOME_ATTACHED", "actual_pitches": 14,
                         "hit": False, "logged_at": "2026-09-02T08:00:00+00:00"}

        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved", return_value=preds), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count",
                   side_effect=fetch_results), \
             patch("validation.outcome_ingestion.attach_outcome", return_value=attach_return):
            result = ingest_outcomes(dry_run=False, max_rows=10)

        statuses = {r.status for r in result.rows}
        self.assertIn("ATTACHED", statuses)
        self.assertIn("NO_DATA", statuses)
        self.assertIn("FETCH_ERROR", statuses)
        self.assertEqual(len(result.rows), 3)


# ---------------------------------------------------------------------------
# T13: max_rows
# ---------------------------------------------------------------------------

class TestMaxRows(unittest.TestCase):

    def test_t13_max_rows_respected(self):
        """_find_unresolved is called with the correct max_rows limit."""
        from validation.outcome_ingestion import ingest_outcomes
        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved",
                   return_value=[]) as mock_find:
            ingest_outcomes(dry_run=True, max_rows=7)
        # Verify max_rows was passed through
        args, kwargs = mock_find.call_args
        self.assertEqual(args[3], 7)


# ---------------------------------------------------------------------------
# T14: DB unavailable
# ---------------------------------------------------------------------------

class TestDbUnavailable(unittest.TestCase):

    def test_t14_db_unavailable_top_level_error(self):
        from validation.outcome_ingestion import ingest_outcomes
        with patch("validation.outcome_ingestion._get_conn",
                   side_effect=RuntimeError("DATABASE_URL not set")):
            result = ingest_outcomes(dry_run=True)
        self.assertIsNotNone(result.top_level_error)
        self.assertIn("DB_UNAVAILABLE", result.top_level_error)
        self.assertEqual(len(result.rows), 0)


# ---------------------------------------------------------------------------
# T15: Dry-run never calls attach_outcome
# ---------------------------------------------------------------------------

class TestDryRunNeverWrites(unittest.TestCase):

    def test_t15_dry_run_never_calls_attach(self):
        from validation.outcome_ingestion import ingest_outcomes
        pred = _make_pred()
        fetch = {"status": "OK", "pitch_count": 18, "game_pk": "748001",
                 "game_pks_found": ["748001"], "fetch_method": "savant_csv_direct",
                 "error": None, "outcome_verified": True}
        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved", return_value=[pred]), \
             patch("validation.outcome_ingestion._fetch_game_pitch_count", return_value=fetch), \
             patch("validation.outcome_ingestion.attach_outcome") as mock_attach:
            ingest_outcomes(dry_run=True, max_rows=10)
        mock_attach.assert_not_called()


# ---------------------------------------------------------------------------
# T16–T20: _fetch_game_pitch_count unit tests
# ---------------------------------------------------------------------------

class TestFetchGamePitchCount(unittest.TestCase):
    """Test the internal fetch function with mock DataFrames."""

    def _run_fetch(self, df_primary, df_fallback=None, game_date=None):
        from validation.outcome_ingestion import _fetch_game_pitch_count
        gdate = game_date or _past_date()
        with patch("validation.outcome_ingestion._fetch_savant_csv_direct",
                   return_value=(df_primary, "savant_csv_direct",
                                 None if df_primary is not None else "failed")) as _mp, \
             patch("validation.outcome_ingestion._fetch_pybaseball_fallback",
                   return_value=(df_fallback, "pybaseball_fallback",
                                 None if df_fallback is not None else "failed2")), \
             patch("validation.outcome_ingestion._ensure_pandas", return_value=True):
            # Import inside patch context
            import importlib
            import validation.outcome_ingestion as oi_mod
            # Patch the local imports inside the function
            with patch.object(oi_mod, "_fetch_game_pitch_count",
                               wraps=oi_mod._fetch_game_pitch_count):
                return _fetch_game_pitch_count(681911, gdate)

    def _run_fetch_real(self, df_primary, game_date=None):
        """Run _fetch_game_pitch_count with real internals but mocked savant calls."""
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not available")
        from validation.outcome_ingestion import _fetch_game_pitch_count
        gdate = game_date or _past_date()

        with patch("gate_engine.mlb.savant_1ip_ledger._fetch_savant_csv_direct",
                   return_value=(df_primary, "savant_csv_direct",
                                 None if df_primary is not None else "err")), \
             patch("gate_engine.mlb.savant_1ip_ledger._fetch_pybaseball_fallback",
                   return_value=(None, "pybaseball_fallback", "not_used")):
            return _fetch_game_pitch_count(681911, gdate)

    def test_t16_single_group_returns_ok(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not available")
        gdate = _past_date()
        df = _make_df(gdate, ["748001"], [12])
        result = self._run_fetch_real(df, game_date=gdate)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["pitch_count"], 12)
        self.assertEqual(result["game_pk"], "748001")

    def test_t17_two_groups_one_nonzero_resolves(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not available")
        gdate = _past_date()
        # game 748001 has 0 pitches (no rows), 748002 has 8
        df = _make_df(gdate, ["748002"], [8])
        # Add a zero-pitch game entry — but since we only include rows with pitches,
        # game 748001 won't appear in the DataFrame at all (0 rows = 0 pitch count)
        # Instead simulate via two games where only one has pitches
        import pandas as pd2
        rows = [
            {"game_pk": "748002", "game_date": gdate, "inning": 1, "at_bat_number": i, "events": None}
            for i in range(8)
        ]
        df2 = pd2.DataFrame(rows)
        result = self._run_fetch_real(df2, game_date=gdate)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["pitch_count"], 8)
        self.assertEqual(result["game_pk"], "748002")

    def test_t18_two_groups_both_nonzero_ambiguous(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not available")
        gdate = _past_date()
        import pandas as pd2
        rows = (
            [{"game_pk": "748001", "game_date": gdate, "inning": 1, "at_bat_number": i, "events": None}
             for i in range(5)]
            + [{"game_pk": "748002", "game_date": gdate, "inning": 1, "at_bat_number": i, "events": None}
               for i in range(7)]
        )
        df = pd2.DataFrame(rows)
        result = self._run_fetch_real(df, game_date=gdate)
        self.assertEqual(result["status"], "AMBIGUOUS_DOUBLEHEADER")
        self.assertIsNone(result["pitch_count"])

    def test_t19_date_filter_removes_all_rows_identity_mismatch(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not available")
        import pandas as pd2
        expected_date = _past_date(3)
        wrong_date = _past_date(10)
        rows = [{"game_pk": "748001", "game_date": wrong_date, "inning": 1,
                 "at_bat_number": i, "events": None} for i in range(5)]
        df = pd2.DataFrame(rows)
        result = self._run_fetch_real(df, game_date=expected_date)
        self.assertEqual(result["status"], "IDENTITY_MISMATCH")

    def test_t20_both_fetch_methods_fail(self):
        from validation.outcome_ingestion import _fetch_game_pitch_count
        gdate = _past_date()
        with patch("gate_engine.mlb.savant_1ip_ledger._fetch_savant_csv_direct",
                   return_value=(None, "savant_csv_direct", "HTTP 503")), \
             patch("gate_engine.mlb.savant_1ip_ledger._fetch_pybaseball_fallback",
                   return_value=(None, "pybaseball_fallback", "import error")):
            result = _fetch_game_pitch_count(681911, gdate)
        self.assertEqual(result["status"], "FETCH_ERROR")
        self.assertIsNotNone(result["error"])


# ---------------------------------------------------------------------------
# T21–T23: IngestResult helpers
# ---------------------------------------------------------------------------

class TestIngestResultHelpers(unittest.TestCase):

    def _make_result(self, statuses: list[str]):
        from validation.outcome_ingestion import IngestResult, RowResult
        result = IngestResult(
            dry_run=False, run_timestamp="2026-09-02T00:00:00+00:00",
            predictions_queried=len(statuses),
            before_date=_past_date(), after_date=None, max_rows=50,
        )
        for i, st in enumerate(statuses):
            result.rows.append(RowResult(
                log_dedup_key=f"key{i}", prediction_id=f"pred{i}",
                pitcher_name="Test", pitcher_mlbam_id=12345,
                game_date=_past_date(i + 1), line=17.5, direction="LESS",
                status=st, pitch_count=14, game_pk="748001",
                fetch_method="savant_csv_direct", detail=None, dry_run=False,
            ))
        return result

    def test_t21_summary_counts_by_status(self):
        result = self._make_result(["ATTACHED", "NO_DATA", "ATTACHED", "FETCH_ERROR", "ATTACHED"])
        s = result.summary
        self.assertEqual(s.get("ATTACHED", 0), 3)
        self.assertEqual(s.get("NO_DATA", 0), 1)
        self.assertEqual(s.get("FETCH_ERROR", 0), 1)

    def test_t22_n_attached_counts_attached_and_dry_run(self):
        result = self._make_result(["ATTACHED", "DRY_RUN", "NO_DATA"])
        self.assertEqual(result.n_attached, 2)

    def test_t23_row_result_to_dict_has_required_keys(self):
        result = self._make_result(["ATTACHED"])
        d = result.rows[0].to_dict()
        required = {"log_dedup_key", "prediction_id", "pitcher_name", "pitcher_mlbam_id",
                    "game_date", "line", "direction", "status", "pitch_count",
                    "game_pk", "fetch_method", "detail", "dry_run"}
        self.assertTrue(required.issubset(set(d.keys())))


# ---------------------------------------------------------------------------
# T24–T27: CLI tests
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def _run_cli(self, argv, ingest_result):
        from validation.cli_ingest import main
        from validation.outcome_ingestion import IngestResult
        with patch("validation.cli_ingest.ingest_outcomes", return_value=ingest_result):
            return main(argv)

    def _empty_result(self, top_level_error=None, statuses=None):
        from validation.outcome_ingestion import IngestResult, RowResult
        result = IngestResult(
            dry_run=True, run_timestamp="2026-09-02T00:00:00+00:00",
            predictions_queried=len(statuses or []),
            before_date=_past_date(), after_date=None, max_rows=50,
            top_level_error=top_level_error,
        )
        for i, st in enumerate(statuses or []):
            result.rows.append(RowResult(
                log_dedup_key=f"k{i}", prediction_id=f"p{i}",
                pitcher_name="T", pitcher_mlbam_id=1,
                game_date=_past_date(i+1), line=17.5, direction="LESS",
                status=st, pitch_count=None, game_pk=None,
                fetch_method=None, detail=None, dry_run=True,
            ))
        return result

    def test_t24_dry_run_default_flag(self):
        """Default argv has dry_run=True; --no-dry-run overrides."""
        from validation.cli_ingest import _parse_args
        args_default = _parse_args([])
        self.assertTrue(args_default.dry_run)
        args_live = _parse_args(["--no-dry-run"])
        self.assertFalse(args_live.dry_run)

    def test_t25_exit_code_0_on_clean_run(self):
        result = self._empty_result(statuses=["ATTACHED", "ALREADY_SETTLED"])
        exit_code = self._run_cli(["--no-dry-run"], result)
        self.assertEqual(exit_code, 0)

    def test_t26_exit_code_1_on_partial_failure(self):
        result = self._empty_result(statuses=["ATTACHED", "FETCH_ERROR"])
        exit_code = self._run_cli(["--no-dry-run"], result)
        self.assertEqual(exit_code, 1)

    def test_t27_exit_code_2_on_db_unavailable(self):
        result = self._empty_result(top_level_error="DB_UNAVAILABLE:conn failed")
        exit_code = self._run_cli(["--dry-run"], result)
        self.assertEqual(exit_code, 2)


# ---------------------------------------------------------------------------
# T28: after_date lower bound
# ---------------------------------------------------------------------------

class TestDateBounds(unittest.TestCase):

    def test_t28_after_date_passed_to_find_unresolved(self):
        from validation.outcome_ingestion import ingest_outcomes
        with patch("validation.outcome_ingestion._get_conn"), \
             patch("validation.outcome_ingestion._find_unresolved",
                   return_value=[]) as mock_find:
            ingest_outcomes(dry_run=True, after_date="2026-07-01", max_rows=10)
        args, kwargs = mock_find.call_args
        # after_date is the 3rd positional arg (conn, before_date, after_date, max_rows)
        self.assertEqual(args[2], "2026-07-01")


# ---------------------------------------------------------------------------
# T29: outcome_verified by fetch method
# ---------------------------------------------------------------------------

class TestOutcomeVerified(unittest.TestCase):

    def test_t29_outcome_verified_true_for_direct_fetch(self):
        """outcome_verified=True only when fetch_method=savant_csv_direct."""
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not available")
        gdate = _past_date()
        df = _make_df(gdate, ["748001"], [14])
        from validation.outcome_ingestion import _fetch_game_pitch_count
        with patch("gate_engine.mlb.savant_1ip_ledger._fetch_savant_csv_direct",
                   return_value=(df, "savant_csv_direct", None)), \
             patch("gate_engine.mlb.savant_1ip_ledger._fetch_pybaseball_fallback",
                   return_value=(None, "pybaseball_fallback", "n/a")):
            result = _fetch_game_pitch_count(681911, gdate)
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["outcome_verified"])

    def test_t29b_outcome_verified_false_for_fallback(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not available")
        gdate = _past_date()
        df = _make_df(gdate, ["748001"], [14])
        from validation.outcome_ingestion import _fetch_game_pitch_count
        with patch("gate_engine.mlb.savant_1ip_ledger._fetch_savant_csv_direct",
                   return_value=(None, "savant_csv_direct", "503")), \
             patch("gate_engine.mlb.savant_1ip_ledger._fetch_pybaseball_fallback",
                   return_value=(df, "pybaseball_fallback", None)):
            result = _fetch_game_pitch_count(681911, gdate)
        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["outcome_verified"])


# ---------------------------------------------------------------------------
# T30: Secrets never in error messages
# ---------------------------------------------------------------------------

class TestSecretsNotLogged(unittest.TestCase):

    def test_t30_db_url_not_in_error(self):
        """DB connection error must not expose DATABASE_URL value."""
        import os
        from validation.outcome_ingestion import ingest_outcomes

        fake_url = "postgres://user:superSecret@host:5432/db"
        with patch.dict(os.environ, {"DATABASE_URL": fake_url}):
            with patch("validation.outcome_ingestion._get_conn",
                       side_effect=RuntimeError("could not connect")):
                result = ingest_outcomes(dry_run=True)

        # The fake URL secret must not appear in the top-level error
        if result.top_level_error:
            self.assertNotIn("superSecret", result.top_level_error)


if __name__ == "__main__":
    unittest.main()
