from __future__ import annotations

import pytest

from ncaaf_evidence_ingestion import NCAAFAcquisitionUnavailable, persist_normalized_evidence


class Result:
    data = [{"ok": True}]


class Table:
    def __init__(self):
        self.rows = None
    def upsert(self, rows, on_conflict=None):
        self.rows = rows
        assert on_conflict == "official_event_id,evidence_kind,scope,source_provider,payload_sha256"
        return self
    def execute(self):
        return Result()


class DB:
    def __init__(self):
        self.table_obj = Table()
    def table(self, name):
        assert name == "wow_ncaaf_pregame_evidence"
        return self.table_obj


def row(kind="PLAYER_AVAILABILITY_REPORT"):
    return {
        "official_event_id": "evt-1",
        "event_start_time": "2026-09-05T23:00:00+00:00",
        "evidence_kind": kind,
        "scope": "EVENT" if kind == "PLAYER_AVAILABILITY_REPORT" else "HOME",
        "team": None if kind == "PLAYER_AVAILABILITY_REPORT" else "Texas",
        "player": "QB One",
        "source_provider": "BIG12_OFFICIAL_AVAILABILITY",
        "source_record_id": "r1",
        "source_uri": "https://official.example/report",
        "evidence_timestamp": "2026-09-05T20:00:00+00:00",
        "provenance_grade": "A",
        "payload": {"team": "Texas", "status": "PROBABLE"},
        "payload_sha256": "a" * 64,
        "blocker_codes": [],
        "probability_publishable": False,
        "can_execute": False,
    }


def test_persists_normalized_raw_evidence_and_strips_publication_flag():
    db = DB()
    assert persist_normalized_evidence(db, [row()]) == 1
    assert "probability_publishable" not in db.table_obj.rows[0]
    assert db.table_obj.rows[0]["can_execute"] is False


def test_rejects_execution_flag():
    bad = row()
    bad["can_execute"] = True
    with pytest.raises(NCAAFAcquisitionUnavailable) as exc:
        persist_normalized_evidence(DB(), [bad])
    assert exc.value.code == "NCAAF_EVIDENCE_EXECUTION_FLAG_INVALID"


def test_rejects_arbitrary_model_ready_evidence_kind():
    with pytest.raises(NCAAFAcquisitionUnavailable) as exc:
        persist_normalized_evidence(DB(), [row("TEAM_POWER")])
    assert exc.value.code == "NCAAF_EVIDENCE_KIND_NOT_ALLOWED_BY_INGESTION"
