"""Real PostgreSQL proof for the NBA shadow-receipt timestamp boundary.

The migration is deliberately unapplied in production. This module applies it
only to an authorized disposable PostgreSQL database and proves that callers
cannot supply or backdate ``created_at``, including inside a long transaction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

psycopg = pytest.importorskip(
    "psycopg", reason="psycopg is required for database-backed migration proof"
)
from psycopg.rows import dict_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/20261103_nba_event_shadow_prediction_receipts.sql"
TRIGGER = "trg_wow_nba_event_shadow_predictions_server_created_at"
TABLE = "public.wow_nba_event_shadow_predictions"


def _dsn() -> str:
    dsn = os.environ.get("WOW_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip(
            "WOW_TEST_POSTGRES_UNAVAILABLE: set WOW_TEST_POSTGRES_DSN to an "
            "authorized disposable PostgreSQL database"
        )
    parsed = urlparse(dsn)
    # pgserver supplies a local Unix-socket DSN with no hostname. That is still
    # a local disposable cluster; reject only an explicit non-local network host.
    if parsed.hostname not in {None, "localhost", "127.0.0.1", "::1"}:
        pytest.skip("WOW_TEST_POSTGRES_DSN must target a local disposable database")
    return dsn


def _connect(*, autocommit: bool = True):
    return psycopg.connect(
        _dsn(), autocommit=autocommit, row_factory=dict_row
    )


@pytest.fixture(scope="module")
def db():
    with _connect() as conn:
        conn.execute(
            """
            do $$
            begin
              if not exists (select 1 from pg_roles where rolname = 'anon') then
                create role anon nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'authenticated') then
                create role authenticated nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'service_role') then
                create role service_role nologin bypassrls;
              end if;
            end $$;
            """
        )
        sql = MIGRATION.read_text(encoding="utf-8")
        conn.execute(sql)
        conn.execute(sql)  # replay must not create a second trigger
        yield conn
        conn.execute(f"drop table if exists {TABLE} cascade")
        conn.execute(
            "drop function if exists "
            "public.wow_nba_event_shadow_predictions_server_created_at()"
        )


def _insert(conn, *, created_at: datetime, event_start: datetime):
    now = datetime.now(timezone.utc)
    source_snapshot_id = "sha256:" + uuid4().hex * 2
    row = conn.execute(
        """
        insert into public.wow_nba_event_shadow_predictions (
          created_at, official_event_id, event_start_time_utc,
          home_team_id, away_team_id, source_snapshot_id,
          source_snapshot_timestamp, model_timestamp, certification_id,
          model_artifact_id, model_artifact_version, model_artifact_sha256,
          calibration_artifact_id, calibration_artifact_sha256,
          raw_home_probability, calibrated_home_probability,
          calibrated_home_lower_bound, calibrated_home_upper_bound,
          prediction_sha256
        ) values (
          %s, 'espn-401809999', %s,
          'espn-1610612749', 'espn-1610612741', %s,
          %s, %s, %s,
          %s, 'NBA_FITTED_V1', %s,
          %s, %s,
          0.57, 0.55,
          0.50, 0.60,
          %s
        ) returning *
        """,
        (
            created_at,
            event_start,
            source_snapshot_id,
            now - timedelta(minutes=20),
            now - timedelta(minutes=10),
            f"cert-{uuid4().hex}",
            f"model-{uuid4().hex}",
            "b" * 64,
            f"cal-{uuid4().hex}",
            "c" * 64,
            uuid4().hex * 2,
        ),
    ).fetchone()
    assert row is not None
    return row


def test_created_at_default_is_removed_and_trigger_is_before_insert(db):
    column = db.execute(
        """
        select column_default
          from information_schema.columns
         where table_schema = 'public'
           and table_name = 'wow_nba_event_shadow_predictions'
           and column_name = 'created_at'
        """
    ).fetchone()
    assert column == {"column_default": None}
    trigger = db.execute(
        """
        select pg_get_triggerdef(oid) as definition
          from pg_trigger
         where tgrelid = 'public.wow_nba_event_shadow_predictions'::regclass
           and tgname = %s
           and not tgisinternal
        """,
        (TRIGGER,),
    ).fetchone()
    assert trigger is not None
    assert "before insert" in trigger["definition"].lower()


def test_backdated_created_at_is_replaced_with_server_clock(db):
    supplied = datetime(2000, 1, 1, tzinfo=timezone.utc)
    event_start = datetime.now(timezone.utc) + timedelta(hours=3)
    before = db.execute("select pg_catalog.clock_timestamp() as t").fetchone()["t"]
    row = _insert(db, created_at=supplied, event_start=event_start)
    after = db.execute("select pg_catalog.clock_timestamp() as t").fetchone()["t"]
    assert before <= row["created_at"] <= after
    assert row["created_at"] != supplied


def test_transaction_start_is_not_used_as_the_receipt_time():
    with _connect(autocommit=False) as tx:
        transaction_start = tx.execute("select now() as t").fetchone()["t"]
        tx.execute("select pg_sleep(0.25)")
        row = _insert(
            tx,
            created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            event_start=datetime.now(timezone.utc) + timedelta(hours=3),
        )
        assert row["created_at"] > transaction_start + timedelta(milliseconds=150)
        tx.commit()


def test_receipt_across_kickoff_is_rejected_even_with_an_old_created_at(db):
    before = db.execute(
        "select count(*) as n from public.wow_nba_event_shadow_predictions"
    ).fetchone()["n"]
    with pytest.raises(psycopg.errors.CheckViolation, match="nba_shadow_prospective"):
        _insert(
            db,
            created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            event_start=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    after = db.execute(
        "select count(*) as n from public.wow_nba_event_shadow_predictions"
    ).fetchone()["n"]
    assert after == before


def test_replay_is_idempotent_and_service_role_cannot_rewrite_receipts(db):
    trigger_count = db.execute(
        """
        select count(*) as n
          from pg_trigger
         where tgrelid = 'public.wow_nba_event_shadow_predictions'::regclass
           and not tgisinternal
        """
    ).fetchone()["n"]
    assert trigger_count == 1
    privileges = db.execute(
        """
        select has_table_privilege('service_role', %s, 'INSERT') as can_insert,
               has_table_privilege('service_role', %s, 'UPDATE') as can_update,
               has_table_privilege('service_role', %s, 'TRIGGER') as can_trigger
        """,
        (TABLE, TABLE, TABLE),
    ).fetchone()
    assert privileges == {
        "can_insert": True,
        "can_update": False,
        "can_trigger": False,
    }
