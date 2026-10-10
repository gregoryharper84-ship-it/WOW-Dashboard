"""#1562: replay must read the candidate's frozen corpus, not the growing ledger."""
from __future__ import annotations

from types import SimpleNamespace

import v17.candidate_certification_evidence as evidence

CANDIDATE_CREATED = "2026-09-25T20:39:14.055195+00:00"


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.lte_filters = db, table, {}, {}
        self.order_key, self.bounds, self.payload = None, None, None

    def select(self, *_a, **_k):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def lte(self, key, value):
        self.lte_filters[key] = value
        return self

    def order(self, key, *_a, **_k):
        self.order_key = key
        return self

    def range(self, lo, hi):
        self.bounds = (lo, hi)
        return self

    def limit(self, n):
        self.bounds = (0, n - 1)
        return self

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        if self.payload is not None:
            self.db.inserted.append(self.payload)
            return SimpleNamespace(data=[{**self.payload, "receipt_id": "r1"}])
        rows = [
            r for r in self.db.tables.get(self.table, [])
            if all(r.get(k) == v for k, v in self.filters.items())
            and all(evidence._iso(r[k]) <= evidence._iso(v) for k, v in self.lte_filters.items())
        ]
        if self.order_key:
            rows.sort(key=lambda r: r[self.order_key])
        if self.bounds:
            rows = rows[self.bounds[0]: self.bounds[1] + 1]
        return SimpleNamespace(data=rows)


class _DB:
    def __init__(self, tables):
        self.tables, self.inserted = tables, []

    def table(self, name):
        return _Query(self, name)


def _row(i, created_at):
    return {
        "training_row_id": i,
        "created_at": created_at,
        "sport": "NCAAF",
        "league": "NCAAF",
        "model_family": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "feature_schema_version": "S1",
        "official_event_id": f"E{i % 1500}",
        "event_start_time": f"2024-09-{1 + i % 28:02d}T20:00:00+00:00",
    }


def _candidate(**overrides):
    base = {
        "candidate_id": "c1",
        "created_at": CANDIDATE_CREATED,
        "sport": "NCAAF",
        "league": "NCAAF",
        "model_family": "NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2",
        "feature_schema_version": "S1",
    }
    base.update(overrides)
    return base


def test_rows_appended_after_candidate_are_excluded_from_replay_corpus():
    frozen = [_row(i, "2026-09-20T00:00:00+00:00") for i in range(1200)]  # spans 2 pages
    later = [_row(5000 + i, "2026-10-10T04:00:00+00:00") for i in range(900)]  # incl. duplicate events
    db = _DB({"wow_d1_training_rows": frozen + later})

    rows = evidence._all_rows(db, _candidate())

    assert len(rows) == 1200


def test_row_created_exactly_at_candidate_time_is_included():
    db = _DB({"wow_d1_training_rows": [_row(1, CANDIDATE_CREATED), _row(2, "2026-09-25T20:39:14.055196+00:00")]})
    assert len(evidence._all_rows(db, _candidate())) == 1


def test_candidate_without_created_at_fails_closed_with_typed_blocker():
    candidate = {
        **_candidate(created_at=None),
        "promoted": False, "active": False, "automatic_certification": False,
        "automatic_promotion": False, "probability_publishable": False,
        "can_execute": False, "lifecycle_state": "CANDIDATE", "research_screen_pass": True,
        "source_policy_id": "TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1",
        "model_artifact_version": "v", "training_dataset_hash": "h", "artifact_checksum": "a",
        "training_rows": 1, "calibration_rows": 0, "test_rows": 0,
    }
    db = _DB({"wow_d1_candidate_artifacts": [candidate], "wow_d1_training_rows": [_row(1, "2026-09-20T00:00:00+00:00")],
              "wow_d1_certification_evidence_receipts": []})

    result = evidence.verify_candidate_certification_evidence(db, "c1")

    assert result["status"] == "CERTIFICATION_EVIDENCE_FAILED"
    assert "CANDIDATE_CREATED_AT_REQUIRED_FOR_FROZEN_CORPUS" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert db.inserted and db.inserted[0]["replay_status"] == "FAIL"


def test_frozen_candidate_uses_latest_event_version_and_ignores_later_writes():
    # A dynamic challenger trains once per canonical game, although later
    # maintenance appends immutable source versions for the same event.
    old = _row(1, "2026-09-20T00:00:00+00:00")
    old["source_manifest_sha256"] = "old"
    fresh = _row(1501, "2026-09-25T20:39:13+00:00")
    fresh["source_manifest_sha256"] = "fresh"
    after_cutoff = _row(3001, "2026-09-26T00:00:00+00:00")
    after_cutoff["source_manifest_sha256"] = "not-visible"
    separate = _row(2, "2026-09-20T00:00:00+00:00")
    assert old["official_event_id"] == fresh["official_event_id"] == after_cutoff["official_event_id"]
    db = _DB({"wow_d1_training_rows": [old, fresh, after_cutoff, separate]})
    rows = evidence._all_rows(db, _candidate())
    assert len(rows) == 2
    one = next(row for row in rows if row["official_event_id"] == old["official_event_id"])
    assert one["source_manifest_sha256"] == "fresh"


def test_frozen_candidate_rejects_ambiguous_latest_source_fork():
    one = _row(1, "2026-09-25T20:39:13+00:00")
    other = _row(1501, "2026-09-25T20:39:13+00:00")
    one["source_manifest_sha256"] = "manifest-A"
    other["source_manifest_sha256"] = "manifest-B"
    db = _DB({"wow_d1_training_rows": [one, other]})
    import pytest
    with pytest.raises(ValueError, match="CANDIDATE_CORPUS_LATEST_VERSION_AMBIGUOUS"):
        evidence._all_rows(db, _candidate())


def test_identical_same_instant_source_rewrite_does_not_double_count():
    one = _row(1, "2026-09-25T20:39:13+00:00")
    other = _row(1501, "2026-09-25T20:39:13+00:00")
    one.update({"source_manifest_sha256": "identical", "features": {"p": 1.0}})
    other.update({"source_manifest_sha256": "identical", "features": {"p": 1.0},
                  "event_start_time": one["event_start_time"]})
    db = _DB({"wow_d1_training_rows": [one, other]})
    assert len(evidence._all_rows(db, _candidate())) == 1


def test_frozen_corpus_replay_version_bumped_for_new_receipts():
    assert evidence.VERIFIER_VERSION == "V17_CANDIDATE_BOUND_BINARY_REPLAY_V2"


def test_frozen_candidate_missing_canonical_event_identity_fails():
    bad = _row(1, "2026-09-25T20:39:13+00:00")
    bad["official_event_id"] = ""
    db = _DB({"wow_d1_training_rows": [bad]})
    import pytest
    with pytest.raises(ValueError, match="CANDIDATE_CORPUS_OFFICIAL_EVENT_ID_MISSING"):
        evidence._all_rows(db, _candidate())
