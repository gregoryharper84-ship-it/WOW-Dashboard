"""Governed MLB calibrator evidence producer.

These tests fit the real ratified Phase B calibrator on synthetic settled rows
and assert the persisted evidence row carries its own proofs: a derived
calibrator checksum, cohort and holdout digests, zero holdout overlap and honest
holdout metrics. No governed row is certified, no verification flag is written
and no probability is published.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from v17 import mlb_calibrator_evidence_producer as producer

ARTIFACT_SHA = "a" * 64
LAST_SETTLEMENT = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
NOW = "2026-11-09T00:00:00+00:00"


class _Table:
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

    def limit(self, count):
        self._matched = self._matched[:count]
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = dict(payload)
        return self

    def execute(self):
        if self._mode == "insert":
            row = {**self._payload, "id": len(self._rows) + 1}
            self._rows.append(row)
            self.db.writes.append((self.name, "insert"))
            return SimpleNamespace(data=[dict(row)])
        return SimpleNamespace(data=[dict(row) for row in self._matched])


class _Db:
    def __init__(self):
        self.tables = {producer.TABLE: []}
        self.writes = []

    def table(self, name):
        return _Table(self, name)


def _artifact(**overrides):
    row = {
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


def _rows(*, days=30, per_day=40, seed=11):
    rng = random.Random(seed)
    rows = []
    for day in range(days):
        settled = LAST_SETTLEMENT - timedelta(days=days - 1 - day)
        for index in range(per_day):
            raw = rng.uniform(0.25, 0.75)
            rows.append(
                {
                    "event_id": f"MLB:{day}-{index}",
                    "raw_probability": raw,
                    "outcome": 1 if rng.random() < raw else 0,
                    "settled_at": settled.isoformat(),
                }
            )
    return rows


def _produce(db, rows, **overrides):
    kwargs = {
        "artifact": _artifact(),
        "settled_rows": rows,
        "calibrator_id": "cal-mlb-1",
        "now": NOW,
    }
    kwargs.update(overrides)
    return producer.produce_calibrator_evidence(db, **kwargs)


def test_producer_fits_and_persists_one_exact_version_row():
    db = _Db()

    receipt = _produce(db, _rows())

    assert receipt["status"] == producer.PERSISTED
    stored = db.tables[producer.TABLE]
    assert len(stored) == 1
    row = stored[0]
    assert row["calibrator_id"] == "cal-mlb-1"
    assert row["sport"] == "MLB"
    assert row["model_family"] == "MLB_EVENT_WIN_PROBABILITY"
    assert row["model_artifact_version"] == "MLB_EVENT_V1"
    assert row["feature_schema_version"] == "MLB_EVENT_FEATURES_V1"
    assert row["model_artifact_checksum"] == ARTIFACT_SHA
    assert row["calibration_method"] == "PLATT_TIME_SPLIT_V1"
    assert row["bounds_method_version"] == "PREDICTIVE_BOUNDS_V1"
    assert row["calibration_training_n"] == 880
    assert row["holdout_overlap_rows"] == 0
    assert 0.0 <= row["brier"] <= 1.0
    assert row["log_loss"] >= 0.0
    assert -1.0 <= row["calibration_bias"] <= 1.0
    for field in (
        "calibrator_checksum",
        "calibration_cohort_sha256",
        "holdout_evidence_sha256",
    ):
        assert len(row[field]) == 64
    assert row["calibrator_checksum"] != row["calibration_cohort_sha256"]
    assert row["calibrator_checksum"] != row["holdout_evidence_sha256"]
    # Independent-verification bindings belong to the CERTIFIED receipt only.
    assert "chronological_replay_verified" not in row
    assert "calibrated_lower_bound_verified" not in row
    assert receipt["probability_written"] is False
    assert receipt["verification_flag_written"] is False
    assert receipt["can_execute"] is False


def test_the_holdout_is_strictly_later_than_the_fit_cohort():
    normalized, blockers = producer.normalize_settled_rows(
        _rows(), decision_at=datetime(2026, 11, 9, tzinfo=timezone.utc)
    )
    assert blockers == ()
    fit, holdout = producer.partition_settled_rows(normalized)

    assert fit and holdout
    assert max(row["settled_at"] for row in fit) < min(
        row["settled_at"] for row in holdout
    )
    assert not {row["event_id"] for row in fit} & {row["event_id"] for row in holdout}


def test_replay_is_idempotent():
    db = _Db()

    assert _produce(db, _rows())["status"] == producer.PERSISTED
    second = _produce(db, _rows())

    assert second["status"] == producer.ALREADY_CURRENT
    assert len(db.tables[producer.TABLE]) == 1


def test_two_different_cohorts_produce_different_calibrator_checksums():
    first, second = _Db(), _Db()

    _produce(first, _rows(seed=11))
    _produce(second, _rows(seed=12))

    assert (
        first.tables[producer.TABLE][0]["calibrator_checksum"]
        != second.tables[producer.TABLE][0]["calibrator_checksum"]
    )


def test_insufficient_fit_cohort_is_refused():
    db = _Db()

    receipt = _produce(db, _rows(days=5, per_day=30))

    assert receipt["status"] == producer.REFUSED
    assert any("FIT_COHORT_INSUFFICIENT" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []
    assert db.writes == []


def test_a_fit_failure_is_typed_and_never_papered_over():
    db = _Db()

    receipt = _produce(db, _rows(days=5, per_day=50))

    assert receipt["status"] == producer.REFUSED
    assert any("FIT_FAILED" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


def test_duplicate_settled_identity_is_a_conflict():
    rows = _rows()
    db = _Db()

    receipt = _produce(db, [*rows, dict(rows[0])])

    assert receipt["status"] == producer.REFUSED
    assert any("DUPLICATE_SETTLED_ROW" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


def test_an_unsettled_observation_is_refused():
    rows = _rows()
    rows[-1] = {
        **rows[-1],
        "settled_at": "2026-11-10T00:00:00+00:00",
    }
    db = _Db()

    receipt = _produce(db, rows)

    assert receipt["status"] == producer.REFUSED
    assert any("NOT_SETTLED_AT_DECISION" in item for item in receipt["blockers"])


def test_invalid_probability_or_outcome_is_refused():
    rows = _rows()
    rows[0] = {**rows[0], "raw_probability": 0.0}
    rows[1] = {**rows[1], "outcome": 2}
    db = _Db()

    receipt = _produce(db, rows)

    assert receipt["status"] == producer.REFUSED
    joined = " ".join(receipt["blockers"])
    assert "RAW_PROBABILITY_INVALID" in joined
    assert "OUTCOME_INVALID" in joined


def test_an_uncertified_artifact_is_not_evidence_authority():
    db = _Db()

    receipt = _produce(db, _rows(), artifact=_artifact(lifecycle_state="CANDIDATE"))

    assert receipt["status"] == producer.REFUSED
    assert any("ARTIFACT_NOT_CERTIFIED" in item for item in receipt["blockers"])
    assert db.tables[producer.TABLE] == []


def test_a_missing_database_is_a_typed_refusal():
    receipt = producer.produce_calibrator_evidence(
        None,
        artifact=_artifact(),
        settled_rows=_rows(),
        calibrator_id="cal-mlb-1",
        now=NOW,
    )

    assert receipt["status"] == producer.REFUSED
    assert receipt["blockers"] == ["MLB_CALIBRATOR_EVIDENCE_DB_UNAVAILABLE"]
    assert receipt["can_execute"] is False
