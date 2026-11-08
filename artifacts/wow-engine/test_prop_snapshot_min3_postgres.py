"""Real PostgreSQL regression for the reviewed exact MLB strikeout min3 gate.

Uses an ephemeral schema and the actual migration SQL, never production data.
Runs in the existing WOW_AGENT_RUNTIME_INTEGRATION job.
"""
from __future__ import annotations

import os
from pathlib import Path
import uuid
from urllib.parse import urlparse

import pytest
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


pytestmark = pytest.mark.skipif(
    os.getenv("WOW_AGENT_RUNTIME_INTEGRATION") != "1",
    reason="requires the ephemeral Postgres integration service",
)
ROOT = Path(__file__).parent
MIGRATION = ROOT / "migrations/20261107064224_v17_mlb_strikeout_snapshot_min3.sql"


@pytest.fixture
def snapshot_db():
    schema = "min3_" + uuid.uuid4().hex
    dsn = os.environ["AGENT_RUNTIME_POSTGRES_DSN"]
    target = urlparse(dsn)
    assert target.hostname in {"localhost", "127.0.0.1"} and not target.query, "local ephemeral Postgres only"
    assert target.path == "/wow_agent_ci" and target.username == "wow_agent_ci", "CI database only"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        try:
            base = (ROOT / "prop_runtime_contract.sql").read_text().split(
                "insert into public.wow_runtime_capabilities", 1
            )[0]
            # Namespacing changes only the target schema; production function
            # bodies and privilege statements are otherwise executed verbatim.
            for content in [
                base,
                (ROOT / "migrations/20260902_prop_evidence_opponent_context.sql").read_text(),
                (ROOT / "migrations/20260904_v17_detailed_evidence_prop_snapshot.sql").read_text(),
                MIGRATION.read_text(),
                MIGRATION.read_text(),  # additive migration must be idempotent
            ]:
                namespaced = content.replace("public.", f"{schema}.")
                namespaced = namespaced.replace("search_path = public", f"search_path = {schema}")
                namespaced = namespaced.replace("search_path to 'public'", f"search_path to '{schema}'")
                conn.execute(namespaced)
            yield conn, schema
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def _read(snapshot_db, *, sport="MLB", stat="PITCHER_STRIKEOUTS", n=3, overrides=None, request_stat=None):
    conn, schema = snapshot_db
    row = {
        "source_snapshot_id": str(uuid.uuid4()),
        "event_id": "fixture-event", "event_start_time": "2030-01-02T12:00:00Z",
        "sport": sport, "player": "Fixture Player", "stat_type": stat, "line": 4.5,
        "game_log": [5] * n, "box_score_log": [{"outs": 18}] * n,
        "role_status": {"status": "ACTIVE"}, "role_timestamp": "2030-01-01T12:00:00Z",
        "opportunity_ledger": {"status": "PASS"}, "source_timestamps": {},
        "hydration_status": "PASS", "blockers": [],
        "opponent_context": {"lineup_status": "VERIFIED"},
        "detailed_evidence": {"fixture": True}, "detailed_evidence_fingerprint": "fixture-hash",
    }
    row.update(overrides or {})
    json_fields = {"game_log", "box_score_log", "role_status", "opportunity_ledger", "source_timestamps", "opponent_context", "detailed_evidence"}
    conn.execute(
        sql.SQL("insert into {}.wow_prop_evidence_snapshots ({}) values ({})").format(
            sql.Identifier(schema), sql.SQL(",").join(map(sql.Identifier, row)),
            sql.SQL(",").join(sql.Placeholder() for _ in row),
        ),
        [Jsonb(value) if key in json_fields else value for key, value in row.items()],
    )
    return conn.execute(
        sql.SQL("select {}.wow_prop_evidence_snapshot(%s,%s,%s,%s,%s,%s::numeric)").format(sql.Identifier(schema)),
        (row["source_snapshot_id"], row["event_id"], sport, row["player"], request_stat or stat, row["line"]),
    ).fetchone()[0]


@pytest.mark.parametrize("n", [3, 5, 9, 10])
def test_reviewed_strikeout_floor_reaches_evidence_without_publication(snapshot_db, n):
    result = _read(snapshot_db, n=n)
    assert result["code"] == "PROP_EVIDENCE_READY"
    assert result["opponent_context"] == {"lineup_status": "VERIFIED"}
    assert result["detailed_evidence"] == {"fixture": True}
    assert result["detailed_evidence_fingerprint"] == "fixture-hash"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("sport,stat,n", [
    ("MLB", "PITCHER_STRIKEOUTS", 2), ("MLB", "PITCHING_OUTS", 9),
    ("NFL", "PASSING_YARDS", 9), ("WNBA", "POINTS", 9),
])
def test_min3_never_weakens_other_routes_or_two_start_rows(snapshot_db, sport, stat, n):
    result = _read(snapshot_db, sport=sport, stat=stat, n=n)
    assert result["ok"] is False
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["probability_publishable"] is False


@pytest.mark.parametrize("overrides", [
    {"game_log": [True, 5, 6]}, {"box_score_log": ["bad", {}, {}]},
    {"hydration_status": "INCOMPLETE"}, {"role_status": {}},
    {"opportunity_ledger": {"status": "FAILED"}}, {"blockers": ["SOURCE_STALE"]},
])
def test_existing_mandatory_evidence_checks_survive_min3(snapshot_db, overrides):
    result = _read(snapshot_db, overrides=overrides)
    assert result["code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["can_execute"] is False


def test_snapshot_identity_and_backend_only_grants_survive(snapshot_db):
    result = _read(snapshot_db, request_stat="PITCHING_OUTS")
    assert result["code"] == "PROP_EVIDENCE_IDENTITY_MISMATCH"
    conn, schema = snapshot_db
    function = f"{schema}.wow_prop_evidence_snapshot(uuid,text,text,text,text,numeric)"
    for role in ("anon", "authenticated"):
        assert conn.execute("select has_function_privilege(%s,%s,'EXECUTE')", (role, function)).fetchone()[0] is False
    assert conn.execute("select has_function_privilege('service_role',%s,'EXECUTE')", (function,)).fetchone()[0] is True
