"""Governed MLB prelock ingest: write path, refusals and idempotency.

The doubles below are PostgREST-shaped readers/writers, not a database. These
tests prove the writer derives its own hashes, refuses a closed prelock window,
supersedes rather than rewrites, replays idempotently and writes no probability
or verification flag.
"""
from __future__ import annotations

from types import SimpleNamespace

from v17 import mlb_pregame_evidence_ingest as ingest

EVENT_ID = "MLB:TEST-1"
EVENT_START = "2026-11-12T19:00:00+00:00"
SOURCE_AT = "2026-11-12T16:00:00+00:00"
FEATURE_AT = "2026-11-12T18:00:00+00:00"
NOW = "2026-11-12T18:15:00+00:00"

ARTIFACT_SHA = "a" * 64
VERSION = "MLB_EVENT_V1"
FAMILY = "MLB_EVENT_WIN_PROBABILITY"
SCHEMA = "MLB_EVENT_FEATURES_V1"

SOURCE_PAYLOAD = {
    "provider": "MLB_STATS_API",
    "game_pk": 745123,
    "home_team": "Home",
    "away_team": "Away",
    "probable_pitchers": {"home": "H", "away": "A"},
    "venue": "Park",
    "weather": {"temp_f": 64, "wind_mph": 8},
}
FEATURES = {
    "home_win_rate_l10": 0.6,
    "away_win_rate_l10": 0.4,
    "home_starter_era": 3.21,
    "away_starter_era": 4.02,
    "park_factor": 0.98,
}


class _Table:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters: list[tuple[str, str]] = []
        self.limit_value: int | None = None
        self.update_payload = None
        self.insert_payload = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value).strip().lower()))
        return self

    def limit(self, count):
        self.limit_value = count
        return self

    def update(self, payload):
        self.update_payload = dict(payload)
        return self

    def insert(self, payload):
        self.insert_payload = dict(payload) if isinstance(payload, dict) else list(payload)
        return self

    def execute(self):
        rows = [
            row
            for row in self.db.tables.get(self.name, [])
            if all(
                str(row.get(column)).strip().lower() == wanted
                for column, wanted in self.filters
            )
        ]
        if self.update_payload is not None:
            self.db.calls.append(("update", self.name, dict(self.update_payload)))
            for row in rows:
                row.update(self.update_payload)
            return SimpleNamespace(data=rows)
        if self.insert_payload is not None:
            self.db.calls.append(("insert", self.name, dict(self.insert_payload)))
            self.db.tables.setdefault(self.name, []).append(dict(self.insert_payload))
            return SimpleNamespace(data=[dict(self.insert_payload)])
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return SimpleNamespace(data=rows)


class _Db:
    def __init__(self, rows=None):
        self.tables = {ingest.TABLE: [dict(row) for row in (rows or [])]}
        self.calls: list[tuple] = []

    def table(self, name):
        return _Table(self, name)


class _FailingDb(_Db):
    def table(self, name):
        class _Explode:
            def select(self, *_a, **_k):
                raise ConnectionError("store unavailable")

        return _Explode()


def _ingest(db, **overrides):
    kwargs = {
        "event": {
            "official_event_id": EVENT_ID,
            "event_start_time": EVENT_START,
            "event_status": "SCHEDULED",
        },
        "source_payload": SOURCE_PAYLOAD,
        "features": FEATURES,
        "source_snapshot_id": "src-snap-1",
        "source_snapshot_timestamp": SOURCE_AT,
        "feature_snapshot_id": "feat-snap-1",
        "feature_snapshot_timestamp": FEATURE_AT,
        "model_family": FAMILY,
        "model_artifact_version": VERSION,
        "model_artifact_checksum": ARTIFACT_SHA,
        "feature_schema_version": SCHEMA,
        "now": NOW,
    }
    kwargs.update(overrides)
    return ingest.ingest_pregame_snapshot(db, **kwargs)


def _prior_row(**overrides):
    row = {
        "id": 7,
        "is_current": True,
        "official_event_id": EVENT_ID,
        "model_family": FAMILY,
        "model_artifact_version": VERSION,
        "feature_schema_version": SCHEMA,
        "source_snapshot_id": "src-snap-old",
        "feature_snapshot_id": "feat-snap-old",
        "source_payload_sha256": "1" * 64,
        "feature_snapshot_sha256": "2" * 64,
    }
    row.update(overrides)
    return row


def test_ingest_writes_one_governed_prelock_row():
    db = _Db()

    receipt = _ingest(db)

    assert receipt["status"] == ingest.PERSISTED
    assert receipt["blockers"] == []
    assert receipt["can_execute"] is False
    assert receipt["probability_written"] is False
    assert receipt["verification_flag_written"] is False
    rows = db.tables[ingest.TABLE]
    assert len(rows) == 1
    row = rows[0]
    assert row["official_event_id"] == EVENT_ID
    assert row["event_status"] == "SCHEDULED"
    assert row["is_current"] is True
    assert row["model_artifact_checksum"] == ARTIFACT_SHA
    # Hashes describe the payloads that were actually written.
    assert row["source_payload_sha256"] == ingest.canonical_sha256(SOURCE_PAYLOAD)
    assert row["feature_snapshot_sha256"] == ingest.canonical_sha256(FEATURES)
    assert set(row) <= set(ingest._ROW_FIELDS)


def test_the_row_never_carries_probability_or_verification_columns():
    db = _Db()
    _ingest(db)

    row = db.tables[ingest.TABLE][0]

    forbidden = ("probability", "publishable", "rank", "verified", "certified")
    assert not [key for key in row if any(token in key for token in forbidden)]


def test_ingest_supersedes_rather_than_rewriting_the_previous_row():
    db = _Db([_prior_row()])

    receipt = _ingest(db)

    assert receipt["status"] == ingest.PERSISTED
    assert receipt["superseded_row_id"] == 7
    prior = db.tables[ingest.TABLE][0]
    assert prior["is_current"] is False
    # Payload columns of the superseded row are untouched.
    assert prior["source_snapshot_id"] == "src-snap-old"
    assert prior["feature_snapshot_sha256"] == "2" * 64
    assert prior["source_payload_sha256"] == "1" * 64
    assert [call[0] for call in db.calls] == ["update", "insert"]
    assert len(db.tables[ingest.TABLE]) == 2


def test_replaying_the_same_snapshot_writes_nothing_new():
    db = _Db()
    first = _ingest(db)
    db.calls.clear()

    second = _ingest(db)

    assert first["status"] == ingest.PERSISTED
    assert second["status"] == ingest.ALREADY_CURRENT
    assert db.calls == []
    assert len(db.tables[ingest.TABLE]) == 1


def test_ingest_refuses_a_snapshot_after_first_pitch():
    db = _Db()

    receipt = _ingest(db, now="2026-11-12T19:00:00+00:00")

    assert receipt["status"] == ingest.REFUSED
    assert "MLB_PREGAME_EVIDENCE_WINDOW_CLOSED" in receipt["blockers"]
    assert db.calls == []
    assert db.tables[ingest.TABLE] == []


def test_ingest_refuses_features_frozen_at_or_after_the_official_start():
    db = _Db()

    receipt = _ingest(db, feature_snapshot_timestamp=EVENT_START)

    assert receipt["status"] == ingest.REFUSED
    assert "MLB_PREGAME_EVIDENCE_PRELOCK_ORDER_INVALID" in receipt["blockers"]
    assert db.calls == []


def test_ingest_refuses_a_source_captured_after_the_feature_freeze():
    db = _Db()

    receipt = _ingest(db, source_snapshot_timestamp="2026-11-12T18:30:00+00:00")

    assert receipt["status"] == ingest.REFUSED
    assert "MLB_PREGAME_EVIDENCE_PRELOCK_ORDER_INVALID" in receipt["blockers"]
    assert db.calls == []


def test_ingest_refuses_a_non_scheduled_event():
    db = _Db()

    receipt = _ingest(
        db,
        event={
            "official_event_id": EVENT_ID,
            "event_start_time": EVENT_START,
            "event_status": "POSTPONED",
        },
    )

    assert receipt["status"] == ingest.REFUSED
    assert "MLB_PREGAME_EVIDENCE_EVENT_NOT_SCHEDULED:POSTPONED" in receipt["blockers"]
    assert db.calls == []


def test_ingest_refuses_unbindable_identity_and_a_malformed_artifact_checksum():
    db = _Db()

    receipt = _ingest(
        db, model_artifact_version="", model_artifact_checksum="not-a-checksum"
    )

    assert receipt["status"] == ingest.REFUSED
    assert "MLB_PREGAME_EVIDENCE_IDENTITY_INCOMPLETE:model_artifact_version" in receipt["blockers"]
    assert "MLB_PREGAME_EVIDENCE_ARTIFACT_CHECKSUM_INVALID" in receipt["blockers"]
    assert db.calls == []


def test_ingest_refuses_an_empty_or_non_object_payload():
    db = _Db()

    receipt = _ingest(db, features={})

    assert receipt["status"] == ingest.REFUSED
    assert "MLB_PREGAME_EVIDENCE_PAYLOAD_NOT_OBJECT:features" in receipt["blockers"]
    assert db.calls == []


def test_hashes_are_derived_not_supplied():
    db_one = _Db()
    db_two = _Db()
    _ingest(db_one)
    changed = dict(FEATURES)
    changed["home_win_rate_l10"] = 0.61
    _ingest(db_two, features=changed)

    first = db_one.tables[ingest.TABLE][0]["feature_snapshot_sha256"]
    second = db_two.tables[ingest.TABLE][0]["feature_snapshot_sha256"]

    assert first == ingest.canonical_sha256(FEATURES)
    assert first != second
    # Key order cannot change the digest.
    reordered = dict(reversed(list(FEATURES.items())))
    assert ingest.canonical_sha256(reordered) == first


def test_a_failed_supersede_does_not_insert_partial_evidence():
    class _SupersedeFails(_Db):
        def table(self, name):
            table = super().table(name)

            def exploding_update(payload):
                table.update_payload = payload

                def execute():
                    raise RuntimeError("supersede rejected")

                table.execute = execute
                return table

            table.update = exploding_update
            return table

    db = _SupersedeFails([_prior_row()])

    receipt = _ingest(db)

    assert receipt["status"] == ingest.WRITE_FAILED
    assert any(
        "MLB_PREGAME_EVIDENCE_SUPERSEDE_FAILED" in blocker
        for blocker in receipt["blockers"]
    )
    assert len(db.tables[ingest.TABLE]) == 1
    assert db.tables[ingest.TABLE][0]["is_current"] is True


def test_an_unreadable_store_is_a_typed_refusal_not_an_insert():
    receipt = _ingest(_FailingDb())

    assert receipt["status"] == ingest.READ_FAILED
    assert any(
        "MLB_PREGAME_EVIDENCE_READ_FAILED" in blocker
        for blocker in receipt["blockers"]
    )
