"""Slate adapter: one hydrated MLB slate writes its governed prelock evidence.

The database doubles below exercise the real read/write path. They fabricate no
governed row, certify nothing and promote nothing; the served artifact row is a
read-only registry stand-in and the forward-shadow rows are the same shape the
existing hydration loop writes.
"""
from __future__ import annotations

from types import SimpleNamespace

from v17 import mlb_pregame_evidence_ingest as ingest
from v17 import mlb_pregame_evidence_slate as slate

SLATE = "2026-11-12"
START = "2026-11-12T19:00:00+00:00"
SOURCE_AT = "2026-11-12T17:00:00+00:00"
FEATURE_AT = "2026-11-12T18:00:00+00:00"
NOW = "2026-11-12T18:30:00+00:00"

ARTIFACT_SHA = "a" * 64
PREGAME_TABLE = "wow_mlb_event_pregame_feature_rows"


class _Table:
    """Minimal PostgREST-shaped double: select/eq/order/limit/insert/update."""

    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._rows = db.tables.setdefault(name, [])
        self._matched = list(self._rows)
        self._mode = "select"
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def eq(self, column, value):
        wanted = str(value).strip().lower()
        self._matched = [
            row
            for row in self._matched
            if str(row.get(column)).strip().lower() == wanted
        ]
        return self

    def order(self, column, desc=False):
        self._matched.sort(
            key=lambda row: str(row.get(column) or ""), reverse=bool(desc)
        )
        return self

    def limit(self, count):
        self._matched = self._matched[:count]
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = dict(payload)
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = dict(payload)
        return self

    def execute(self):
        if self._mode == "insert":
            row = {**self._payload, "id": len(self._rows) + 1}
            self._rows.append(row)
            self.db.writes.append((self.name, "insert"))
            return SimpleNamespace(data=[dict(row)])
        if self._mode == "update":
            for row in self._matched:
                row.update(self._payload)
            self.db.writes.append((self.name, "update"))
            return SimpleNamespace(data=[dict(row) for row in self._matched])
        return SimpleNamespace(data=[dict(row) for row in self._matched])


class _Db:
    def __init__(self, *, artifacts=(), events=(), features=()):
        self.tables = {
            slate.ARTIFACT_TABLE: [dict(row) for row in artifacts],
            slate.EVENT_TABLE: [dict(row) for row in events],
            slate.FEATURE_TABLE: [dict(row) for row in features],
            PREGAME_TABLE: [],
        }
        self.writes = []

    def table(self, name):
        return _Table(self, name)


def _artifact(**overrides):
    row = {
        "provider_identity": slate.PROVIDER_IDENTITY,
        "sport": "MLB",
        "model_family": "MLB_EVENT_WIN_PROBABILITY",
        "model_artifact_version": "MLB_EVENT_V1",
        "feature_schema_version": "MLB_EVENT_FEATURES_V1",
        "artifact_checksum": ARTIFACT_SHA,
        "lifecycle_state": "CHAMPION",
        "active": True,
        "promoted": True,
    }
    row.update(overrides)
    return row


def _event(**overrides):
    row = {
        "shadow_event_id": "shadow-1",
        "official_event_id": "MLB:1",
        "official_date": SLATE,
        "event_start_time": START,
        "event_status": "Scheduled",
        "home_team": "Yankees",
        "away_team": "Red Sox",
        "home_team_id": 147,
        "away_team_id": 111,
        "venue_name": "Yankee Stadium",
        "home_probable_pitcher": "Home Starter",
        "away_probable_pitcher": "Away Starter",
        "home_probable_pitcher_id": 1,
        "away_probable_pitcher_id": 2,
        "snapshot_id": "src-snap-1",
        "snapshot_timestamp": SOURCE_AT,
        "source_game_json": {"gamePk": 777},
        "feature_hydration_status": "PASS",
    }
    row.update(overrides)
    return row


def _feature(side, **overrides):
    row = {
        "feature_snapshot_id": f"feat-{side.lower()}-1",
        "shadow_event_id": "shadow-1",
        "side": side,
        "scoring_team_id": 147 if side == "HOME" else 111,
        "probable_starter_id": 1 if side == "HOME" else 2,
        "feature_names": ["is_home", "off_runs_pg"],
        "feature_vector": [1.0, 4.5] if side == "HOME" else [0.0, 4.2],
        "source_snapshot_ids": ["src-snap-1"],
        "hydration_status": "PASS",
        "created_at": FEATURE_AT,
    }
    row.update(overrides)
    return row


def _run(db):
    return slate.run_slate_pregame_evidence(db, slate_date=SLATE, now=NOW)


def test_one_slate_run_writes_one_governed_prelock_row():
    db = _Db(
        artifacts=[_artifact()],
        events=[_event()],
        features=[_feature("HOME"), _feature("AWAY")],
    )

    receipt = _run(db)

    assert receipt["status"] == slate.SLATE_COMPLETE
    assert receipt["written"] == 1
    assert receipt["pregame_evidence_written"] is True
    rows = db.tables[PREGAME_TABLE]
    assert len(rows) == 1
    row = rows[0]
    assert row["official_event_id"] == "MLB:1"
    assert row["event_status"] == "SCHEDULED"
    assert row["model_artifact_version"] == "MLB_EVENT_V1"
    assert row["feature_schema_version"] == "MLB_EVENT_FEATURES_V1"
    assert row["model_artifact_checksum"] == ARTIFACT_SHA
    assert row["source_snapshot_id"] == "src-snap-1"
    assert row["source_snapshot_timestamp"] == SOURCE_AT
    assert row["feature_snapshot_id"] == "feat-home-1:feat-away-1"
    assert row["feature_snapshot_timestamp"] == FEATURE_AT
    assert row["is_current"] is True
    # Evidence only: the writer never grows a probability/verification column.
    assert set(row) - {"id"} <= ingest._ROW_FIELDS
    assert len(row["source_payload_sha256"]) == 64
    assert len(row["feature_snapshot_sha256"]) == 64
    assert receipt["probability_written"] is False
    assert receipt["verification_flag_written"] is False
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == slate.TERMINAL_AUTHORITY


def test_the_feature_digest_is_derived_from_the_frozen_vector():
    first = _Db(
        artifacts=[_artifact()],
        events=[_event()],
        features=[_feature("HOME"), _feature("AWAY")],
    )
    changed = _Db(
        artifacts=[_artifact()],
        events=[_event()],
        features=[_feature("HOME"), _feature("AWAY", feature_vector=[0.0, 4.3])],
    )

    assert _run(first)["written"] == 1
    assert _run(changed)["written"] == 1
    original = first.tables[PREGAME_TABLE][0]["feature_snapshot_sha256"]
    edited = changed.tables[PREGAME_TABLE][0]["feature_snapshot_sha256"]
    assert original != edited
    # Only the payload digest moves; identity and ordering are unchanged.
    assert (
        first.tables[PREGAME_TABLE][0]["feature_snapshot_id"]
        == changed.tables[PREGAME_TABLE][0]["feature_snapshot_id"]
    )


def test_a_slate_without_a_served_artifact_writes_nothing():
    db = _Db(artifacts=[], events=[_event()], features=[_feature("HOME"), _feature("AWAY")])

    receipt = _run(db)

    assert receipt["status"] == slate.SLATE_BLOCKED
    assert "MLB_PREGAME_EVIDENCE_ARTIFACT_UNAVAILABLE" in receipt["blockers"]
    assert db.tables[PREGAME_TABLE] == []
    assert db.writes == []


def test_two_served_artifacts_are_ambiguous_and_write_nothing():
    db = _Db(
        artifacts=[
            _artifact(),
            _artifact(model_artifact_version="MLB_EVENT_V2"),
        ],
        events=[_event()],
        features=[_feature("HOME"), _feature("AWAY")],
    )

    receipt = _run(db)

    assert receipt["status"] == slate.SLATE_BLOCKED
    assert "MLB_PREGAME_EVIDENCE_ARTIFACT_AMBIGUOUS" in receipt["blockers"]
    assert db.tables[PREGAME_TABLE] == []


def test_an_uncertified_artifact_is_not_a_calibration_source():
    db = _Db(
        artifacts=[_artifact(lifecycle_state="CANDIDATE")],
        events=[_event()],
        features=[_feature("HOME"), _feature("AWAY")],
    )

    receipt = _run(db)

    # A candidate lifecycle is not a served artifact: no row is written and the
    # slate reports the same typed "unavailable" reason as an empty registry.
    assert receipt["status"] == slate.SLATE_BLOCKED
    assert "MLB_PREGAME_EVIDENCE_ARTIFACT_UNAVAILABLE" in receipt["blockers"]
    assert db.tables[PREGAME_TABLE] == []


def test_the_documented_pregame_alias_is_canonicalized():
    db = _Db(
        artifacts=[_artifact()],
        events=[_event(event_status="Pre-Game")],
        features=[_feature("HOME"), _feature("AWAY")],
    )

    assert _run(db)["written"] == 1
    assert db.tables[PREGAME_TABLE][0]["event_status"] == "SCHEDULED"


def test_warmup_is_refused_rather_than_relabeled_scheduled():
    db = _Db(
        artifacts=[_artifact()],
        events=[_event(event_status="Warmup")],
        features=[_feature("HOME"), _feature("AWAY")],
    )

    receipt = _run(db)

    assert receipt["status"] == slate.SLATE_BLOCKED
    assert receipt["refused"] == 1
    assert db.tables[PREGAME_TABLE] == []
    assert (
        "MLB_PREGAME_EVIDENCE_EVENT_NOT_SCHEDULED:WARMUP"
        in receipt["events"][0]["blockers"]
    )


def test_a_missing_away_feature_snapshot_writes_nothing():
    db = _Db(artifacts=[_artifact()], events=[_event()], features=[_feature("HOME")])

    receipt = _run(db)

    assert receipt["status"] == slate.SLATE_BLOCKED
    assert (
        "MLB_PREGAME_EVIDENCE_FEATURE_PAIR_UNAVAILABLE"
        in receipt["events"][0]["blockers"]
    )
    assert db.tables[PREGAME_TABLE] == []


def test_features_frozen_at_first_pitch_are_refused():
    db = _Db(
        artifacts=[_artifact()],
        events=[_event()],
        features=[_feature("HOME", created_at=START), _feature("AWAY")],
    )

    receipt = _run(db)

    assert receipt["refused"] == 1
    assert db.tables[PREGAME_TABLE] == []
    assert (
        "MLB_PREGAME_EVIDENCE_PRELOCK_ORDER_INVALID"
        in receipt["events"][0]["blockers"]
    )


def test_replaying_the_same_slate_is_idempotent():
    db = _Db(
        artifacts=[_artifact()],
        events=[_event()],
        features=[_feature("HOME"), _feature("AWAY")],
    )

    first = _run(db)
    second = _run(db)

    assert first["written"] == 1
    assert second["already_current"] == 1
    assert second["written"] == 0
    assert second["status"] == slate.SLATE_COMPLETE
    assert len(db.tables[PREGAME_TABLE]) == 1


def test_only_hydrated_events_are_read():
    db = _Db(
        artifacts=[_artifact()],
        events=[_event(), _event(shadow_event_id="shadow-2", official_event_id="MLB:2",
                               feature_hydration_status="DELAYED_STARTER_UNRESOLVED")],
        features=[_feature("HOME"), _feature("AWAY")],
    )

    receipt = _run(db)

    assert receipt["written"] == 1
    assert [row["official_event_id"] for row in db.tables[PREGAME_TABLE]] == ["MLB:1"]
