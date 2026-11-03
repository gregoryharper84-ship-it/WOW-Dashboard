"""Fail-closed shadow NBA event persistence (never a publication permit)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from v17.nba_event_shadow_prediction_persistence import (
    NBAShadowReceiptHold, persist_nba_shadow_prediction,
)

NOW = datetime(2026, 11, 3, 18, tzinfo=timezone.utc)
EVENT = (NOW + timedelta(hours=3)).isoformat()
MODEL = "b" * 64
CAL = "c" * 64


def scored(**extra):
    s = dict(
        status="SCORED", code="NBA_PREGAME_SCORED", sport="NBA",
        official_event_id="espn-401809999",
        source_snapshot_id="sha256:" + "a" * 64,
        source_snapshot_timestamp=(NOW - timedelta(minutes=20)).isoformat(),
        feature_timestamp=(NOW - timedelta(minutes=10)).isoformat(),
        certification_id="cert-1", model_artifact_id="model-1",
        model_artifact_version="NBA_FITTED_V1", model_artifact_sha256=MODEL,
        calibration_artifact_id="cal-1", calibration_artifact_sha256=CAL,
        raw_probability=0.57, calibrated_probability=0.55,
        calibrated_lower_bound=0.50, calibrated_upper_bound=0.60,
        probability_publishable=False, rank_eligible=False, can_execute=False,
    )
    s.update(extra)
    return s


class Db:
    def __init__(self):
        self.rows = []
        self.filters = {}
        self.new = None
        self.insert_count = 0
        self.fail_insert = False

    def table(self, name):
        assert name == "wow_nba_event_shadow_predictions"
        self.filters = {}
        return self

    def select(self, *_):
        return self

    def eq(self, field, value):
        self.filters[field] = value
        return self

    def limit(self, *_):
        return self

    def insert(self, candidate):
        self.new = candidate
        return self

    def execute(self):
        if self.new is not None:
            candidate = self.new
            self.new = None
            if self.fail_insert:
                raise RuntimeError("transport")
            self.insert_count += 1
            self.rows.append({**candidate, "nba_prediction_id": "generated-uuid"})
            return type("R", (), {"data": []})()
        matches = [
            row for row in self.rows
            if all(row.get(k) == v for k, v in self.filters.items())
        ]
        return type("R", (), {"data": matches})()


def save(db, s=None, event=EVENT):
    return persist_nba_shadow_prediction(
        db, scored=s if s is not None else scored(),
        home_team="Milwaukee Bucks", away_team="Chicago Bulls",
        event_start_time_utc=event, now=NOW,
    )


def test_prospective_insert_and_exact_duplicate_readback():
    db = Db()
    first = save(db)
    second = save(db)
    assert first["status"] == "PERSISTED_RESEARCH_ONLY"
    assert second["status"] == "ALREADY_PERSISTED_RESEARCH_ONLY"
    assert first["nba_prediction_id"] == second["nba_prediction_id"]
    assert db.insert_count == 1
    for key in ("probability_publishable", "rank_eligible", "can_execute"):
        assert first[key] is False
        assert db.rows[0][key] is False
    assert db.rows[0]["terminal_label"] == "MODEL_RESEARCH_ONLY_HOLD"


@pytest.mark.parametrize("changes", [
    {"status": "HOLD"}, {"code": "NBA_RESEARCH_ONLY"},
    {"probability_publishable": True}, {"rank_eligible": True},
    {"can_execute": True}, {"sport": "MLB"},
    {"official_event_id": "not-an-ESPN-event"},
    {"source_snapshot_id": "client-supplied"},
    {"source_snapshot_timestamp": (NOW + timedelta(minutes=1)).isoformat()},
    {"feature_timestamp": (NOW + timedelta(minutes=1)).isoformat()},
    {"model_artifact_sha256": "unverified"},
    {"certification_id": ""},
    {"raw_probability": float("nan")},
    {"calibrated_lower_bound": 0.70},
])
def test_negative_source_score_publication_bounds_or_identity_denied(changes):
    db = Db()
    with pytest.raises(NBAShadowReceiptHold):
        save(db, scored(**changes))
    assert db.insert_count == 0


def test_past_event_cannot_be_retroactively_written():
    db = Db()
    with pytest.raises(NBAShadowReceiptHold) as caught:
        save(db, event=NOW.isoformat())
    assert caught.value.code == "NBA_SHADOW_NOT_PROSPECTIVE"
    assert not db.rows


def test_competing_changed_probability_is_immutable_conflict():
    db = Db()
    save(db)
    with pytest.raises(NBAShadowReceiptHold) as caught:
        save(db, scored(calibrated_probability=0.56))
    assert caught.value.code == "NBA_SHADOW_IMMUTABLE_READBACK_CONFLICT"
    assert db.insert_count == 1


def test_lost_insert_is_not_reported_as_success():
    db = Db()
    db.fail_insert = True
    with pytest.raises(NBAShadowReceiptHold) as caught:
        save(db)
    assert caught.value.code == "NBA_SHADOW_WRITE_NOT_PERSISTED"


def test_ambiguous_duplicate_db_rows_fail_closed():
    db = Db()
    save(db)
    db.rows.append(dict(db.rows[0]))
    with pytest.raises(NBAShadowReceiptHold) as caught:
        save(db)
    assert caught.value.code == "NBA_SHADOW_READBACK_INVALID"


def test_sql_contract_restricts_roles_and_fixes_shadow_state():
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations/20261103_nba_event_shadow_prediction_receipts.sql"
    ).read_text().lower()
    assert "alter table public.wow_nba_event_shadow_predictions enable row level security" in sql
    assert "revoke all" in sql and "from public, anon, authenticated" in sql
    assert "grant select, insert" in sql and "to service_role" in sql
    assert "probability_publishable = false" in sql
    assert "rank_eligible = false" in sql and "can_execute = false" in sql
    assert "created_at < event_start_time_utc" in sql
    assert "unique (" in sql
    # Do not alter the MLB-only event table or player-prop prediction ledger.
    tail = sql.split("create table if not exists")[1]
    assert "alter table public.wow_event_predictions" not in tail
    assert "alter table public.wow_predictions" not in tail
