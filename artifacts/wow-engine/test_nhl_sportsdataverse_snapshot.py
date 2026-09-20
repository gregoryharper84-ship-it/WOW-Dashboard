from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import nhl_sportsdataverse_snapshot as snap
from historical_data_backbone import SourceRightsState, load_source_manifest
from v17.model_source_entitlements import SOURCES, source_readiness


def test_pinned_snapshot_contains_three_direct_identity_seasons_and_nine_assets():
    assets = snap.pinned_assets()
    assert snap.PINNED_SEASONS == (2024, 2025, 2026)
    assert snap.QUARANTINED_SCHEMA_SEASONS == (2022, 2023)
    assert len(assets) == 9
    assert {asset.season for asset in assets} == set(snap.PINNED_SEASONS)
    assert all(sum(1 for row in assets if row.season == season) == 3 for season in snap.PINNED_SEASONS)
    assert {asset.kind for asset in assets} == {"PLAYER_BOXSCORES", "GAME_INFO", "SCHEDULE"}
    assert not (set(snap.PINNED_SEASONS) & set(snap.QUARANTINED_SCHEMA_SEASONS))


def test_2026_assets_are_explicitly_pinned_by_release_digest():
    by_name = {asset.filename: asset for asset in snap.pinned_assets()}
    assert by_name["player_box_2026.csv"].sha256 == "41a35357d65e0d51967568ca9d0d16dae0dba593bf0193372f6cd14e5a46b102"
    assert by_name["game_info_2026.csv"].sha256 == "15bfbde574d9b84f07ffc387460127cb66b0173a06c6812132fbc718b524b610"
    assert by_name["nhl_schedule_2026.csv"].sha256 == "51bf53885ad4807ae9cb65b96a09715473ae5f466c834269d4df521b8debef4c"


def test_schedule_assets_are_pinned_for_each_replay_season():
    expected = {
        2024: "9292b99f8d5daf362a9e7316f3146fd677e5bafe99063f6fb759baa9383f8d07",
        2025: "017f89619c13857e8b7f2f52ccbf7c1ea20fcef46f5bc90de719ea12475d1e00",
        2026: "51bf53885ad4807ae9cb65b96a09715473ae5f466c834269d4df521b8debef4c",
    }
    schedule_assets = [asset for asset in snap.pinned_assets() if asset.kind == "SCHEDULE"]
    assert {asset.season: asset.sha256 for asset in schedule_assets} == expected
    assert all(f"/{snap.SCHEDULE_TAG}/" in asset.url for asset in schedule_assets)


def test_all_pinned_assets_use_expected_github_release_origin_and_sha256():
    for asset in snap.pinned_assets():
        assert asset.url.startswith(
            "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
        assert asset.filename.endswith(f"_{asset.season}.csv")


def test_snapshot_manifest_is_deterministic_and_governance_locked():
    left = snap.snapshot_manifest(retrieved_at="2026-09-20T10:00:00+00:00")
    right = snap.snapshot_manifest(retrieved_at="2026-09-20T10:00:00+00:00")
    assert left == right
    assert left["manifest_sha256"] == right["manifest_sha256"]
    assert left["source_id"] == "SPORTSDATAVERSE_NHL"
    assert left["source_review_status"] == "REQUIRED"
    assert left["source_review_required"] is True
    assert left["research_only"] is True
    assert left["probability_publishable"] is False
    assert left["can_execute"] is False
    assert left["seasons"] == [2024, 2025, 2026]
    assert left["quarantined_schema_seasons"] == [2022, 2023]
    assert len(left["assets"]) == 9


def test_snapshot_manifest_rejects_incomplete_asset_set():
    with pytest.raises(snap.NHLSportsDataverseSnapshotError) as exc:
        snap.snapshot_manifest(
            retrieved_at="2026-09-20T10:00:00+00:00",
            verified_assets=snap.pinned_assets()[:-1],
        )
    assert exc.value.code == "NHL_SDV_SNAPSHOT_INCOMPLETE"


def test_asset_digest_mismatch_fails_closed(tmp_path: Path):
    asset = snap.pinned_assets()[0]
    path = tmp_path / asset.filename
    path.write_bytes(b"not-the-pinned-asset")
    with pytest.raises(snap.NHLSportsDataverseSnapshotError) as exc:
        snap.verify_asset_file(path, asset)
    assert exc.value.code == "NHL_SDV_ASSET_DIGEST_MISMATCH"


def test_quarantined_legacy_player_box_filename_is_not_pinned(tmp_path: Path):
    path = tmp_path / "player_box_2023.csv"
    path.write_text("x\n", encoding="utf-8")
    with pytest.raises(snap.NHLSportsDataverseSnapshotError) as exc:
        snap.verify_asset_file(path)
    assert exc.value.code == "NHL_SDV_ASSET_NOT_PINNED"


def test_unpinned_future_filename_fails_closed(tmp_path: Path):
    path = tmp_path / "player_box_2027.csv"
    path.write_text("x\n", encoding="utf-8")
    with pytest.raises(snap.NHLSportsDataverseSnapshotError) as exc:
        snap.verify_asset_file(path)
    assert exc.value.code == "NHL_SDV_ASSET_NOT_PINNED"


def test_entitlement_is_candidate_source_review_pending_not_open_licensed():
    source = SOURCES["SPORTSDATAVERSE_NHL"]
    readiness = source_readiness("SPORTSDATAVERSE_NHL")
    assert source.sports == ("NHL",)
    assert source.use == "CANDIDATE_FIRST_PARTY_UNDOCUMENTED"
    assert source.use != "TRAINING_OPEN_LICENSED"
    assert source.license_id is None
    assert source.license_url is None
    assert source.fitted_training_allowed_when_ready is True
    assert source.certification_source_review_required is True
    assert source.probability_source is False
    assert source.market_feature_allowed is False
    assert source.can_execute is False
    assert readiness.ready_for_candidate_training is True
    assert readiness.can_execute is False


def test_historical_manifest_keeps_sportsdataverse_nhl_license_review_required():
    manifest_path = Path(__file__).with_name("historical_source_manifest_v1.json")
    entries = load_source_manifest(manifest_path)
    matches = [
        entry
        for entry in entries
        if entry.sport == "NHL" and entry.provider == "SPORTSDATAVERSE_NHL"
    ]
    assert len(matches) == 1
    entry = matches[0]
    assert entry.rights_state is SourceRightsState.LICENSE_REVIEW_REQUIRED
    assert entry.production_training_eligible is False
    assert entry.grants_model_capability is False
    assert entry.can_execute is False


def test_historical_manifest_does_not_claim_cc_by_for_sportsdataverse_nhl():
    raw = json.loads(Path(__file__).with_name("historical_source_manifest_v1.json").read_text())
    row = next(
        item
        for item in raw["sources"]
        if item["sport"] == "NHL" and item["provider"] == "SPORTSDATAVERSE_NHL"
    )
    serialized = json.dumps(row, sort_keys=True).upper()
    assert "CC-BY" not in serialized
    assert "CC BY" not in serialized
    assert row["rights_state"] == "LICENSE_REVIEW_REQUIRED"
    assert row["grants_model_capability"] is False


def test_governance_constants_are_fail_closed():
    assert snap.SOURCE_REVIEW_REQUIRED is True
    assert snap.SOURCE_REVIEW_STATUS == "REQUIRED"
    assert snap.RESEARCH_ONLY is True
    assert snap.PROBABILITY_PUBLISHABLE is False
    assert snap.CAN_EXECUTE is False
