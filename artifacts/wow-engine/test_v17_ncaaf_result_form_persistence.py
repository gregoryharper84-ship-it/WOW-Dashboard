from __future__ import annotations

from types import SimpleNamespace

from v17.binary_candidate_lifecycle import BinaryTrainingRow
from v17 import ncaaf_result_form_candidate as result_form


class FakeQuery:
    def __init__(self):
        self.upsert_calls = []

    def upsert(self, payload, **kwargs):
        self.upsert_calls.append((payload, kwargs))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self):
        self.query = FakeQuery()
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return self.query


def test_result_form_training_rows_use_insert_only_conflict_handling():
    client = FakeClient()
    row = BinaryTrainingRow(
        event_id="event-1",
        event_start_time="2026-09-01T00:00:00+00:00",
        feature_as_of="2026-08-31T23:59:59+00:00",
        positive_outcome=True,
        features={name: 0.0 for name in result_form.FEATURE_NAMES},
        source_manifest_sha256="a" * 64,
    )
    meta = [{"source_manifest": {"policy": result_form.SOURCE_POLICY_ID}}]

    result_form._persist_training_rows(client, [row], meta)

    assert client.tables == ["wow_d1_training_rows"]
    assert len(client.query.upsert_calls) == 1
    _, kwargs = client.query.upsert_calls[0]
    assert kwargs["on_conflict"] == "sport,official_event_id,feature_schema_version,source_manifest_sha256"
    assert kwargs["ignore_duplicates"] is True
