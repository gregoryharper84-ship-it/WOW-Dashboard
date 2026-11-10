from __future__ import annotations
from types import SimpleNamespace
from v17.daily_snapshot_runtime import _prop_manifest_rows

class _Query:
    def __init__(self, rows): self.rows=list(rows); self.start=0; self.end=len(self.rows)-1
    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, n): self.start,self.end=0,n-1; return self
    def range(self, start, end): self.start,self.end=start,end; return self
    def execute(self): return SimpleNamespace(data=self.rows[self.start:self.end+1])
class _DB:
    def __init__(self, rows): self.rows=rows
    def table(self, name): assert name == "wow_prop_evidence_snapshots"; return _Query(self.rows)

def _row(snapshot_id, player, line):
    return {"source_snapshot_id": snapshot_id, "captured_at": "2099-09-20T10:00:00Z", "event_id": f"evt-{player}", "event_start_time": "2099-09-20T20:00:00Z", "sport": "MLB", "player": player, "stat_type": "PITCHER_STRIKEOUTS", "line": line, "hydration_status": "PASS", "blockers": []}

def test_twenty_duplicate_snapshots_do_not_consume_four_prop_limit_slots():
    rows=[_row(f"dup-{i}", "Cristopher Sanchez", 6.5) for i in range(20)] + [_row("u-1", "Pitcher Two", 5.5), _row("u-2", "Pitcher Three", 4.5), _row("u-3", "Pitcher Four", 7.5)]
    manifest=_prop_manifest_rows(_DB(rows), "2099-09-20", "UTC")
    limited=manifest[:4]
    assert len(manifest) == 4
    assert [row["player"] for row in limited] == ["Cristopher Sanchez", "Pitcher Two", "Pitcher Three", "Pitcher Four"]
    assert limited[0]["source_instance_count"] == 20
    assert len(limited[0]["source_snapshot_ids"]) == 20


def test_newer_blocked_snapshot_overrides_older_clean_for_same_canonical_prop():
    old = _row("old-clean", "Cristopher Sanchez", 6.5)
    old["captured_at"] = "2099-09-19T12:00:00Z"
    newer = _row("new-unverified", "Cristopher Sanchez", 6.5)
    newer["captured_at"] = "2099-09-20T12:00:00Z"
    newer["blockers"] = ["PLAYER_AVAILABILITY_UNVERIFIED"]

    exclusions, stats = [], {}
    manifest = _prop_manifest_rows(
        _DB([old, newer]), "2099-09-20", "UTC",
        page_size=1, exclusions=exclusions, stats=stats,
    )
    assert manifest == []  # Never revive old-clean after new-unverified.
    assert len(exclusions) == 1
    assert exclusions[0]["source_snapshot_id"] == "new-unverified"
    assert set(exclusions[0]["source_snapshot_ids"]) == {"old-clean", "new-unverified"}
    assert exclusions[0]["source_instance_count"] == 2
    assert exclusions[0]["reason"] == "EVIDENCE_BLOCKED"
    assert exclusions[0]["blockers"] == ["PLAYER_AVAILABILITY_UNVERIFIED"]
    assert stats == {
        "raw_rows_read": 2,
        "canonical_rows": 1,
        "canonical_merged_rows": 1,
    }


def test_newer_clean_snapshot_supersedes_older_blocked_without_duplicate_exclusion():
    old = _row("old-held", "Pitcher", 4.5)
    old["captured_at"] = "2099-09-19T12:00:00Z"
    old["blockers"] = ["PLAYER_AVAILABILITY_UNVERIFIED"]
    newer = _row("new-clear", "Pitcher", 4.5)
    newer["captured_at"] = "2099-09-20T12:00:00Z"

    exclusions, stats = [], {}
    manifest = _prop_manifest_rows(
        _DB([old, newer]), "2099-09-20", "UTC",
        exclusions=exclusions, stats=stats,
    )
    assert len(manifest) == 1
    assert manifest[0]["source_snapshot_id"] == "new-clear"
    assert set(manifest[0]["source_snapshot_ids"]) == {"old-held", "new-clear"}
    assert not exclusions
    assert stats["raw_rows_read"] == 2
    assert stats["canonical_merged_rows"] == 1


def test_newer_failed_hydration_supersedes_old_pass_even_without_explicit_blocker():
    prior = _row("old-pass", "Pitcher", 4.5)
    prior["captured_at"] = "2099-09-19T12:00:00Z"
    newer = _row("new-failed", "Pitcher", 4.5)
    newer["captured_at"] = "2099-09-20T12:00:00Z"
    newer["hydration_status"] = "FAILED"
    newer["blockers"] = []

    exclusions, stats = [], {}
    rows = _prop_manifest_rows(
        _DB([prior, newer]), "2099-09-20", "UTC",
        page_size=1, exclusions=exclusions, stats=stats,
    )
    assert rows == []
    assert len(exclusions) == 1
    assert exclusions[0]["source_snapshot_id"] == "new-failed"
    assert exclusions[0]["reason"] == "EVIDENCE_HYDRATION_NOT_PASSED"
    assert set(exclusions[0]["source_snapshot_ids"]) == {"old-pass", "new-failed"}
    assert stats["raw_rows_read"] == 2
    assert stats["canonical_merged_rows"] == 1
