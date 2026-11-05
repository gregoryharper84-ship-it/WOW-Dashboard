"""PostgreSQL-backed proof of the #2473 / canonical-ledger schema mismatch.

This is a read-only reproduction of a defect, not a desired contract. It starts
a disposable cluster, applies the repository's own `event_schema.sql` table DDL
for `public.wow_event_predictions`, and drives the real
`_verify_governed_event_prediction` through a psycopg adapter implementing the
governed client's `.table().select().eq().limit().execute()` surface.

If a governed multisport receipt ledger (or an official adapter) is introduced,
these characterisation tests must be replaced with the new contract's own
positive and negative controls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
pgserver = pytest.importorskip("pgserver")
from psycopg.rows import dict_row  # noqa: E402

from v17.llp_team_intelligence_readback import (  # noqa: E402
    _SUPPORTED,
    _verify_governed_event_prediction,
)

REPO = Path(__file__).resolve().parents[3]
SCHEMA = REPO / "artifacts" / "wow-engine" / "event_schema.sql"
TABLE = "wow_event_predictions"

START = datetime(2026, 9, 15, 23, 5, tzinfo=timezone.utc)
SNAPSHOT_AT = START - timedelta(hours=6)
MODEL_AT = START - timedelta(hours=5)
CREATED_AT = START - timedelta(hours=5)
LOCKED_AT = START - timedelta(hours=4)
REDUCED_AT = START - timedelta(hours=3)
HOME, AWAY = "New York Yankees", "Boston Red Sox"
SNAPSHOT_ID = "3f1c6f3e-9a2b-4c6d-8e10-2b7c4d5e6f70"
OFFICIAL_EVENT_ID = "mlb-778812"

# The LLP verifier selects these two columns; the base event_schema.sql does
# not declare them and they arrive from later migrations in the deployed
# schema. They are added here so the verifier's own select can run unchanged.
ADDITIVE_COLUMNS = (
    "alter table public.wow_event_predictions "
    "add column if not exists source_snapshot_timestamp timestamptz",
    "alter table public.wow_event_predictions "
    "add column if not exists terminal_reduced_at timestamptz",
)


def _canonical_ddl() -> str:
    text = SCHEMA.read_text(encoding="utf-8")
    start = text.index("create table if not exists wow_event_predictions")
    end = text.index("\n);", start) + len("\n);")
    return text[start:end]


def _mlb_receipt(**overrides) -> dict:
    """A genuinely well-formed canonical MLB receipt, in the real DDL's shape."""
    row = {
        "event_prediction_id": str(uuid4()),
        "created_at": CREATED_AT,
        "research_run_id": "run-20260915-mlb",
        "event_key": f"MLB:{OFFICIAL_EVENT_ID}",
        "official_event_id": OFFICIAL_EVENT_ID,
        "requested_slate_date": START.date(),
        "requested_timezone": "UTC",
        "scan_stage": "PREGAME",
        "event_start_time": START,
        "sport": "MLB",
        "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": HOME,
        "away_team": AWAY,
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_timestamp": SNAPSHOT_AT,
        "model_timestamp": MODEL_AT,
        "model_valid_after_latest_update": True,
        "model_version": "MLB_EVENT_V1",
        "calibration_version": "MLB_CAL_V1",
        "calibrated_home_probability": Decimal("0.61"),
        "calibrated_home_lower_bound": Decimal("0.55"),
        "calibrated_home_upper_bound": Decimal("0.65"),
        "calibrated_away_probability": Decimal("0.39"),
        "calibrated_away_lower_bound": Decimal("0.35"),
        "calibrated_away_upper_bound": Decimal("0.45"),
        "selected_participant": HOME,
        "probability_audit_result": "PASS_PROBABILITY_AUDIT",
        "rank_eligible": True,
        "probability_publishable": True,
        "locked_at": LOCKED_AT,
        "terminal_reduced_at": REDUCED_AT,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def _event(*, sport: str = "MLB", official_event_id: str = OFFICIAL_EVENT_ID) -> dict:
    return {
        "official_event_id": official_event_id,
        "sport": sport,
        "home_team": HOME,
        "away_team": AWAY,
        "event_start_time_utc": START,
    }


def _winner(receipt_id: str, *, side: str = HOME) -> dict:
    return {
        "source_receipt_id": receipt_id,
        "model_timestamp": MODEL_AT,
        "source_snapshot_timestamp": SNAPSHOT_AT,
        "side": side,
        "calibrated_probability": Decimal("0.61"),
        "calibrated_probability_lower_bound": Decimal("0.55"),
        "calibrated_probability_upper_bound": Decimal("0.65"),
        "model_version": "MLB_EVENT_V1",
        "calibration_version": "MLB_CAL_V1",
        "source_snapshot_id": SNAPSHOT_ID,
    }


class _Query:
    """Minimal stand-in for the governed client's query builder."""

    def __init__(self, uri: str, table_name: str) -> None:
        self.uri = uri
        self.table_name = table_name
        self._columns = "*"
        self._where: tuple[str, object] | None = None
        self._limit: int | None = None

    def select(self, columns: str) -> "_Query":
        self._columns = columns
        return self

    def eq(self, column: str, value: object) -> "_Query":
        self._where = (column, value)
        return self

    def limit(self, count: int) -> "_Query":
        self._limit = int(count)
        return self

    def execute(self) -> SimpleNamespace:
        sql = f"select {self._columns} from public.{self.table_name}"
        params: list[object] = []
        if self._where is not None:
            sql += f" where {self._where[0]} = %s"
            params.append(self._where[1])
        if self._limit is not None:
            sql += f" limit {self._limit}"
        with psycopg.connect(self.uri, row_factory=dict_row) as conn:
            rows = conn.execute(sql, params).fetchall()
        return SimpleNamespace(data=[dict(row) for row in rows])


class Ledger:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        with self._connect() as conn:
            conn.execute(_canonical_ddl())
            for statement in ADDITIVE_COLUMNS:
                conn.execute(statement)
        self.reset()

    def _connect(self):
        return psycopg.connect(self.uri, autocommit=True)

    def reset(self) -> None:
        with self._connect() as conn:
            conn.execute(f"truncate table public.{TABLE}")

    def insert(self, row: dict) -> None:
        columns = ",".join(row)
        placeholders = ",".join(["%s"] * len(row))
        with self._connect() as conn:
            conn.execute(
                f"insert into public.{TABLE} ({columns}) values ({placeholders})",
                list(row.values()),
            )

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute(f"select count(*) from public.{TABLE}").fetchone()[0]

    def constraint_defs(self) -> dict[str, str]:
        with self._connect() as conn:
            return dict(
                conn.execute(
                    "select conname, pg_get_constraintdef(oid) from pg_constraint"
                    " where conrelid = 'public.wow_event_predictions'::regclass"
                ).fetchall()
            )

    def table(self, table_name: str) -> _Query:
        assert table_name == TABLE, table_name
        return _Query(self.uri, table_name)


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        yield server.get_uri()
    finally:
        cleanup = getattr(server, "cleanup", None)
        if callable(cleanup):
            cleanup()


@pytest.fixture()
def ledger(cluster) -> Ledger:
    handle = Ledger(cluster)
    yield handle
    handle.reset()


def test_real_schema_keeps_mlb_only_guards(ledger):
    defs = ledger.constraint_defs()
    definitions = " ".join(defs.values())
    assert "sport = 'MLB'" in definitions, defs
    assert "wow.mlb-game-win-probability-expert" in definitions, defs
    assert "FULL_GAME_INCLUDING_EXTRA_INNINGS" in definitions, defs
    # The verifier's chronology columns are migration additions, not base DDL.
    ddl = _canonical_ddl()
    assert "terminal_reduced_at" not in ddl
    assert "source_snapshot_timestamp" not in ddl


def test_llp_supported_sports_cannot_be_persisted_in_the_canonical_ledger(ledger):
    assert _SUPPORTED == frozenset({"NFL", "NBA", "NHL"})
    assert _SUPPORTED.isdisjoint({"MLB"})
    for sport in sorted(_SUPPORTED):
        with pytest.raises(psycopg.errors.CheckViolation) as caught:
            ledger.insert(_mlb_receipt(sport=sport))
        assert caught.value.sqlstate == "23514"
        assert "sport" in str(caught.value).lower()
    assert ledger.count() == 0


def test_nfl_settlement_contract_is_blocked_independently_of_the_sport_guard(ledger):
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        ledger.insert(_mlb_receipt(settlement_basis="FULL_GAME_INCLUDING_OVERTIME"))
    assert caught.value.sqlstate == "23514"
    assert "settlement_basis" in str(caught.value).lower()


def test_canonical_mlb_row_cannot_satisfy_the_verifier_basis_map(ledger):
    row = _mlb_receipt()
    ledger.insert(row)
    assert ledger.count() == 1
    hold = _verify_governed_event_prediction(
        ledger, event=_event(sport="MLB"), winner=_winner(row["event_prediction_id"])
    )
    # The verifier's settlement-basis map covers only NFL/NBA/NHL, so the one
    # sport this ledger can store is never a verifiable basis.
    assert hold == "LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"


def test_supported_sport_event_holds_against_the_canonical_ledger(ledger):
    hold = _verify_governed_event_prediction(
        ledger,
        event=_event(sport="NFL", official_event_id="nfl-401872931"),
        winner=_winner(str(uuid4())),
    )
    assert hold == "LLP_EVENT_PREDICTION_NOT_EXACTLY_ONE"
