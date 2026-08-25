"""
gate_engine/tests/test_settlement_idempotency_db.py
FOLLOWUP_194 — Database-backed behavioral idempotency fixture

Proves that re-running the real settlement grading path against persisted
DB state produces:
  • zero duplicate outcome rows (row count for the test key never exceeds 1)
  • zero second settlement effect  (UPDATE WHERE settlement_status='OPEN'
    matches 0 rows on the re-run because the row is already SETTLED)
  • no additional state transition beyond the first settlement (DB fields
    brier_score, result / selected_side_result unchanged between reads)

This is a BEHAVIORAL proof using a real PostgreSQL connection; it is NOT a
source-text inspection.  The existing cursor-mock tests in
test_settlement_reliability.py are completely separate and unchanged.

Two grading paths are covered exactly as the requirement specifies:
  PROP   — _grade_open_prop_settlements (llp_event_settlements table)
  KALSHI — _grade_open_kalshi_settlements (kalshi_forecast_ledger table)

All tests are skipped automatically when DATABASE_URL is not set, so they
are safe for CI environments that have no database.

Implementation notes
--------------------
PROP path:
  _ensure_tables() issues ALTER TABLE … ADD COLUMN IF NOT EXISTS to
  guarantee that the raw_row JSONB column is present.  The production DDL
  in llp_stage2_tables.py already declares this column; the ALTER is an
  idempotent no-op when the column exists and a one-time schema repair
  when it does not.  It does not change any Python production source file.
  reconcile_settlement is NOT patched — it runs on the real implementation.

KALSHI path:
  _fetch_kalshi_resolution is patched to return deterministic resolution
  data without making real HTTP calls.
  kalshi_engine.settlement_reconciliation.reconcile is patched to return
  calibration_include=True (the production worker passes no fill-price or
  fee fields, so the real reconcile always returns calibration_include=False;
  patching the reconcile layer is the established pattern from the existing
  cursor-mock suite — see test_kalshi_fixture_rerun_does_not_duplicate_outcome).

Production Python source diff: ZERO — this file adds test-only logic only.
"""
from __future__ import annotations

import functools
import json
import os
import unittest
import uuid
from unittest.mock import patch

try:
    import psycopg2
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

import gate_engine.settlement_worker as sw


_SKIP_REASON = "DATABASE_URL not set or psycopg2 not available"

# Deterministic Kalshi resolution: YES wins
_KALSHI_RESOLUTION = {"yes_resolved": True, "closing_price_cents": 99}


# ---------------------------------------------------------------------------
# Skip decorator
# ---------------------------------------------------------------------------

def _needs_db(test_func):
    """Skip the decorated test when DATABASE_URL is absent or psycopg2 missing."""
    @functools.wraps(test_func)
    def wrapper(self, *args, **kwargs):
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            self.skipTest(_SKIP_REASON)
        return test_func(self, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_conn():
    """Open a fresh psycopg2 connection using DATABASE_URL."""
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)


_PROP_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS llp_event_settlements (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at              TIMESTAMPTZ,
    event_key               TEXT NOT NULL,
    run_id                  TEXT,
    decision_id             BIGINT,
    selected_side           TEXT NOT NULL,
    CONSTRAINT llp_event_settlements_unique_per_run_side
        UNIQUE (run_id, event_key, selected_side),
    official_event_result   TEXT,
    selected_side_result    TEXT,
    settlement_status       TEXT DEFAULT 'OPEN',
    settlement_source       TEXT,
    model_probability       NUMERIC,
    brier_score             NUMERIC,
    log_loss                NUMERIC,
    calibration_bucket      TEXT,
    entry_price             NUMERIC,
    closing_price           NUMERIC,
    clv                     NUMERIC,
    gross_pnl               NUMERIC,
    net_pnl                 NUMERIC,
    process_pass_fail       TEXT,
    failure_category        TEXT,
    dominant_failure_tag    TEXT,
    is_primary_observation  BOOLEAN DEFAULT TRUE,
    duplicate_suppressed    BOOLEAN DEFAULT FALSE,
    can_execute             BOOLEAN NOT NULL DEFAULT FALSE,
    execution_rule          TEXT    NOT NULL
                            DEFAULT 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS',
    notes                   TEXT
)
"""

_KALSHI_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS kalshi_forecast_ledger (
    id                  SERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    market_ticker       TEXT NOT NULL,
    event_ticker        TEXT,
    contract_title      TEXT,
    category            TEXT,
    side_yes_no         TEXT NOT NULL,
    model_probability   NUMERIC NOT NULL,
    confidence_low      NUMERIC,
    confidence_high     NUMERIC,
    kalshi_price        NUMERIC,
    entry_price         NUMERIC,
    best_bid            NUMERIC,
    best_ask            NUMERIC,
    spread              NUMERIC,
    depth_score         TEXT,
    fee_estimate        NUMERIC,
    adjusted_edge       NUMERIC,
    max_playable_price  NUMERIC,
    label               TEXT NOT NULL,
    market_bucket       TEXT,
    settlement_source   TEXT,
    settlement_status   TEXT DEFAULT 'OPEN',
    closing_price       NUMERIC,
    result              TEXT,
    brier_score         NUMERIC,
    clv                 NUMERIC,
    net_pnl             NUMERIC,
    dominant_failure_tag TEXT,
    notes               TEXT,
    mode                TEXT DEFAULT 'paper'
)
"""


def _ensure_tables(conn: "psycopg2.connection") -> None:
    """
    CREATE IF NOT EXISTS for both settlement tables; also ensures the
    raw_row JSONB column is present on llp_event_settlements.

    The production DDL (llp_stage2_tables.py) already declares raw_row.
    ALTER TABLE … ADD COLUMN IF NOT EXISTS is idempotent: it is a no-op
    when the column exists and a one-time schema repair otherwise.
    No Python production source file is modified.
    """
    with conn.cursor() as cur:
        cur.execute(_PROP_TABLE_DDL)
        # Ensure raw_row column exists — it may be absent if the table was
        # created before the column was added to the DDL.
        cur.execute(
            "ALTER TABLE llp_event_settlements"
            " ADD COLUMN IF NOT EXISTS raw_row JSONB"
        )
        cur.execute(_KALSHI_TABLE_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Prop settlement — DB-backed idempotency
# ---------------------------------------------------------------------------

class TestPropSettlementIdempotencyDB(unittest.TestCase):
    """
    Database-backed behavioral idempotency proof for the prop grading path.

    Each test:
      1. INSERTs a real OPEN row into llp_event_settlements (raw_row
         column guaranteed to exist by _ensure_tables).
      2. Calls _grade_open_prop_settlements with a real psycopg2 cursor
         and a real conn.commit() — no cursor mocks.
      3. Queries the DB to confirm the row was settled.
      4. Calls the grader a second time with a fresh connection.
      5. Queries the DB again and asserts:
           – row count for the test event_key is still exactly 1
           – settlement_status is still SETTLED (no second transition)
           – graded count from run 2 is 0
           – DB metric fields unchanged between reads
    """

    @classmethod
    def setUpClass(cls):
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            return
        conn = _open_conn()
        _ensure_tables(conn)
        conn.close()

    def setUp(self):
        self._run_id     = f"test-followup194-{uuid.uuid4().hex}"
        self._event_key  = f"TEST:FOLLOWUP194:PROP:{uuid.uuid4().hex}"
        self._inserted_ids: list[int] = []

    def tearDown(self):
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            return
        if not self._inserted_ids:
            return
        try:
            conn = _open_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM llp_event_settlements WHERE id = ANY(%s)",
                        (self._inserted_ids,),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # never let teardown mask a test failure

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _insert_open_prop_row(
        self,
        side: str = "OVER",
        official_result: str = "HOME_WIN",
        selected_is_home: bool = True,
    ) -> int:
        """
        INSERT one OPEN prop settlement row; return its generated id.

        raw_row makes reconcile_settlement deterministic without any
        network call:
          HOME_WIN + selected_side_is_home=True  → model_result='WIN'
          AWAY_WIN + selected_side_is_home=True  → model_result='LOSS'
        """
        raw_row = {
            "official_event_result":    official_result,
            "selected_side":            side,
            "selected_side_is_home":    selected_is_home,
            "platform_display_result":  "WIN" if selected_is_home else "LOSS",
            "platform_payment":         90.91,
            "stake":                    100.0,
            "promo_protection_active":  False,
        }
        conn = _open_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO llp_event_settlements
                        (event_key, run_id, selected_side,
                         official_event_result, settlement_status,
                         model_probability, entry_price, closing_price,
                         raw_row)
                    VALUES (%s, %s, %s, %s, 'OPEN',
                            %s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        self._event_key,
                        self._run_id,
                        side,
                        official_result,
                        0.62, -110, -115,
                        json.dumps(raw_row),
                    ),
                )
                row_id: int = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        self._inserted_ids.append(row_id)
        return row_id

    def _query_prop_row(self, row_id: int) -> dict:
        conn = _open_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, settlement_status, selected_side_result,
                           brier_score, process_pass_fail
                    FROM llp_event_settlements WHERE id = %s
                    """,
                    (row_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return {}
        return {
            "id":                   row[0],
            "settlement_status":    row[1],
            "selected_side_result": row[2],
            "brier_score":          row[3],
            "process_pass_fail":    row[4],
        }

    def _count_rows_for_event_key(self) -> int:
        conn = _open_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM llp_event_settlements"
                    " WHERE event_key = %s",
                    (self._event_key,),
                )
                return int(cur.fetchone()[0])
        finally:
            conn.close()

    def _run_prop_grader(self) -> int:
        """
        Open a fresh connection, run _grade_open_prop_settlements with a
        very large batch cap so our test row is guaranteed to be included
        in the SELECT, then close and return the graded count.

        reconcile_settlement is NOT patched — it runs for real.
        The raw_row fixture is crafted so that reconcile_settlement
        produces a deterministic result without any network call.
        """
        conn = _open_conn()
        try:
            with conn.cursor() as cur, \
                 patch.object(sw, "_BATCH_SIZE", 1_000_000):
                graded = sw._grade_open_prop_settlements(cur, conn)
        finally:
            conn.close()
        return graded

    # ── Tests ─────────────────────────────────────────────────────────────────

    @_needs_db
    def test_prop_open_row_is_settled_on_first_run(self):
        """
        Run 1 against a fresh OPEN row: status transitions to SETTLED,
        reconcile_settlement derives WIN, process_pass_fail=PASS, and
        brier_score is written.
        """
        row_id = self._insert_open_prop_row(
            official_result="HOME_WIN", selected_is_home=True
        )

        pre = self._query_prop_row(row_id)
        self.assertEqual(pre["settlement_status"], "OPEN",
                         "Precondition: row must start OPEN")

        self._run_prop_grader()

        post = self._query_prop_row(row_id)
        self.assertEqual(post["settlement_status"], "SETTLED",
                         "After run 1: row must be SETTLED in the DB")
        self.assertEqual(post["selected_side_result"], "WIN",
                         "HOME_WIN + selected_side_is_home=True "
                         "→ reconcile_settlement must derive WIN")
        self.assertEqual(post["process_pass_fail"], "PASS",
                         "WIN result must produce process_pass_fail=PASS")
        self.assertIsNotNone(post["brier_score"],
                             "brier_score must be written on first settlement")

    @_needs_db
    def test_prop_rerun_produces_zero_new_outcome_rows(self):
        """
        Core fixture-settlement proof (WIN path, user requirement):

        "fixture settlement should prove rerunning does not duplicate the outcome"

        Sequence:
          Start: 1 OPEN row in DB for test event_key.
          Run 1: _grade_open_prop_settlements → row SETTLED, graded ≥ 1.
          Run 2: _grade_open_prop_settlements → UPDATE WHERE
                 settlement_status='OPEN' matches 0 rows for our row →
                 graded = 0, no commit issued for this row.

        Assertions against the real database:
          row_count == 1 before, after run 1, after run 2 (no duplication)
          settlement_status == SETTLED after run 1 AND after run 2
          graded on run 2 == 0
          brier_score and selected_side_result unchanged between reads
        """
        row_id = self._insert_open_prop_row(
            official_result="HOME_WIN", selected_is_home=True
        )
        self.assertEqual(self._count_rows_for_event_key(), 1,
                         "Precondition: exactly 1 row before any run")

        # ── Run 1 ──────────────────────────────────────────────────────────
        first_graded = self._run_prop_grader()
        self.assertGreaterEqual(first_graded, 1,
                                "Run 1 must grade at least our test row")

        self.assertEqual(self._count_rows_for_event_key(), 1,
                         "After run 1: row count must still be exactly 1 — "
                         "settlement must not duplicate the outcome row")

        post_run1 = self._query_prop_row(row_id)
        self.assertEqual(post_run1["settlement_status"], "SETTLED",
                         "After run 1: row must be SETTLED")

        # ── Run 2 ──────────────────────────────────────────────────────────
        second_graded = self._run_prop_grader()

        post_run2 = self._query_prop_row(row_id)

        # ── Invariant assertions ────────────────────────────────────────────
        self.assertEqual(second_graded, 0,
                         "Run 2 graded count must be 0: the SETTLED row is "
                         "excluded by WHERE settlement_status='OPEN'; the "
                         "UPDATE WHERE id=%s AND settlement_status='OPEN' "
                         "matches zero rows; no second settlement effect occurs")
        self.assertEqual(self._count_rows_for_event_key(), 1,
                         "After run 2: row count must remain exactly 1 — "
                         "re-running the grader must not create duplicate "
                         "outcome rows for this event_key")
        self.assertEqual(post_run2["settlement_status"], "SETTLED",
                         "DB state must still be SETTLED after run 2 — "
                         "no second state transition permitted")
        self.assertEqual(post_run2["selected_side_result"],
                         post_run1["selected_side_result"],
                         "selected_side_result must not change on re-run")
        self.assertEqual(post_run2["brier_score"],
                         post_run1["brier_score"],
                         "brier_score must not change on re-run")

    @_needs_db
    def test_prop_loss_outcome_rerun_produces_zero_new_rows(self):
        """
        LOSS outcome path:
          AWAY_WIN + selected_side_is_home=True → model_result='LOSS'

        Idempotency must hold for LOSS outcomes, not just WIN.
        """
        row_id = self._insert_open_prop_row(
            official_result="AWAY_WIN", selected_is_home=True
        )

        # Run 1
        self._run_prop_grader()

        post_run1 = self._query_prop_row(row_id)
        self.assertEqual(post_run1["settlement_status"], "SETTLED")
        self.assertEqual(post_run1["selected_side_result"], "LOSS",
                         "AWAY_WIN + home selected → LOSS")

        count_before_run2 = self._count_rows_for_event_key()

        # Run 2
        second_graded = self._run_prop_grader()

        post_run2 = self._query_prop_row(row_id)

        self.assertEqual(second_graded, 0,
                         "LOSS path: run 2 must grade zero rows")
        self.assertEqual(self._count_rows_for_event_key(), count_before_run2,
                         "LOSS path: row count must not change on re-run")
        self.assertEqual(post_run2["settlement_status"], "SETTLED")
        self.assertEqual(post_run2["selected_side_result"], "LOSS",
                         "LOSS result must not be altered by re-run")
        self.assertEqual(post_run2["brier_score"], post_run1["brier_score"],
                         "LOSS brier_score must not change on re-run")


# ---------------------------------------------------------------------------
# Kalshi settlement — DB-backed idempotency
# ---------------------------------------------------------------------------

class TestKalshiSettlementIdempotencyDB(unittest.TestCase):
    """
    Database-backed behavioral idempotency proof for the Kalshi grading path.

    Two patches applied (matching the established cursor-mock pattern):
      _fetch_kalshi_resolution — patched to return deterministic resolution
        without real HTTP calls (same reason the cursor-mock suite patches it)
      kalshi_engine.settlement_reconciliation.reconcile — patched to return
        calibration_include=True.  The production worker passes no fill-price,
        quantity, or fee fields to reconcile(), so the real implementation
        always returns calibration_include=False (price_fields_ok=False) →
        the grader hits continue and grades nothing.  Patching reconcile is
        the established pattern in the cursor-mock suite; see
        test_kalshi_fixture_rerun_does_not_duplicate_outcome.

    All DB operations (SELECT, UPDATE, conn.commit) run against the real
    PostgreSQL database — no cursor mocks.
    """

    @classmethod
    def setUpClass(cls):
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            return
        conn = _open_conn()
        _ensure_tables(conn)
        conn.close()

    def setUp(self):
        self._ticker         = f"KXTEST194-{uuid.uuid4().hex[:14].upper()}"
        self._inserted_ids: list[int] = []

    def tearDown(self):
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            return
        if not self._inserted_ids:
            return
        try:
            conn = _open_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM kalshi_forecast_ledger WHERE id = ANY(%s)",
                        (self._inserted_ids,),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _insert_open_kalshi_row(self, side: str = "YES") -> int:
        conn = _open_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO kalshi_forecast_ledger
                        (market_ticker, side_yes_no, model_probability,
                         label, settlement_status)
                    VALUES (%s, %s, %s, 'KALSHI_SCOUT', 'OPEN')
                    RETURNING id
                    """,
                    (self._ticker, side, 0.58),
                )
                row_id: int = cur.fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        self._inserted_ids.append(row_id)
        return row_id

    def _query_kalshi_row(self, row_id: int) -> dict:
        conn = _open_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, settlement_status, result, brier_score
                    FROM kalshi_forecast_ledger WHERE id = %s
                    """,
                    (row_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return {}
        return {
            "id":                row[0],
            "settlement_status": row[1],
            "result":            row[2],
            "brier_score":       row[3],
        }

    def _count_rows_for_ticker(self) -> int:
        conn = _open_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM kalshi_forecast_ledger"
                    " WHERE market_ticker = %s",
                    (self._ticker,),
                )
                return int(cur.fetchone()[0])
        finally:
            conn.close()

    def _run_kalshi_grader(self, *, final_result: str = "WIN") -> int:
        """
        Open a fresh connection and run _grade_open_kalshi_settlements.

        Patches applied (same as the cursor-mock suite):
          _fetch_kalshi_resolution → deterministic resolution dict
          reconcile               → calibration_include=True + final_result
          FILL_STATUS_FILLED / SS_SETTLED → string constants that reconcile
            checks; patching ensures the import-inside-function form used
            by the worker matches what we return from reconcile.
        """
        reconcile_result = {
            "calibration_include":          True,
            "final_result":                 final_result,
            "clv_cents":                    5,
            "clv_percent":                  0.05,
            "net_pnl_after_fees_cents":     95,
        }
        conn = _open_conn()
        try:
            with conn.cursor() as cur, \
                 patch.object(sw, "_fetch_kalshi_resolution",
                              return_value=_KALSHI_RESOLUTION), \
                 patch("kalshi_engine.settlement_reconciliation.reconcile",
                       return_value=reconcile_result), \
                 patch("kalshi_engine.settlement_reconciliation.FILL_STATUS_FILLED",
                       "FILLED"), \
                 patch("kalshi_engine.settlement_reconciliation.SS_SETTLED",
                       "SETTLED"), \
                 patch.object(sw, "_BATCH_SIZE", 1_000_000):
                graded = sw._grade_open_kalshi_settlements(cur, conn)
        finally:
            conn.close()
        return graded

    # ── Tests ─────────────────────────────────────────────────────────────────

    @_needs_db
    def test_kalshi_open_row_is_settled_on_first_run(self):
        """
        Run 1: OPEN Kalshi row transitions to SETTLED; result='YES' is
        written; brier_score is set.
        """
        row_id = self._insert_open_kalshi_row(side="YES")

        pre = self._query_kalshi_row(row_id)
        self.assertEqual(pre["settlement_status"], "OPEN",
                         "Precondition: Kalshi row must start OPEN")

        self._run_kalshi_grader(final_result="WIN")

        post = self._query_kalshi_row(row_id)
        self.assertEqual(post["settlement_status"], "SETTLED",
                         "After run 1: Kalshi row must be SETTLED in the DB")
        self.assertEqual(post["result"], "YES",
                         "final_result=WIN → result must be 'YES'")
        self.assertIsNotNone(post["brier_score"],
                             "brier_score must be written on first Kalshi settlement")

    @_needs_db
    def test_kalshi_yes_side_rerun_produces_zero_new_outcome_rows(self):
        """
        Core Kalshi fixture-settlement proof (YES side):

        "fixture settlement should prove rerunning does not duplicate the outcome"

        Sequence:
          Start: 1 OPEN row in kalshi_forecast_ledger for test ticker.
          Run 1: _grade_open_kalshi_settlements → row SETTLED, result='YES',
                 graded ≥ 1.
          Run 2: UPDATE WHERE settlement_status='OPEN' AND id=%s matches 0
                 rows → graded = 0, no second commit issued for this row.

        Assertions against the real database:
          row_count == 1 throughout
          settlement_status == SETTLED after run 1 AND after run 2
          graded on run 2 == 0
          result and brier_score unchanged between reads
        """
        row_id = self._insert_open_kalshi_row(side="YES")
        self.assertEqual(self._count_rows_for_ticker(), 1,
                         "Precondition: exactly 1 row for test ticker")

        # ── Run 1 ──────────────────────────────────────────────────────────
        first_graded = self._run_kalshi_grader(final_result="WIN")
        self.assertGreaterEqual(first_graded, 1,
                                "Run 1 must grade at least our test row")

        self.assertEqual(self._count_rows_for_ticker(), 1,
                         "After run 1: row count must still be exactly 1 — "
                         "settlement must not duplicate the Kalshi outcome row")

        post_run1 = self._query_kalshi_row(row_id)
        self.assertEqual(post_run1["settlement_status"], "SETTLED")
        self.assertEqual(post_run1["result"], "YES")

        # ── Run 2 ──────────────────────────────────────────────────────────
        second_graded = self._run_kalshi_grader(final_result="WIN")

        post_run2 = self._query_kalshi_row(row_id)

        # ── Invariant assertions ────────────────────────────────────────────
        self.assertEqual(second_graded, 0,
                         "Kalshi run 2 graded count must be 0: the SETTLED row "
                         "is excluded by WHERE settlement_status='OPEN'; the "
                         "UPDATE WHERE id=%s AND settlement_status='OPEN' "
                         "matches zero rows; no second settlement effect occurs")
        self.assertEqual(self._count_rows_for_ticker(), 1,
                         "After Kalshi run 2: row count must remain exactly 1 — "
                         "re-running must not create duplicate outcome rows")
        self.assertEqual(post_run2["settlement_status"], "SETTLED",
                         "Kalshi DB state must still be SETTLED after run 2 — "
                         "no second state transition permitted")
        self.assertEqual(post_run2["result"], post_run1["result"],
                         "result field must not change on Kalshi re-run")
        self.assertEqual(post_run2["brier_score"], post_run1["brier_score"],
                         "brier_score must not change on Kalshi re-run")

    @_needs_db
    def test_kalshi_no_side_rerun_produces_zero_new_outcome_rows(self):
        """
        NO-side Kalshi path:
          final_result='LOSS' → result='NO' written to DB on run 1.
          Re-run produces zero additional state changes (same invariant).
        """
        row_id = self._insert_open_kalshi_row(side="NO")
        self.assertEqual(self._count_rows_for_ticker(), 1,
                         "Precondition: 1 row for test ticker")

        # Run 1
        self._run_kalshi_grader(final_result="LOSS")

        post_run1 = self._query_kalshi_row(row_id)
        self.assertEqual(post_run1["settlement_status"], "SETTLED")
        self.assertEqual(post_run1["result"], "NO",
                         "final_result=LOSS → result must be 'NO'")

        count_before_run2 = self._count_rows_for_ticker()

        # Run 2
        second_graded = self._run_kalshi_grader(final_result="LOSS")

        post_run2 = self._query_kalshi_row(row_id)

        self.assertEqual(second_graded, 0,
                         "NO-side run 2 must grade zero rows")
        self.assertEqual(self._count_rows_for_ticker(), count_before_run2,
                         "NO-side: row count must not change on re-run")
        self.assertEqual(post_run2["settlement_status"], "SETTLED")
        self.assertEqual(post_run2["result"], "NO",
                         "NO result must not be altered by re-run")
        self.assertEqual(post_run2["brier_score"], post_run1["brier_score"],
                         "NO-side brier_score must not change on re-run")


if __name__ == "__main__":
    unittest.main()
