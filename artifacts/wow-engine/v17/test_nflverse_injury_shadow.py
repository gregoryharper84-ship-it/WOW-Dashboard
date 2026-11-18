"""Fail-closed regression tests for the hashed nflverse NFL injury shadow adapter."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from nfl_event_data_p1 import CapturedAsset, DATASET_INJURIES, season_assets
from v17.nflverse_injury_shadow import inspect_captured_injuries

HEADER = "season,team,week,season_type,gsis_id,full_name,position,report_status,practice_status,date_modified\n"
START = "2026-11-20T01:15:00+00:00"
NOW = "2026-11-18T18:30:00+00:00"


def capture(tmp_path: Path, rows: str, **overrides) -> CapturedAsset:
    content = (HEADER + rows).encode()
    path = tmp_path / "injuries-2026.csv"
    path.write_bytes(content)
    base = CapturedAsset(
        dataset_name=DATASET_INJURIES, season=2026,
        requested_url=next(x.source_url for x in season_assets(2026) if x.dataset_name == DATASET_INJURIES),
        resolved_url="https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_2026.csv",
        local_path=str(path), content_sha256=hashlib.sha256(content).hexdigest(),
        byte_count=len(content), row_count=len(rows.splitlines()), column_names=tuple(HEADER.strip().split(",")),
        fetched_at="2026-11-18T18:00:00+00:00", etag=None, last_modified=None, source_status="CAPTURED",
    )
    return replace(base, **overrides)


def inspect(asset, **kwargs):
    return inspect_captured_injuries(
        asset, gsis_id="00-1234", team="IND", season=2026, week=12,
        season_type="REG", official_event_id="2026_11_IND_HOU",
        event_start_utc=START, now_utc=NOW, **kwargs,
    )


def test_reported_out_never_grants_availability(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    result = inspect(asset)
    assert result["reported_designation"] == "OUT"
    assert result["blocker"] == "NFLVERSE_INJURY_REPORTED_OUT_RECHECK_REQUIRED"
    assert result["status"] == "UNVERIFIED"
    assert result["report_complete"] is False
    assert result["available_for_scoring"] is False
    assert result["can_execute"] is False and result["probability_publishable"] is False


def test_questionable_is_risk_not_playable(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Questionable,Limited,2026-11-18T17:00:00Z\n")
    result = inspect(asset)
    assert result["blocker"] == "NFLVERSE_INJURY_RISK_RECHECK_REQUIRED"
    assert result["status"] == "UNVERIFIED"


def test_full_practice_or_empty_report_not_official_clearance(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,,Full,2026-11-18T17:00:00Z\n")
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_REPORT_NOT_OFFICIAL_CLEARANCE"
    assert inspect(asset)["available_for_scoring"] is False


def test_absent_gsis_cannot_be_matched_by_player_name(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-9999,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_TARGET_ROW_MISSING"


def test_wrong_team_or_week_never_applies_to_new_event(tmp_path):
    asset = capture(tmp_path, "2026,HOU,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_PLAYER_TEAM_OR_WEEK_MISMATCH"


def test_stale_and_future_rows_are_rejected(tmp_path):
    stale = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-15T10:00:00Z\n")
    assert inspect(stale)["blocker"] == "NFLVERSE_INJURY_REPORT_STALE"
    future = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-20T02:00:00Z\n")
    assert inspect(future)["blocker"] == "NFLVERSE_INJURY_REPORT_TIMESTAMP_UNVERIFIED"


def test_same_timestamp_conflicting_designations_fail_closed(tmp_path):
    asset = capture(tmp_path, (
        "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n"
        "2026,IND,12,REG,00-1234,A Player,RB,Questionable,Limited,2026-11-18T17:00:00Z\n"
    ))
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_REPORT_CONFLICT"


def test_hash_mismatch_is_never_accepted(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    asset = replace(asset, content_sha256="0" * 64)
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_CAPTURE_HASH_MISMATCH"


def test_captured_after_event_cannot_be_backdated(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n", fetched_at="2026-11-20T02:00:00+00:00")
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_EVENT_OR_CAPTURE_TIME_UNVERIFIED"


def test_naive_unverified_and_malformed_schema(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18 17:00:00\n")
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_REPORT_TIMESTAMP_UNVERIFIED"
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    asset.local_path  # source is immutable capture record
    bad = replace(asset, season=2025)
    assert inspect(bad)["blocker"] == "NFLVERSE_INJURY_CAPTURE_SCOPE_MISMATCH"


def test_missing_capture_file_holds(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    Path(asset.local_path).unlink()
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_CAPTURE_UNREADABLE"


def test_modified_bytes_with_matching_hash_but_invalid_schema(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    bad = b"season,team,gsis_id\n2026,IND,00-1234\n"
    Path(asset.local_path).write_bytes(bad)
    asset = replace(asset, content_sha256=hashlib.sha256(bad).hexdigest())
    assert inspect(asset)["blocker"] == "NFLVERSE_INJURY_SCHEMA_INVALID"


def test_implicit_identity_and_postkickoff_are_rejected(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    unresolved = inspect_captured_injuries(
        asset, gsis_id="", team="IND", season=2026, week=12,
        season_type="REG", official_event_id="2026_11_IND_HOU",
        event_start_utc=START, now_utc=NOW,
    )
    assert unresolved["blocker"] == "NFLVERSE_INJURY_IDENTITY_UNRESOLVED"
    after_kickoff = inspect_captured_injuries(
        asset, gsis_id="00-1234", team="IND", season=2026, week=12,
        season_type="REG", official_event_id="2026_11_IND_HOU",
        event_start_utc=START, now_utc="2026-11-20T02:00:00Z",
    )
    assert after_kickoff["blocker"] == "NFLVERSE_INJURY_EVENT_OR_CAPTURE_TIME_UNVERIFIED"


def test_later_report_replaces_older_report_but_not_clearance(tmp_path):
    asset = capture(tmp_path, (
        "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T12:00:00Z\n"
        "2026,IND,12,REG,00-1234,A Player,RB,Questionable,Limited,2026-11-18T17:00:00Z\n"
    ))
    result = inspect(asset)
    assert result["blocker"] == "NFLVERSE_INJURY_RISK_RECHECK_REQUIRED"
    assert result["source_row_n"] == 2
    assert result["reported_designation"] == "QUESTIONABLE"
    assert result["status"] == "UNVERIFIED"
    assert result["available_for_scoring"] is False


def test_malformed_rows_and_duplicate_columns_are_typed_holds(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    # Duplicate header or extra CSV cell must not be interpreted as evidence.
    duplicate_header = (HEADER.replace("date_modified", "report_status") +
        "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,Out\n").encode()
    Path(asset.local_path).write_bytes(duplicate_header)
    corrupt = replace(asset, content_sha256=hashlib.sha256(duplicate_header).hexdigest())
    assert inspect(corrupt)["blocker"] == "NFLVERSE_INJURY_SCHEMA_INVALID"

    overflow = (HEADER + "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z,EXTRA\n").encode()
    Path(asset.local_path).write_bytes(overflow)
    corrupt = replace(asset, content_sha256=hashlib.sha256(overflow).hexdigest())
    assert inspect(corrupt)["blocker"] == "NFLVERSE_INJURY_SCHEMA_INVALID"


def test_truncated_csv_row_is_schema_failure_not_implicit_clearance(tmp_path):
    asset = capture(tmp_path, "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T17:00:00Z\n")
    truncated = (HEADER + "2026,IND,12,REG,00-1234\n").encode()
    Path(asset.local_path).write_bytes(truncated)
    corrupt = replace(asset, content_sha256=hashlib.sha256(truncated).hexdigest())
    result = inspect(corrupt)
    assert result["blocker"] == "NFLVERSE_INJURY_SCHEMA_INVALID"
    assert result["available_for_scoring"] is False


def test_report_timestamp_after_source_capture_fails_even_when_before_inspection(tmp_path):
    """A later row cannot already exist inside an earlier immutable capture."""
    asset = capture(
        tmp_path,
        "2026,IND,12,REG,00-1234,A Player,RB,Out,DNP,2026-11-18T18:15:00Z\n",
        fetched_at="2026-11-18T18:00:00+00:00",
    )
    result = inspect(asset)
    assert result["blocker"] == "NFLVERSE_INJURY_REPORT_TIMESTAMP_UNVERIFIED"
    assert result["reported_designation"] is None
    assert result["reported_at"] is None
    assert result["available_for_scoring"] is False
    assert result["can_execute"] is False
