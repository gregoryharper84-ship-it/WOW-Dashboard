"""Pinned raw-corpus contract for the NHL SOG SportsDataverse research lane.

This module freezes source identity and bytes only. It does not parse the CSV
schema, build features, fit a model, publish probability, or grant source/model
certification. `SPORTSDATAVERSE_NHL` remains source-review-pending.

can_execute=false unconditionally.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

SOURCE_ID = "SPORTSDATAVERSE_NHL"
SOURCE_REPOSITORY = "sportsdataverse/sportsdataverse-data"
SOURCE_REVIEW_STATUS = "REQUIRED"
SOURCE_REVIEW_REQUIRED = True
RESEARCH_ONLY = True
PROBABILITY_PUBLISHABLE = False
CAN_EXECUTE = False

RELEASE_BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
PLAYER_TAG = "nhl_player_boxscores"
GAME_TAG = "nhl_game_info"
PINNED_SEASONS = (2022, 2023, 2024, 2025)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class NHLSportsDataverseSnapshotError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


@dataclass(frozen=True, order=True)
class PinnedAsset:
    kind: str
    season: int
    filename: str
    url: str
    sha256: str

    def __post_init__(self) -> None:
        if self.kind not in {"PLAYER_BOXSCORES", "GAME_INFO"}:
            raise NHLSportsDataverseSnapshotError("NHL_SDV_ASSET_KIND_INVALID", self.kind)
        if self.season not in PINNED_SEASONS:
            raise NHLSportsDataverseSnapshotError("NHL_SDV_SEASON_OUTSIDE_PIN", str(self.season))
        if not self.url.startswith(RELEASE_BASE + "/"):
            raise NHLSportsDataverseSnapshotError("NHL_SDV_ASSET_URL_UNTRUSTED", self.url)
        if not _SHA256_RE.fullmatch(self.sha256):
            raise NHLSportsDataverseSnapshotError("NHL_SDV_ASSET_DIGEST_INVALID", self.sha256)
        expected_prefix = "player_box_" if self.kind == "PLAYER_BOXSCORES" else "game_info_"
        expected_name = f"{expected_prefix}{self.season}.csv"
        if self.filename != expected_name or not self.url.endswith("/" + expected_name):
            raise NHLSportsDataverseSnapshotError(
                "NHL_SDV_ASSET_IDENTITY_MISMATCH", f"{self.filename}:{self.url}"
            )


PINNED_ASSETS: tuple[PinnedAsset, ...] = (
    PinnedAsset(
        "PLAYER_BOXSCORES",
        2022,
        "player_box_2022.csv",
        f"{RELEASE_BASE}/{PLAYER_TAG}/player_box_2022.csv",
        "60c457cfe62c125929861367816c4eb243ebf17574cd35ad9982318a1e484e4d",
    ),
    PinnedAsset(
        "PLAYER_BOXSCORES",
        2023,
        "player_box_2023.csv",
        f"{RELEASE_BASE}/{PLAYER_TAG}/player_box_2023.csv",
        "4f823eb8146a03becdaaf220128f68729528443c08a18852e54aae3f3833dd44",
    ),
    PinnedAsset(
        "PLAYER_BOXSCORES",
        2024,
        "player_box_2024.csv",
        f"{RELEASE_BASE}/{PLAYER_TAG}/player_box_2024.csv",
        "889d439dae5b5a2e831496a3a0dcbea4d385883d68d55b70d55a554169ff6e74",
    ),
    PinnedAsset(
        "PLAYER_BOXSCORES",
        2025,
        "player_box_2025.csv",
        f"{RELEASE_BASE}/{PLAYER_TAG}/player_box_2025.csv",
        "511f58b09996be6165c7ad2a0f475ac029f0206653ce4e11665e1ff8088516b0",
    ),
    PinnedAsset(
        "GAME_INFO",
        2022,
        "game_info_2022.csv",
        f"{RELEASE_BASE}/{GAME_TAG}/game_info_2022.csv",
        "752fb3b3406c9f14b91d76d66fb6d1efd78eb17e94a6261b9931da484e632786",
    ),
    PinnedAsset(
        "GAME_INFO",
        2023,
        "game_info_2023.csv",
        f"{RELEASE_BASE}/{GAME_TAG}/game_info_2023.csv",
        "cd2746413a4eaf8819140c894b5297d57098a4736ec52aa5b7d9e17f58351fcc",
    ),
    PinnedAsset(
        "GAME_INFO",
        2024,
        "game_info_2024.csv",
        f"{RELEASE_BASE}/{GAME_TAG}/game_info_2024.csv",
        "03f87329a2113ddaf4b8f31b213952efc49d85d413155ec67b00d8b6c4043994",
    ),
    PinnedAsset(
        "GAME_INFO",
        2025,
        "game_info_2025.csv",
        f"{RELEASE_BASE}/{GAME_TAG}/game_info_2025.csv",
        "e743bafe13dfa761f3ac84ab978b8e7de73ea7f126f2daf4f27e437017f3212b",
    ),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pinned_assets() -> tuple[PinnedAsset, ...]:
    return tuple(sorted(PINNED_ASSETS, key=lambda row: (row.season, row.kind, row.filename)))


def asset_by_filename(filename: str) -> PinnedAsset:
    matches = [asset for asset in PINNED_ASSETS if asset.filename == filename]
    if len(matches) != 1:
        raise NHLSportsDataverseSnapshotError("NHL_SDV_ASSET_NOT_PINNED", filename)
    return matches[0]


def verify_asset_file(path: str | Path, asset: PinnedAsset | None = None) -> str:
    candidate = Path(path)
    if not candidate.is_file():
        raise NHLSportsDataverseSnapshotError("NHL_SDV_ASSET_MISSING", str(candidate))
    asset = asset or asset_by_filename(candidate.name)
    if candidate.name != asset.filename:
        raise NHLSportsDataverseSnapshotError(
            "NHL_SDV_ASSET_FILENAME_MISMATCH", f"{candidate.name}:{asset.filename}"
        )
    actual = _sha256_file(candidate)
    if actual != asset.sha256:
        raise NHLSportsDataverseSnapshotError(
            "NHL_SDV_ASSET_DIGEST_MISMATCH", f"{asset.filename}:{actual}:{asset.sha256}"
        )
    return actual


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def snapshot_manifest(
    *,
    retrieved_at: str,
    verified_assets: Iterable[PinnedAsset] | None = None,
) -> dict[str, object]:
    assets = tuple(sorted(verified_assets or pinned_assets(), key=lambda row: (row.season, row.kind, row.filename)))
    if set(assets) != set(PINNED_ASSETS):
        raise NHLSportsDataverseSnapshotError(
            "NHL_SDV_SNAPSHOT_INCOMPLETE", f"expected={len(PINNED_ASSETS)} actual={len(assets)}"
        )
    base: dict[str, object] = {
        "schema_version": "WOW_NHL_SDV_RAW_SNAPSHOT_V1",
        "source_id": SOURCE_ID,
        "source_repository": SOURCE_REPOSITORY,
        "source_review_status": SOURCE_REVIEW_STATUS,
        "source_review_required": SOURCE_REVIEW_REQUIRED,
        "research_only": RESEARCH_ONLY,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
        "retrieved_at": retrieved_at,
        "seasons": list(PINNED_SEASONS),
        "assets": [asdict(asset) for asset in assets],
    }
    base["manifest_sha256"] = hashlib.sha256(_canonical_json(base)).hexdigest()
    return base


def verify_snapshot_directory(directory: str | Path) -> dict[str, str]:
    root = Path(directory)
    verified: dict[str, str] = {}
    for asset in pinned_assets():
        verified[asset.filename] = verify_asset_file(root / asset.filename, asset)
    return verified


__all__ = [
    "CAN_EXECUTE",
    "GAME_TAG",
    "NHLSportsDataverseSnapshotError",
    "PINNED_ASSETS",
    "PINNED_SEASONS",
    "PLAYER_TAG",
    "PROBABILITY_PUBLISHABLE",
    "PinnedAsset",
    "RELEASE_BASE",
    "RESEARCH_ONLY",
    "SOURCE_ID",
    "SOURCE_REPOSITORY",
    "SOURCE_REVIEW_REQUIRED",
    "SOURCE_REVIEW_STATUS",
    "asset_by_filename",
    "pinned_assets",
    "snapshot_manifest",
    "verify_asset_file",
    "verify_snapshot_directory",
]
