"""Selective nflverse injury-source refresh must be read-only by default."""
from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

import nfl_event_hydration_runtime as runtime
from nfl_event_data_p1 import CapturedAsset, DATASET_INJURIES, _scan_csv


def _capture(tmp_path):
    body = "\n".join([
        "season,team,week,season_type,gsis_id,position,report_status,practice_status,date_modified",
        "2026,IND,12,REG,00-1234,RB,Questionable,Limited,2026-11-18T00:00:00Z",
        "",
    ]).encode()
    local = tmp_path / "injuries_2026.csv"
    local.write_bytes(body)
    return CapturedAsset(
        dataset_name=DATASET_INJURIES,
        season=2026,
        requested_url="https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_2026.csv",
        resolved_url="https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_2026.csv",
        local_path=str(local),
        content_sha256=hashlib.sha256(body).hexdigest(),
        byte_count=len(body),
        row_count=1,
        column_names=("season", "team", "week", "season_type", "gsis_id", "position", "report_status", "practice_status", "date_modified"),
        fetched_at="2026-11-18T00:00:00+00:00",
        etag=None,
        last_modified=None,
        source_status="CAPTURED",
    )


def test_observation_downloads_only_injury_source_and_never_touches_db(monkeypatch, tmp_path):
    seen = []
    def fake_download(asset, path):
        seen.append((asset.dataset_name, asset.season))
        return _capture(tmp_path)
    monkeypatch.setattr(runtime, "download_asset", fake_download)
    monkeypatch.setattr(runtime, "_preserve_raw", lambda *_: pytest.fail("must not touch Storage"))
    monkeypatch.setattr(runtime, "_snapshot_id", lambda *_: pytest.fail("must not touch DB"))
    result = runtime.refresh_nfl_injury_source(season=2026)
    assert seen == [(DATASET_INJURIES, 2026)]
    assert result["status"] == "SOURCE_OBSERVED_NOT_PERSISTED"
    assert result["snapshot_id"] is None and result["raw_object_uri"] is None
    assert result["source_sha256"] == hashlib.sha256(Path(_capture(tmp_path).local_path).read_bytes()).hexdigest() and result["row_count"] == 1
    assert result["report_complete"] is False
    assert result["player_availability_verified"] is False
    assert result["probability_publishable"] is False and result["can_execute"] is False


@pytest.mark.parametrize("season", [2008, True, "2026", 0, 2101])
def test_invalid_season_rejected_before_any_download(monkeypatch, tmp_path, season):
    monkeypatch.setattr(runtime, "download_asset", lambda *_: pytest.fail("invalid input must fail first"))
    with pytest.raises(ValueError, match="NFL_INJURY_SOURCE_SEASON_INVALID"):
        runtime.refresh_nfl_injury_source(season=season)


def test_persistence_requires_explicit_db_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "download_asset", lambda *_: pytest.fail("must not download yet"))
    with pytest.raises(ValueError, match="PERSISTENCE_DB_REQUIRED"):
        runtime.refresh_nfl_injury_source(season=2026, persist=True)


def test_explicit_persistence_reuses_existing_content_addressed_helpers(monkeypatch, tmp_path):
    seen = []
    db = object()
    monkeypatch.setattr(runtime, "download_asset", lambda asset, path: _capture(tmp_path))
    def save_raw(received_db, capture):
        assert received_db is db and capture.content_sha256 == hashlib.sha256(Path(capture.local_path).read_bytes()).hexdigest()
        seen.append("raw")
        return "supabase://wow-nfl-raw/injuries/2026/hash.csv.gz"
    def save_snapshot(received_db, capture, uri):
        assert received_db is db and "injuries/2026" in uri
        seen.append("snapshot")
        return "snapshot-1"
    monkeypatch.setattr(runtime, "_preserve_raw", save_raw)
    monkeypatch.setattr(runtime, "_snapshot_id", save_snapshot)
    result = runtime.refresh_nfl_injury_source(season=2026, db=db, persist=True)
    assert seen == ["raw", "snapshot"]
    assert result["status"] == "SOURCE_PERSISTED"
    assert result["snapshot_id"] == "snapshot-1"
    assert result["player_availability_verified"] is False
    assert result["can_execute"] is False


def test_unexpected_capture_scope_fails_closed_before_persistence(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "download_asset", lambda *_: replace(_capture(tmp_path), season=2025))
    monkeypatch.setattr(runtime, "_preserve_raw", lambda *_: pytest.fail("must not write Storage"))
    monkeypatch.setattr(runtime, "_snapshot_id", lambda *_: pytest.fail("must not write DB"))
    with pytest.raises(RuntimeError, match="NFL_INJURY_SOURCE_CAPTURE_SCOPE_CONFLICT"):
        runtime.refresh_nfl_injury_source(season=2026, db=object(), persist=True)


def test_download_failure_propagates_without_writes(monkeypatch, tmp_path):
    def failure(*_):
        raise RuntimeError("NFL_SOURCE_DOWNLOAD_FAILED")
    monkeypatch.setattr(runtime, "download_asset", failure)
    monkeypatch.setattr(runtime, "_preserve_raw", lambda *_: pytest.fail("must not write Storage"))
    with pytest.raises(RuntimeError, match="NFL_SOURCE_DOWNLOAD_FAILED"):
        runtime.refresh_nfl_injury_source(season=2026, db=object(), persist=True)


def test_empty_source_is_not_written_even_when_persistence_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runtime, "download_asset",
        lambda *_: replace(_capture(tmp_path), row_count=0, source_status="CAPTURED_EMPTY"),
    )
    monkeypatch.setattr(runtime, "_preserve_raw", lambda *_: pytest.fail("empty must not touch Storage"))
    monkeypatch.setattr(runtime, "_snapshot_id", lambda *_: pytest.fail("empty must not touch DB"))
    with pytest.raises(RuntimeError, match="NFL_INJURY_SOURCE_CAPTURE_EMPTY_OR_UNUSABLE"):
        runtime.refresh_nfl_injury_source(season=2026, db=object(), persist=True)



@pytest.mark.parametrize("empty_row", ["", ",,,,,,,,"])
def test_header_only_or_blank_injury_rows_cannot_be_persisted(tmp_path, monkeypatch, empty_row):
    """A CSV record count of one may be an all-empty record, not an injury."""
    base = _capture(tmp_path)
    header = "season,team,week,season_type,gsis_id,position,report_status,practice_status,date_modified\n"
    body = (header + empty_row + "\n").encode()
    Path(base.local_path).write_bytes(body)
    columns, row_count = _scan_csv(Path(base.local_path), DATASET_INJURIES)
    assert row_count in {0, 1}  # _scan_csv can count an empty comma-delimited record
    captured = replace(
        base, content_sha256=hashlib.sha256(body).hexdigest(),
        byte_count=len(body), row_count=row_count,
        column_names=columns,
        source_status="CAPTURED" if row_count else "CAPTURED_EMPTY",
    )
    monkeypatch.setattr(runtime, "download_asset", lambda *_: captured)
    monkeypatch.setattr(runtime, "_preserve_raw", lambda *_: pytest.fail("blank CSV must not reach Storage"))
    monkeypatch.setattr(runtime, "_snapshot_id", lambda *_: pytest.fail("blank CSV must not reach database"))

    with pytest.raises(RuntimeError, match="NFL_INJURY_SOURCE_CAPTURE_EMPTY_OR_UNUSABLE"):
        runtime.refresh_nfl_injury_source(season=2026, db=object(), persist=True)
    with pytest.raises(RuntimeError, match="NFL_INJURY_SOURCE_CAPTURE_EMPTY_OR_UNUSABLE"):
        runtime.refresh_nfl_injury_source(season=2026)


def test_incomplete_row_after_valid_row_is_not_accepted(tmp_path, monkeypatch):
    base = _capture(tmp_path)
    with Path(base.local_path).open("a", encoding="utf-8") as handle:
        handle.write("2026,IND,12,REG,00-9999\n")
    byte_content = Path(base.local_path).read_bytes()
    captured = replace(base, content_sha256=hashlib.sha256(byte_content).hexdigest(), row_count=2)
    monkeypatch.setattr(runtime, "download_asset", lambda *_: captured)
    monkeypatch.setattr(runtime, "_preserve_raw", lambda *_: pytest.fail("malformed source must not persist"))
    with pytest.raises(RuntimeError, match="NFL_INJURY_SOURCE_CAPTURE_EMPTY_OR_UNUSABLE"):
        runtime.refresh_nfl_injury_source(season=2026, db=object(), persist=True)
