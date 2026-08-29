"""Regression contract for immutable governed pregame prediction writes."""
from __future__ import annotations

from pathlib import Path


HERE = Path(__file__).resolve().parent
MIGRATION = HERE / "migrations" / "20260829_immutable_pregame_lock.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_every_prediction_lock_is_backfilled_and_non_nullable():
    sql = _sql()
    assert "where locked_at is null" in sql
    assert "alter column locked_at set default now()" in sql
    assert "alter column locked_at set not null" in sql


def test_historical_backfill_scopes_trigger_disable_and_restores_it_before_commit():
    sql = _sql()
    disable = sql.index("disable trigger trg_wow_block_post_event_edit")
    backfill = sql.index("update public.wow_predictions")
    enable = sql.index("enable trigger trg_wow_block_post_event_edit")
    commit = sql.rindex("commit;")
    assert disable < backfill < enable < commit
    assert "disable trigger trg_wow_block_post_event_delete" not in sql


def test_lock_contract_is_pregame_not_event_start_only():
    sql = _sql()
    assert "every prediction is locked at insert" in sql
    assert "qualified publication requires a persisted prediction row" in sql
