"""#1530: pregame manifest provenance is source bytes + freeze time, not a title."""
from __future__ import annotations

import gzip
import hashlib
from io import StringIO
import csv

import pytest

from v17.nfl_pickem_weekly_evidence import WeeklyEvidenceError
from v17.nfl_pickem_weekly_manifest import freeze_nflverse_week_candidate


def _fixture(count=3, *, modified=None):
    rows = []
    for i in range(count):
        rows.append({
            "game_id": f"2026_05_AWAY{i}_HOME{i}", "season": "2026",
            "week": "5", "game_type": "REG", "gameday": "2026-10-11",
            "gametime": "13:00", "home_team": f"HOME{i}",
            "away_team": f"AWAY{i}", "home_score": "", "away_score": "",
        })
    if modified:
        modified(rows)
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=tuple(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    raw = buffer.getvalue().encode()
    checksum = hashlib.sha256(raw).hexdigest()
    return {
        "snapshot": {
            "snapshot_id": "original-snapshot-W5",
            "source_family": "NFLVERSE_PUBLIC_DATA",
            "dataset_name": "SCHEDULES",
            "source_status": "CAPTURED",
            "content_sha256": checksum,
            "fetched_at": "2026-10-10T13:00:00Z",
        },
        "compressed_raw_csv": gzip.compress(raw, mtime=0),
        "season": 2026, "week": 5, "expected_game_count": count,
        "manifest_frozen_at": "2026-10-10T15:00:00Z",
    }


def test_exact_pre_kickoff_checksum_verified_full_week_frozen_candidate():
    out = freeze_nflverse_week_candidate(**_fixture())
    assert out["manifest_status"] == "FROZEN"
    assert out["manifest_version"] == "NFL_PICKEM_SOURCE_FROZEN_CANDIDATE_V1"
    assert out["expected_game_count"] == len(out["events"]) == 3
    assert len({e["official_event_id"] for e in out["events"]}) == 3
    assert all(e["schedule_status"] == "SCHEDULED" for e in out["events"])
    assert all(e["event_start_time_utc"].startswith("2026-10-11T17:00:00") for e in out["events"])
    assert out["manifest_receipt_id"].startswith("nfl-pickem-source-manifest-")
    assert out["source_authenticity_verified"] is False
    assert out["publication_allowed"] is False
    assert out["can_execute"] is False


def test_same_source_and_freeze_produce_same_candidate_no_implicit_db_write():
    first = freeze_nflverse_week_candidate(**_fixture())
    second = freeze_nflverse_week_candidate(**_fixture())
    assert first == second


@pytest.mark.parametrize(("change", "code"), [
    (lambda d: d["snapshot"].update({"content_sha256": "0" * 64}),
     "PICKEM_FROZEN_SOURCE_CHECKSUM_MISMATCH"),
    (lambda d: d["snapshot"].update({"content_sha256": "not-a-digest"}),
     "PICKEM_FROZEN_SOURCE_HASH_INVALID"),
    (lambda d: d["snapshot"].update({"dataset_name": "PLAY_BY_PLAY"}),
     "PICKEM_FROZEN_SOURCE_IDENTITY_NOT_ALLOWED"),
    (lambda d: d["snapshot"].update({"source_family": "UNVERIFIED_GUESSES"}),
     "PICKEM_FROZEN_SOURCE_IDENTITY_NOT_ALLOWED"),
    (lambda d: d["snapshot"].update({"source_status": "FAILED"}),
     "PICKEM_FROZEN_SOURCE_IDENTITY_NOT_ALLOWED"),
    (lambda d: d.update({"compressed_raw_csv": b"invalid-gzip"}),
     "PICKEM_FROZEN_SOURCE_DECOMPRESSION_FAILED"),
    (lambda d: d.update({"manifest_frozen_at": "2026-10-11T18:00:00Z"}),
     "PICKEM_FROZEN_AFTER_EARLIEST_KICKOFF"),
    (lambda d: d.update({"manifest_frozen_at": "2026-10-10T12:00:00Z"}),
     "PICKEM_FROZEN_SOURCE_CAPTURED_AFTER_FREEZE"),
    (lambda d: d.update({"expected_game_count": 2}),
     "PICKEM_FROZEN_FULL_WEEK_COUNT_UNVERIFIED"),
    (lambda d: d.update({"manifest_frozen_at": "2026-10-10T15:00:00"}),
     "PICKEM_FROZEN_AT_INVALID"),
])
def test_invalid_source_checksum_identity_or_clock_is_hard_failure(change, code):
    data = _fixture()
    change(data)
    with pytest.raises(WeeklyEvidenceError, match=code):
        freeze_nflverse_week_candidate(**data)


@pytest.mark.parametrize(("alter", "code"), [
    (lambda rows: rows[1].update({"game_id": rows[0]["game_id"]}),
     "PICKEM_FROZEN_DUPLICATE_EVENT"),
    (lambda rows: rows[0].update({"home_team": rows[0]["away_team"]}),
     "PICKEM_FROZEN_HOME_AWAY_COLLISION"),
    (lambda rows: rows[1].update({"home_score": "0"}),
     "PICKEM_FROZEN_SOURCE_CONTAINS_GAME_SCORE"),
    (lambda rows: rows[1].update({"away_score": "23"}),
     "PICKEM_FROZEN_SOURCE_CONTAINS_GAME_SCORE"),
    (lambda rows: rows[0].update({"gametime": "not-a-time"}),
     "PICKEM_FROZEN_KICKOFF_INVALID"),
])
def test_bad_official_event_rows_do_not_partially_freeze(alter, code):
    with pytest.raises(WeeklyEvidenceError, match=code):
        freeze_nflverse_week_candidate(**_fixture(modified=alter))


def test_read_size_is_bounded_during_gzip_decompression(monkeypatch):
    """Counterexample: compressed bombs must never use unbounded gzip.decompress."""
    from v17 import nfl_pickem_weekly_manifest as manifest
    requested_limits = []

    class SafeStream:
        def __init__(self, fileobj):
            assert fileobj is not None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, amount):
            requested_limits.append(amount)
            return b"a"  # Now expected checksum fails, but expansion was bounded.

    monkeypatch.setattr(manifest.gzip, "GzipFile", SafeStream)
    with pytest.raises(WeeklyEvidenceError, match="PICKEM_FROZEN_SOURCE_CHECKSUM_MISMATCH"):
        freeze_nflverse_week_candidate(**_fixture())
    assert requested_limits == [50_000_001]


def test_manifest_can_flow_into_unpublished_weekly_reconciliation():
    """The upstream gate yields compatible fields but no false scorecard."""
    from v17.nfl_pickem_weekly_evidence import (
        BLOCKED, reconcile_weekly_evidence,
    )
    args = _fixture()
    manifest = freeze_nflverse_week_candidate(**args)
    result = reconcile_weekly_evidence(
        season=2026, week=5, official_manifest=manifest,
        immutable_predictions=[], official_settlement_history=[],
    )
    assert result["status"] == BLOCKED
    assert result["expected_game_count"] == 3
    assert result["blocker_count"] == 6
    assert result["report"] is None
    assert result["publication_allowed"] is False


def test_missing_score_columns_are_not_evidence_of_unplayed_game():
    def omit_column(rows):
        for row in rows:
            row.pop("home_score")

    with pytest.raises(WeeklyEvidenceError, match="PICKEM_FROZEN_SCHEDULE_COLUMNS_MISSING"):
        freeze_nflverse_week_candidate(**_fixture(modified=omit_column))
