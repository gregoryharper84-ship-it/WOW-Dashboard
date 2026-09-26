from __future__ import annotations

from v17.binary_candidate_lifecycle import BinaryTrainingRow
from v17 import ncaaf_result_form_candidate as result_form


class _Result:
    data = []


class _ImmutableTrainingRowsQuery:
    def __init__(self, ledger):
        self.ledger = ledger
        self.pending = None

    def upsert(self, rows, *, on_conflict, ignore_duplicates=False):
        assert on_conflict == "sport,official_event_id,feature_schema_version,source_manifest_sha256"
        assert ignore_duplicates is True
        self.pending = [dict(row) for row in rows]
        return self

    def execute(self):
        for row in self.pending or []:
            key = (
                row["sport"],
                row["official_event_id"],
                row["feature_schema_version"],
                row["source_manifest_sha256"],
            )
            self.ledger.setdefault(key, row)
        return _Result()


class _DB:
    def __init__(self):
        self.ledger = {}

    def table(self, name):
        assert name == "wow_d1_training_rows"
        return _ImmutableTrainingRowsQuery(self.ledger)


def _row() -> BinaryTrainingRow:
    return BinaryTrainingRow(
        event_id="event-1",
        event_start_time="2026-09-01T17:00:00+00:00",
        feature_as_of="2026-09-01T16:59:59+00:00",
        positive_outcome=True,
        features={name: 0.0 for name in result_form.FEATURE_NAMES},
        source_manifest_sha256="a" * 64,
    )


def test_result_form_training_row_persistence_is_idempotent_for_immutable_ledger():
    db = _DB()
    rows = [_row()]
    meta = [{"source_manifest": {"policy": result_form.SOURCE_POLICY_ID}}]

    result_form._persist_training_rows(db, rows, meta)
    first = dict(db.ledger)
    result_form._persist_training_rows(db, rows, meta)

    assert db.ledger == first
    assert len(db.ledger) == 1
    persisted = next(iter(db.ledger.values()))
    assert persisted["market_features_used"] is False
    assert persisted["can_execute"] is False
    assert persisted["historical_reconstruction"] is True
    assert persisted["archived_pregame_snapshot"] is False
