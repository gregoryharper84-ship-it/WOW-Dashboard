"""Real PostgreSQL regression for the governed prop-evidence repair fallback.

Runs the actual ``wow_prop_evidence_snapshot`` function and the real evidence
table in an ephemeral schema. It proves the repair loop against SQL semantics
rather than a Python imitation: the requested snapshot is absent, the canonical
fallback locates the writer-persisted row, the original RPC revalidates it, and
pregame/hydration/identity violations remain fail-closed.

Never touches a remote database and never inserts fabricated evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import uuid
from urllib.parse import urlparse

from pydantic import BaseModel
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from prop_evidence_repair import repair_prop_evidence


pytestmark = pytest.mark.skipif(
    os.getenv("WOW_AGENT_RUNTIME_INTEGRATION") != "1",
    reason="requires the ephemeral Postgres integration service",
)

ROOT = Path(__file__).parent
MIGRATIONS = (
    ROOT / "migrations/20260902_prop_evidence_opponent_context.sql",
    ROOT / "migrations/20260904_v17_detailed_evidence_prop_snapshot.sql",
    ROOT / "migrations/20261107064224_v17_mlb_strikeout_snapshot_min3.sql",
)


class _Request(BaseModel):
    event_id: str
    event_start_time: str
    sport: str
    player: str
    stat_type: str
    line: float
    source_snapshot_id: str


@pytest.fixture
def repair_db():
    schema = "repair_" + uuid.uuid4().hex
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
            for content in [base, *(path.read_text() for path in MIGRATIONS)]:
                namespaced = content.replace("public.", f"{schema}.")
                namespaced = namespaced.replace("search_path = public", f"search_path = {schema}")
                namespaced = namespaced.replace("search_path to 'public'", f"search_path to '{schema}'")
                conn.execute(namespaced)
            yield conn, schema
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal PostgREST-shaped query over the real evidence table."""

    def __init__(self, conn, schema, table):
        self._conn = conn
        self._schema = schema
        self._table = table
        self._columns = "*"
        self._where: list[tuple[str, str, object]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def select(self, columns):
        self._columns = columns
        return self

    def eq(self, key, value):
        self._where.append((key, "=", value))
        return self

    def lt(self, key, value):
        self._where.append((key, "<", value))
        return self

    def order(self, key, desc=False):
        self._order = (key, desc)
        return self

    def limit(self, value):
        self._limit = int(value)
        return self

    def execute(self):
        names = [name.strip() for name in self._columns.split(",")]
        query = sql.SQL("select {} from {}.{}").format(
            sql.SQL(",").join(sql.Identifier(name) for name in names),
            sql.Identifier(self._schema),
            sql.Identifier(self._table),
        )
        if self._where:
            query += sql.SQL(" where ") + sql.SQL(" and ").join(
                sql.SQL("{} {} %s").format(sql.Identifier(key), sql.SQL(operator))
                for key, operator, _value in self._where
            )
        if self._order is not None:
            query += sql.SQL(" order by {} {}").format(
                sql.Identifier(self._order[0]),
                sql.SQL("desc" if self._order[1] else "asc"),
            )
        if self._limit is not None:
            query += sql.SQL(" limit {}").format(sql.Literal(self._limit))
        rows = self._conn.execute(query, [value for _key, _op, value in self._where]).fetchall()
        return _Result([dict(zip(names, row)) for row in rows])


class _Client:
    def __init__(self, conn, schema):
        self._conn = conn
        self._schema = schema

    def table(self, name):
        return _Query(self._conn, self._schema, name)


def _primary_fetch(conn, schema):
    """The actual governed ``wow_prop_evidence_snapshot`` RPC."""

    def fetch(req):
        return conn.execute(
            sql.SQL("select {}.wow_prop_evidence_snapshot(%s,%s,%s,%s,%s,%s::numeric)").format(
                sql.Identifier(schema)
            ),
            (req.source_snapshot_id, req.event_id, req.sport, req.player, req.stat_type, req.line),
        ).fetchone()[0]

    return fetch


def _insert(conn, schema, **overrides):
    now = datetime.now(timezone.utc)
    row = {
        "source_snapshot_id": str(uuid.uuid4()),
        "captured_at": (now - timedelta(minutes=5)).isoformat(),
        "event_id": "2026020276",
        "event_start_time": (now + timedelta(days=1)).isoformat(),
        "sport": "NFL",
        "player": "Puka Nacua",
        "stat_type": "PASSING_YARDS",
        "line": 284.5,
        "game_log": [270.0 + index for index in range(10)],
        "box_score_log": [{"game_id": f"game-{index}"} for index in range(10)],
        "role_status": {"status": "ACTIVE", "role": "STARTER"},
        "role_timestamp": (now - timedelta(minutes=5)).isoformat(),
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"OFFICIAL_BOX_SCORE": (now - timedelta(minutes=6)).isoformat()},
        "hydration_status": "PASS",
        "blockers": [],
        "evidence_version": "PROP_EVIDENCE_V1",
        "can_execute": False,
    }
    row.update(overrides)
    json_fields = {
        "game_log",
        "box_score_log",
        "role_status",
        "opportunity_ledger",
        "source_timestamps",
    }
    conn.execute(
        sql.SQL("insert into {}.wow_prop_evidence_snapshots ({}) values ({})").format(
            sql.Identifier(schema),
            sql.SQL(",").join(map(sql.Identifier, row)),
            sql.SQL(",").join(sql.Placeholder() for _ in row),
        ),
        [Jsonb(value) if key in json_fields else value for key, value in row.items()],
    )
    return row


def _request(**overrides):
    values = {
        "event_id": " 2026020276 ",
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": "nfl",
        "player": "  Puka   Nacua ",
        "stat_type": "pass-yards",
        "line": 284.5,
        "source_snapshot_id": str(uuid.uuid4()),
    }
    values.update(overrides)
    return _Request(**values)


def _count(conn, schema):
    return conn.execute(
        sql.SQL("select count(*) from {}.wow_prop_evidence_snapshots").format(sql.Identifier(schema))
    ).fetchone()[0]


def test_repair_recovers_exact_writer_identity_through_the_real_rpc(repair_db):
    conn, schema = repair_db
    persisted = _insert(conn, schema)
    req = _request()

    result = repair_prop_evidence(
        req,
        primary_fetch=_primary_fetch(conn, schema),
        client=_Client(conn, schema),
    )

    assert result["code"] == "PROP_EVIDENCE_READY"
    assert result["acquisition_repair_status"] == "RECOVERED_FROM_GOVERNED_SNAPSHOT"
    assert result["requested_source_snapshot_id"] == req.source_snapshot_id
    assert result["effective_source_snapshot_id"] == persisted["source_snapshot_id"]
    assert result["event_id"] == "2026020276"
    assert result["player"] == "Puka Nacua"
    assert result["sport"] == "NFL"
    assert result["stat_type"] == "PASSING_YARDS"
    assert float(result["line"]) == 284.5
    assert result["captured_at"] < req.event_start_time
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert [(attempt["path"], attempt["status"]) for attempt in result["acquisition_attempts"]] == [
        ("REQUESTED_SNAPSHOT", "FAILED"),
        ("GOVERNED_SNAPSHOT_FALLBACK", "PASS"),
    ]
    assert result["acquisition_attempts"][0]["code"] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert _count(conn, schema) == 1, "repair must not fabricate evidence"
    # The caller's own request is never rewritten.
    assert req.event_id == " 2026020276 "
    assert req.stat_type == "pass-yards"


@pytest.mark.parametrize(
    "overrides",
    [
        {"captured_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()},
        {"hydration_status": "INCOMPLETE"},
        {"event_id": "2026020277"},
        {"player": "Other Player"},
        {"stat_type": "RUSHING_YARDS"},
        {"line": 285.5},
    ],
)
def test_repair_fallback_keeps_pregame_hydration_and_identity_filters(repair_db, overrides):
    conn, schema = repair_db
    _insert(conn, schema, **overrides)
    req = _request()

    result = repair_prop_evidence(
        req,
        primary_fetch=_primary_fetch(conn, schema),
        client=_Client(conn, schema),
    )

    assert result["code"] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert result["acquisition_repair_status"] == "FALLBACKS_EXHAUSTED"
    assert result["effective_source_snapshot_id"] == req.source_snapshot_id
    assert result["can_execute"] is False
    assert _count(conn, schema) == 1


def test_repair_never_accepts_a_snapshot_the_real_rpc_deems_incomplete(repair_db):
    conn, schema = repair_db
    persisted = _insert(conn, schema, game_log=[270.0, 271.0, 272.0])
    req = _request()

    result = repair_prop_evidence(
        req,
        primary_fetch=_primary_fetch(conn, schema),
        client=_Client(conn, schema),
    )

    assert result["code"] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert result["acquisition_repair_status"] == "FALLBACKS_EXHAUSTED"
    assert result["effective_source_snapshot_id"] == req.source_snapshot_id
    assert result["acquisition_attempts"][-1] == {
        "path": "GOVERNED_SNAPSHOT_FALLBACK",
        "source_snapshot_id": persisted["source_snapshot_id"],
        "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
        "status": "FAILED",
    }
    assert "game_log" not in result
    assert "probability" not in result
    assert result["can_execute"] is False
