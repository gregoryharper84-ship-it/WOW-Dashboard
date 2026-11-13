"""Run exact SQL predicate fragments against adversarial SELECT-only fixtures.

Uses stdlib SQLite with a public schema alias. This directly exercises the
production collision CTE and lineup EXISTS expression, rather than checking
only that the source text contains expected words. No remote writes.
"""
import re
import sqlite3
from pathlib import Path

SQL = Path(__file__).parents[1] / "v17/sql/mlb_joint_asof_replay_preflight_readonly.sql"


def _connection():
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS public")
    db.executescript("""
      CREATE TABLE public.wow_mlb_forward_shadow_events (
        shadow_event_id TEXT, official_event_id TEXT, official_date TEXT,
        event_start_time TEXT, home_team_id INTEGER, away_team_id INTEGER,
        model_timestamp TEXT
      );
      CREATE TABLE public.wow_mlb_forward_lineup_snapshots (
        shadow_event_id TEXT, official_event_id TEXT,
        home_team_id INTEGER, away_team_id INTEGER, lineup_status TEXT,
        strict_pregame_provenance INTEGER,
        official_pitch_events_at_capture INTEGER,
        official_completed_plays_at_capture INTEGER,
        http_status INTEGER, can_execute INTEGER, captured_at TEXT
      );
    """)
    db.executemany("INSERT INTO public.wow_mlb_forward_shadow_events VALUES (?,?,?,?,?,?,?)", [
      ("valid", "SAFE", "2026-10-01", "2026-10-01T20:00:00Z", 1, 2, "2026-10-01T19:00:00Z"),
      ("late", "LATE", "2026-10-02", "2026-10-02T20:00:00Z", 3, 4, "2026-10-02T19:00:00Z"),
      ("collision-scored", "COLLIDE", "2026-10-03", "2026-10-03T20:00:00Z", 5, 6, "2026-10-03T19:00:00Z"),
      ("collision-unscored", "COLLIDE", "2026-10-04", "2026-10-04T20:00:00Z", 5, 7, "2026-10-04T19:00:00Z"),
    ])
    db.executemany("INSERT INTO public.wow_mlb_forward_lineup_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
      ("valid", "SAFE", 1, 2, "CONFIRMED", 1, 0, 0, 200, 0, "2026-10-01T18:59:00Z"),
      ("late", "LATE", 3, 4, "CONFIRMED", 1, 0, 0, 200, 0, "2026-10-02T19:01:00Z"),
    ])
    return db


def test_unscored_conflicting_archived_revision_is_excluded():
    sql = SQL.read_text(encoding="utf-8")
    match = re.search(r"event_identity_collisions AS \((.*?)\),\s*official_schedule_final AS", sql, re.S)
    assert match, "actual SQL collision CTE missing"
    query = (
        "WITH scored AS (SELECT * FROM public.wow_mlb_forward_shadow_events "
        "WHERE shadow_event_id != 'collision-unscored') "
        "SELECT official_event_id FROM (" + match.group(1).strip() + ") collisions"
    )
    with _connection() as db:
        collisions = {row[0] for row in db.execute(query)}
    assert collisions == {"COLLIDE"}


def test_late_lineup_cannot_qualify_but_valid_pregame_lineup_does():
    sql = SQL.read_text(encoding="utf-8")
    start = sql.index("      EXISTS (\n          SELECT 1\n          FROM public.wow_mlb_forward_lineup_snapshots l")
    end = sql.index(") AS confirmed_lineup_captured_asof", start) + 1
    expr = sql[start:end]
    query = (
        "SELECT s.shadow_event_id, " + expr +
        " AS eligible FROM public.wow_mlb_forward_shadow_events s "
        "WHERE s.shadow_event_id IN ('valid','late') ORDER BY s.shadow_event_id"
    )
    with _connection() as db:
        rows = dict(db.execute(query).fetchall())
    assert rows == {"late": 0, "valid": 1}
