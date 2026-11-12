"""Pinned supplementary roster is observation-only; never guessed model IDs."""
from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_nfl_count_prop_roster_crosswalk as mod


class FakeResponse(io.BytesIO):
    def geturl(self):
        return "https://release-assets.githubusercontent.com/verified-roster.csv"


def _fixture(monkeypatch, rows):
    data = pd.DataFrame(rows).to_csv(index=False).encode()
    monkeypatch.setattr(mod, "ROSTER_2025_SIZE", len(data))
    monkeypatch.setattr(mod, "ROSTER_2025_SHA256", hashlib.sha256(data).hexdigest())
    return data


def test_exact_pinned_roster_pair_is_review_only(monkeypatch):
    data = _fixture(monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0099999", "season": 2025,
         "position": "RB", "team": "ATL"},
        {"pfr_id": "Other01", "gsis_id": "00-0088888", "season": 2025,
         "position": "RB", "team": "MIN"},
    ])
    frame, receipt = mod.read_pinned_2025_roster(
        open_url=lambda *args, **kwargs: FakeResponse(data)
    )
    result = mod.inspect_roster_id_candidates(frame, ["CartNa00", "ShenJo00"])
    assert receipt["bytes_sha256_verified"] == len(data)
    assert result["source_exact_pair_candidate_count"] == 1
    assert result["targets"][0]["gsis_candidates"] == ["00-0099999"]
    assert result["targets"][1]["gsis_candidates"] == []
    assert result["status"] == "HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW"
    assert result["candidate_crosswalk_applied"] is False
    assert result["fitted_specialist_certified"] is False
    assert result["can_execute"] is False


def test_roster_wrong_digest_rejected_before_any_candidate(monkeypatch):
    data = _fixture(monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0099999", "season": 2025,
         "position": "RB", "team": "ATL"}
    ])
    monkeypatch.setattr(mod, "ROSTER_2025_SHA256", "f" * 64)
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_DIGEST_MISMATCH"):
        mod.read_pinned_2025_roster(open_url=lambda *a, **kw: FakeResponse(data))


def test_roster_corrupt_season_rejected_after_exact_hash(monkeypatch):
    data = _fixture(monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0099999", "season": 2024,
         "position": "RB", "team": "ATL"}
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.read_pinned_2025_roster(open_url=lambda *a, **kw: FakeResponse(data))


def test_conflicting_exact_pfr_pairs_rejected():
    roster = pd.DataFrame([
        {"pfr_id": "WhitCo05", "gsis_id": "00-0032222", "season": "2025",
         "position": "WR", "team": "PIT"},
        {"pfr_id": "WhitCo05", "gsis_id": "00-0031111", "season": "2025",
         "position": "WR", "team": "BUF"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_CROSSWALK_CONFLICT"):
        mod.inspect_roster_id_candidates(roster, ["WhitCo05"])


def test_name_team_or_position_without_exact_id_is_not_a_mapping():
    roster = pd.DataFrame([
        {"pfr_id": None, "gsis_id": "00-0099999", "season": "2025",
         "position": "RB", "team": "ATL"},
        {"pfr_id": "Unrelated00", "gsis_id": "00-0088888", "season": "2025",
         "position": "RB", "team": "ATL"},
    ])
    result = mod.inspect_roster_id_candidates(roster, ["CartNa00"])
    assert result["targets"][0]["gsis_candidates"] == []
    assert result["source_exact_pair_candidate_count"] == 0
    assert result["candidate_crosswalk_applied"] is False


def test_duplicate_same_exact_source_pair_is_one_candidate():
    roster = pd.DataFrame([
        {"pfr_id": "WhitCo05", "gsis_id": "00-0032222", "season": "2025",
         "position": "WR", "team": "PIT"},
        {"pfr_id": "WhitCo05", "gsis_id": "00-0032222", "season": "2025",
         "position": "WR", "team": "PIT"},
    ])
    result = mod.inspect_roster_id_candidates(roster, ["WhitCo05"])
    assert result["source_exact_pair_candidate_count"] == 1
    assert result["targets"][0]["source_rows"] == 2
    assert result["candidate_crosswalk_applied"] is False


def test_bad_target_identity_rejected():
    roster = pd.DataFrame(columns=mod.CROSSWALK_FIELDS)
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_TARGET_INVALID"):
        mod.inspect_roster_id_candidates(roster, [" CartNa00 "])


def test_weekly_roster_published_pin_and_review_only_id(monkeypatch):
    frame = pd.DataFrame([{
        "pfr_id": "CartNa00", "gsis_id": "00-0099999", "season": 2025,
        "week": 2, "game_type": "REG", "position": "RB", "team": "ATL",
    }])
    data = frame.to_csv(index=False).encode()
    monkeypatch.setattr(mod, "WEEKLY_ROSTER_2025_SIZE", len(data))
    monkeypatch.setattr(
        mod, "WEEKLY_ROSTER_2025_SHA256", hashlib.sha256(data).hexdigest(),
    )
    read, receipt = mod.read_pinned_2025_roster(
        weekly=True, open_url=lambda *a, **kw: FakeResponse(data),
    )
    assert receipt["asset"] == mod.WEEKLY_ROSTER_2025_PATH
    assert receipt["bytes_sha256_verified"] == len(data)
    result = mod.inspect_roster_id_candidates(read, ["CartNa00"])
    assert result["source_exact_pair_candidate_count"] == 1
    assert result["source_both_exact_id_rows"] == 1
    assert result["candidate_crosswalk_applied"] is False


def test_weekly_asset_rejects_wrong_checksum_even_with_valid_schema(monkeypatch):
    frame = pd.DataFrame([{
        "pfr_id": "CartNa00", "gsis_id": "00-0099999", "season": 2025,
        "week": 2, "game_type": "REG", "position": "RB", "team": "ATL",
    }])
    data = frame.to_csv(index=False).encode()
    monkeypatch.setattr(mod, "WEEKLY_ROSTER_2025_SIZE", len(data))
    monkeypatch.setattr(mod, "WEEKLY_ROSTER_2025_SHA256", "f" * 64)
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_DIGEST_MISMATCH"):
        mod.read_pinned_2025_roster(
            weekly=True, open_url=lambda *a, **kw: FakeResponse(data),
        )


def test_roster_source_column_coverage_does_not_prove_nonparticipation():
    frame = pd.DataFrame([{
        "pfr_id": None, "gsis_id": "00-0099999", "season": 2025,
        "position": "RB", "team": "ATL",
    }])
    result = mod.inspect_roster_id_candidates(frame, ["CartNa00"])
    assert result["source_rows_total"] == 1
    assert result["source_nonblank_pfr_rows"] == 0
    assert result["source_valid_gsis_rows"] == 1
    assert result["source_exact_pair_candidate_count"] == 0
    assert result["can_execute"] is False


@pytest.mark.parametrize("season", ["2024", None, "2026"])
def test_direct_roster_inspector_rejects_wrong_or_null_season(season):
    """Even direct callers cannot launder a non-2025 source as 2025."""
    roster = pd.DataFrame([{
        "pfr_id": "CartNa00", "gsis_id": "00-0099999",
        "season": season, "position": "RB", "team": "ATL",
    }])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.inspect_roster_id_candidates(roster, ["CartNa00"])
