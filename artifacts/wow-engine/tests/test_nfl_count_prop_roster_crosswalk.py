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


def test_unpublished_historical_roster_is_always_research_hold(monkeypatch):
    frame = pd.DataFrame([{
        "pfr_id": "WhitCo05", "gsis_id": "00-0033333", "season": 2021,
        "position": "WR", "team": "PIT",
    }])
    data = frame.to_csv(index=False).encode()
    monkeypatch.setitem(mod.HISTORICAL_ROSTER_SIZES, 2021, len(data))
    downloads = []
    def fake(*args, **kwargs):
        downloads.append(1)
        return FakeResponse(data)
    result, receipt = mod.read_two_copy_historical_roster(
        2021, open_url=fake,
    )
    assert len(downloads) == 2
    assert receipt["release_digest_published"] is False
    assert receipt["status"] == "HOLD_HISTORICAL_ROSTER_UNPUBLISHED_DIGEST"
    assert receipt["identical_read_count"] == 2
    candidates = mod.inspect_roster_id_candidates(
        result, ["WhitCo05"], expected_season=2021,
    )
    assert candidates["targets"][0]["gsis_candidates"] == ["00-0033333"]
    assert candidates["candidate_crosswalk_applied"] is False


def test_historical_roster_same_size_changed_bytes_fail_closed(monkeypatch):
    frame = pd.DataFrame([{
        "pfr_id": "WhitCo05", "gsis_id": "00-0033333", "season": 2021,
        "position": "WR", "team": "PIT",
    }])
    data = frame.to_csv(index=False).encode()
    changed = data.replace(b"00-0033333", b"00-0044444")
    assert len(data) == len(changed)
    monkeypatch.setitem(mod.HISTORICAL_ROSTER_SIZES, 2021, len(data))
    samples = iter([data, changed])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_TWO_READ_DRIFT"):
        mod.read_two_copy_historical_roster(
            2021, open_url=lambda *a, **k: FakeResponse(next(samples)),
        )


def test_historical_roster_wrong_size_or_unknown_year_rejected(monkeypatch):
    monkeypatch.setitem(mod.HISTORICAL_ROSTER_SIZES, 2021, 10)
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SOURCE_SIZE_MISMATCH"):
        mod.read_two_copy_historical_roster(
            2021, open_url=lambda *a, **k: FakeResponse(b"too short"),
        )
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_YEAR_UNPINNED"):
        mod.read_two_copy_historical_roster(2020)


def test_historical_roster_direct_inspector_rejects_wrong_season():
    frame = pd.DataFrame([{
        "pfr_id": "WhitCo05", "gsis_id": "00-0033333", "season": 2021,
        "position": "WR", "team": "PIT",
    }])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.inspect_roster_id_candidates(
            frame, ["WhitCo05"], expected_season=2024,
        )


def test_manual_overrides_do_not_establish_absence_for_four_unmapped_ids():
    targets = ["CartNa00", "ShenJo00", "WhitCo05", "WillRo08"]
    result = mod.inspect_manual_pfr_overrides(
        [{"gsis_id": "00-0012345", "pfr_id": "Other00"},
         {"gsis_id": "00-0088888", "espn_id": "0001"}],
        targets,
    )
    assert result["target_pfr_count"] == 4
    assert result["target_exact_override_count"] == 0
    assert all(not x["gsis_candidates"] for x in result["targets"])
    assert result["manual_overrides_are_not_full_player_universe"] is True
    assert result["no_exact_override_does_not_prove_no_player"] is True
    assert result["status"] == "HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW"
    assert result["candidate_crosswalk_applied"] is False
    assert result["source_rights_independently_approved"] is False
    assert result["fitted_specialist_certified"] is False
    assert result["can_execute"] is False


def test_manual_override_exact_id_remains_review_candidate_only():
    records = [{"pfr_id": "WhitCo05", "gsis_id": "00-0044444"},
               {"pfr_id": "WhitCo05", "gsis_id": "00-0044444"}]
    result = mod.inspect_manual_pfr_overrides(records, ["WhitCo05"])
    assert result["target_exact_override_count"] == 1
    assert result["targets"][0]["manual_override_rows"] == 2
    assert result["targets"][0]["gsis_candidates"] == ["00-0044444"]
    assert result["candidate_crosswalk_applied"] is False
    assert result["can_execute"] is False


def test_manual_override_conflicting_pair_fails_closed():
    records = [{"pfr_id": "CartNa00", "gsis_id": "00-0001111"},
               {"pfr_id": "CartNa00", "gsis_id": "00-0002222"}]
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_CROSSWALK_CONFLICT"):
        mod.inspect_manual_pfr_overrides(records, ["CartNa00"])


@pytest.mark.parametrize("invalid", [None, "", "abc", 123])
def test_manual_override_invalid_target_rejected(invalid):
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_TARGET_INVALID"):
        mod.inspect_manual_pfr_overrides([], [invalid])


@pytest.mark.parametrize("invalid", [None, "", 17, "bad-value", []])
def test_manual_override_target_gsis_requires_exact_source_identity(invalid):
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.inspect_manual_pfr_overrides(
            [{"pfr_id": "ShenJo00", "gsis_id": invalid}], ["ShenJo00"],
        )


def test_manual_override_unhashable_foreign_pfr_is_typed_schema_hold():
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.inspect_manual_pfr_overrides(
            [{"pfr_id": ["CartNa00"], "gsis_id": "00-0012345"}], ["CartNa00"],
        )


def test_manual_override_nondict_record_is_rejected():
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.inspect_manual_pfr_overrides([None], ["CartNa00"])


def test_two_distinct_pfr_aliases_to_same_gsis_fail_closed():
    """One GSIS player must not be admitted under two different PFR codes."""
    roster = pd.DataFrame([
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333", "season": "2025",
         "position": "RB", "team": "ATL"},
        {"pfr_id": "WhitCo05", "gsis_id": "00-0033333", "season": "2025",
         "position": "WR", "team": "ATL"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_CROSSWALK_CONFLICT"):
        mod.inspect_roster_id_candidates(roster, ["CartNa00", "WhitCo05"])


def test_candidate_gsis_collision_with_unrequested_pfr_also_fails_closed():
    """Unrequested source aliases must not make a target look unambiguous."""
    roster = pd.DataFrame([
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333", "season": "2025",
         "position": "RB", "team": "ATL"},
        {"pfr_id": "Other000", "gsis_id": "00-0033333", "season": "2025",
         "position": "RB", "team": "ATL"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_CROSSWALK_CONFLICT"):
        mod.inspect_roster_id_candidates(roster, ["CartNa00"])


def _synthetic_archived_players_rds(tmp_path, monkeypatch, rows):
    payload = b"synthetic-rds-bytes-for-test-only"
    path = tmp_path / "players.rds"
    path.write_bytes(payload)
    from copy import deepcopy
    pins = deepcopy(mod.ARCHIVE_OBSERVATION)
    pins["assets"]["players.rds"] = (
        714885750, len(payload), hashlib.sha256(payload).hexdigest()
    )
    monkeypatch.setattr(mod, "ARCHIVE_OBSERVATION", pins)
    frame = pd.DataFrame(rows)

    def parser(snapshot_path):
        assert Path(snapshot_path).name == "players.rds"
        assert Path(snapshot_path).read_bytes() == payload
        assert Path(snapshot_path) != path
        return {None: frame}

    return path, parser


def test_archived_players_rds_exact_candidates_are_only_for_independent_review(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333"},
        {"pfr_id": "Other000", "gsis_id": "00-0044444"},
        {"pfr_id": None, "gsis_id": "00-0055555"},
    ])
    receipt = mod.inspect_archived_players_rds(
        path, ["CartNa00", "ShenJo00", "WhitCo05", "WillRo08"], read_rds=parser
    )
    assert receipt["status"] == "HOLD_ARCHIVED_IDENTITY_INDEPENDENT_REVIEW"
    assert receipt["archive_tag"] == "archive-2026-11-05"
    assert receipt["source_bytes_sha256_verified"] is True
    assert receipt["target_pfr_count"] == 4
    assert receipt["target_exact_candidate_count"] == 1
    assert receipt["targets"][0]["gsis_candidates"] == ["00-0033333"]
    assert all(not t["gsis_candidates"] for t in receipt["targets"][1:])
    assert receipt["source_absence_does_not_prove_player_absence"] is True
    assert receipt["crosswalk_applied"] is False
    assert receipt["independent_identity_accepted"] is False
    assert receipt["source_rights_independently_approved"] is False
    assert receipt["training_authorized"] is False
    assert receipt["probability_publishable"] is False
    assert receipt["can_execute"] is False


def test_archived_players_rds_rejects_mutated_hash_before_parser(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333"},
    ])
    path.write_bytes(b"unverified-different-bytes")
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_DIGEST_MISMATCH"):
        mod.inspect_archived_players_rds(path, ["CartNa00"], read_rds=lambda _: pytest.fail("unverified parser executed"))


def test_archived_players_rds_rejects_missing_required_id_column(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "wrong_id": "00-0033333"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.inspect_archived_players_rds(path, ["CartNa00"], read_rds=parser)


def test_archived_players_rds_rejects_gsis_alias_outside_requested_ids(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333"},
        {"pfr_id": "Other000", "gsis_id": "00-0033333"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_CROSSWALK_CONFLICT"):
        mod.inspect_archived_players_rds(path, ["CartNa00"], read_rds=parser)


def test_archived_players_rds_rejects_one_pfr_with_two_gsis(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333"},
        {"pfr_id": "CartNa00", "gsis_id": "00-0099999"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_CROSSWALK_CONFLICT"):
        mod.inspect_archived_players_rds(path, ["CartNa00"], read_rds=parser)


def test_archived_players_rds_rejects_target_with_invalid_gsis(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "invalid"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_SCHEMA_INVALID"):
        mod.inspect_archived_players_rds(path, ["CartNa00"], read_rds=parser)


def test_archived_players_rds_rejects_symlink(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333"},
    ])
    alias = tmp_path / "alias.rds"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_FILE_MISSING"):
        mod.inspect_archived_players_rds(alias, ["CartNa00"], read_rds=parser)


def test_archived_players_rds_rejects_unavailable_reader(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333"},
    ])
    import importlib
    def no_reader(name):
        assert name == "pyreadr"
        raise ModuleNotFoundError("no research-only RDS parser")
    monkeypatch.setattr(importlib, "import_module", no_reader)
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_RDS_READER_UNAVAILABLE"):
        mod.inspect_archived_players_rds(path, ["CartNa00"])


def test_archived_players_rds_rejects_untrusted_target_value(tmp_path, monkeypatch):
    path, parser = _synthetic_archived_players_rds(tmp_path, monkeypatch, [
        {"pfr_id": "CartNa00", "gsis_id": "00-0033333"},
    ])
    with pytest.raises(ValueError, match="NFL_PROP_ROSTER_TARGET_INVALID"):
        mod.inspect_archived_players_rds(path, ["CartNa00", "../other"], read_rds=parser)
