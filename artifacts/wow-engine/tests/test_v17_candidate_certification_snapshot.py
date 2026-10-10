"""Frozen candidate certification must not replay subsequently appended rows.

This targets a real NCAAF failure: original candidate at 2026-09-25T20:39Z
expected 3065 rows but unrestricted family selection reached 6139 by Oct 10.
Only the original, hash-verified snapshot can be replayed.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v17 import candidate_certification_evidence as evidence


CUTOFF = "2026-09-25T20:39:14.055195+00:00"


def _candidate(**kwargs):
    base = {
        "created_at": CUTOFF,
        "sport": "NCAAF",
        "league": "NCAAF",
        "model_family": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "feature_schema_version": "NCAAF_DYNAMIC_TEAM_STATE_FEATURES_V2",
    }
    return {**base, **kwargs}


def _row(i, *, created_at="2026-09-25T20:38:00+00:00", sport="NCAAF",
         family="NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2"):
    return {
        "training_row_id": f"{i:06d}",
        "created_at": created_at,
        "sport": sport,
        "league": "NCAAF",
        "model_family": family,
        "feature_schema_version": "NCAAF_DYNAMIC_TEAM_STATE_FEATURES_V2",
        "official_event_id": f"game-{i:06d}",
        "event_start_time": "2026-09-01T19:00:00Z",
        "feature_as_of": "2026-09-01T18:00:00Z",
        "features": {"example": 1.0},
        "outcome_json": {"home_win": True},
        "source_manifest": {},
        "source_manifest_sha256": "",
        "market_features_used": False,
        "can_execute": False,
    }


class _RowsQuery:
    def __init__(self, data):
        self.data = data
        self.filters = []
        self.cutoff = None
        self.order_by = None
        self.from_ = None
        self.to = None

    def select(self, names):
        return self

    def eq(self, name, value):
        self.filters.append((name, value))
        return self

    def lte(self, name, value):
        assert name == "created_at"
        self.cutoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return self

    def order(self, column):
        self.order_by = column
        return self

    def range(self, start, end):
        self.from_, self.to = start, end
        return self

    def execute(self):
        assert self.cutoff is not None, "Missing candidate snapshot cutoff"
        assert self.order_by == "training_row_id", "Pagination must have stable unique ordering"
        found = [
            row for row in self.data
            if all(row.get(name) == value for name, value in self.filters)
            and datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) <= self.cutoff
        ]
        found.sort(key=lambda row: row["training_row_id"])
        return SimpleNamespace(data=found[self.from_:self.to + 1])


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "wow_d1_training_rows"
        return _RowsQuery(self.rows)


def test_candidate_replay_selects_only_rows_existing_at_creation():
    original = [_row(i) for i in range(3065)]
    future = [
        _row(i, created_at="2026-10-09T22:00:00+00:00")
        for i in range(3065, 6139)
    ]
    rows = evidence._all_rows(_DB(list(reversed(original + future))), _candidate())
    assert len(rows) == 3065
    assert len({row["official_event_id"] for row in rows}) == 3065
    assert rows[0]["official_event_id"] == "game-000000"
    assert rows[-1]["official_event_id"] == "game-003064"
    assert all(row["can_execute"] is False for row in rows)


def test_snapshot_time_is_inclusive_but_newer_row_in_same_second_is_excluded():
    rows = evidence._all_rows(
        _DB([
            _row(1, created_at=CUTOFF),
            _row(2, created_at="2026-09-25T20:39:14.055196+00:00"),
        ]),
        _candidate(),
    )
    assert [r["official_event_id"] for r in rows] == ["game-000001"]


def test_replay_does_not_pull_other_sports_or_feature_families():
    rows = evidence._all_rows(
        _DB([
            _row(1),
            _row(2, sport="NHL"),
            _row(3, family="NCAAF_RESULT_FORM_LOGIT_V1"),
            _row(4, created_at="2026-10-01T00:00:00+00:00"),
        ]),
        _candidate(),
    )
    assert [r["official_event_id"] for r in rows] == ["game-000001"]


@pytest.mark.parametrize("bad", [None, "", "not-a-timestamp"])
def test_missing_or_invalid_snapshot_cannot_load_mutable_rows(bad):
    with pytest.raises((TypeError, ValueError)):
        evidence._all_rows(_DB([_row(1)]), _candidate(created_at=bad))


def test_invalid_candidate_cutoff_returns_typed_certification_hold_without_rows():
    from unittest.mock import patch

    candidate = {
        **_candidate(created_at="bad-timestamp"),
        "candidate_id": "11111111-1111-1111-1111-111111111111",
        "model_artifact_version": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2_frozen",
        "training_dataset_hash": "a" * 64,
        "artifact_checksum": "b" * 64,
        "source_policy_id": "TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1",
        "research_screen_pass": True,
        "lifecycle_state": "CANDIDATE",
        "training_rows": 1,
        "calibration_rows": 1,
        "test_rows": 1,
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }

    class CandidateQuery:
        def select(self, *args):
            return self

        def eq(self, *args):
            return self

        def limit(self, *args):
            return self

        def execute(self):
            return SimpleNamespace(data=[candidate])

    class CandidateDB:
        def table(self, name):
            assert name == "wow_d1_candidate_artifacts", (
                "Invalid timestamp must not access the training row ledger"
            )
            return CandidateQuery()

    fake_receipt = {
        "source_review_status": "FAIL",
        "replay_status": "FAIL",
        "receipt_id": "synthetic-hold",
        "evidence_sha256": "synthetic-hash",
    }
    with (
        patch.object(evidence, "_all_rows", side_effect=AssertionError("mutable training read")),
        patch.object(evidence, "_persist", return_value=fake_receipt) as persist,
    ):
        result = evidence.verify_candidate_certification_evidence(
            CandidateDB(), candidate["candidate_id"]
        )

    assert result["status"] == "CERTIFICATION_EVIDENCE_FAILED"
    assert "CANDIDATE_SNAPSHOT_TIME_INVALID" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert persist.called
