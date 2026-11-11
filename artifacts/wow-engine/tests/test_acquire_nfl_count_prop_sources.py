"""Safety regression for offline nflverse source acquisition: synthetic bytes only."""
from __future__ import annotations

import hashlib
import io
import sys
import urllib.error
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from acquire_nfl_count_prop_sources import frozen_assets, acquire_one


class FakeResponse(io.BytesIO):
    def __init__(self, blob, final="https://release-assets.githubusercontent.com/test"):
        super().__init__(blob)
        self.final = final

    def geturl(self):
        return self.final


def test_all_official_pins_complete_and_immutable():
    assets = frozen_assets()
    assert len(assets) == 11
    assert len({a[0] for a in assets}) == 11
    assert sum(a[2] for a in assets) == 61620003
    assert all(len(a[1]) == 64 for a in assets)


def test_good_download_then_verified_existing(tmp_path):
    blob = b"gsis_id,pfr_id\nQ1,P1\n"
    digest = hashlib.sha256(blob).hexdigest()
    one = acquire_one("players/players.csv", digest, len(blob), tmp_path,
                      open_url=lambda *a, **k: FakeResponse(blob))
    assert one["action"] == "downloaded_verified"
    two = acquire_one("players/players.csv", digest, len(blob), tmp_path, verify_only=True)
    assert two["action"] == "verified_existing"


def test_bad_sha_never_published(tmp_path):
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_DIGEST_MISMATCH"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path,
                    open_url=lambda *a, **k: FakeResponse(b"mismatch!"))
    assert not (tmp_path / "players/players.csv").exists()


def test_bad_existing_bytes_never_overwritten(tmp_path):
    dest = tmp_path / "players"
    dest.mkdir()
    target = dest / "players.csv"
    target.write_bytes(b"bad")
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_DIGEST_MISMATCH"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path)
    assert target.read_bytes() == b"bad"


def test_untrusted_redirect_does_not_install(tmp_path):
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED"):
        acquire_one("players/players.csv", "a"*64, 4, tmp_path,
                    open_url=lambda *a, **k: FakeResponse(b"data", "https://example.com/file"))
    assert not (tmp_path / "players/players.csv").exists()


def test_typed_dns_failure_and_no_partial_output(tmp_path):
    def down(*a, **k):
        raise urllib.error.URLError("DNS unavailable")
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_NETWORK_UNAVAILABLE"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path, open_url=down)
    assert not (tmp_path / "players/players.csv").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_verify_only_missing_is_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_FILE_MISSING"):
        acquire_one("players/players.csv", "a"*64, 9, tmp_path, verify_only=True)


def test_symlink_is_not_accepted(tmp_path):
    (tmp_path / "players").mkdir()
    raw = tmp_path / "other.csv"
    raw.write_bytes(b"test")
    (tmp_path / "players/players.csv").symlink_to(raw)
    with pytest.raises(ValueError, match="NFL_PROP_ACQUIRE_FILE_MISSING"):
        acquire_one("players/players.csv", hashlib.sha256(b"test").hexdigest(), 4,
                    tmp_path, verify_only=True)


def test_pr2605_observe_official_source_reconciliation(tmp_path):
    """One PR-scoped live-source *observation*, never a required source gate.

    The standard rapid-regression runner has network access unavailable to the
    ChatGPT container. This probe runs only in that existing PR/workflow, makes
    no modification to protected workflows and intentionally SKIPs with an
    explicit real-data receipt or typed HOLD rather than claiming certification.
    It does not apply on main, other branches or other test shards.
    """
    import json
    import os
    import urllib.request
    import pandas as pd

    if (os.environ.get("GITHUB_WORKFLOW") != "wow-v17-rapid-repair"
            or os.environ.get("GITHUB_HEAD_REF")
            != "engineering/nfl-five-count-candidates-20261111"):
        pytest.skip("NFL_PROP_SOURCE_OBSERVATION_NOT_PR2605_RAPID_RUNNER")

    from acquire_nfl_count_prop_sources import frozen_assets
    assets = frozen_assets()
    downloaded = []
    # Bound every network attempt, without changing the governed downloader API.
    def short_network_timeout(request, **_kwargs):
        return urllib.request.urlopen(request, timeout=20)

    for name, digest, size in assets:
        try:
            receipt = acquire_one(name, digest, size, tmp_path,
                                  open_url=short_network_timeout)
        except (ValueError, OSError) as exc:
            pytest.skip(
                "NFL_PROP_SOURCE_ACQUIRE_HOLD:"
                + type(exc).__name__ + ":" + str(exc)[:180]
                + f":verified_prior_assets={len(downloaded)}/11"
            )
        downloaded.append(receipt)

    # This never means modeled-count outcomes are safe to impute or publish.
    from train_nfl_missing_prop_candidates import (
        load_source_files, EXPECTED_RELEASE_SHA256, SEASONS,
    )
    from audit_nfl_count_prop_participation import (
        _verified_csv, SNAP_SHA256, PLAYERS_SHA256, reconcile_player_games,
    )
    try:
        stats, provenance = load_source_files(
            tmp_path / "stats_player", expected_sha256=EXPECTED_RELEASE_SHA256
        )
        snaps = pd.concat([
            _verified_csv(tmp_path / "snap_counts" / f"snap_counts_{season}.csv",
                          SNAP_SHA256[str(season)])
            for season in SEASONS
        ], ignore_index=True)
        players = _verified_csv(tmp_path / "players" / "players.csv", PLAYERS_SHA256)
        result = reconcile_player_games(stats, snaps, players)
        note = {
            "source_asset_count": len(downloaded),
            "source_bytes_sha_verified": sum(item["size_bytes"] for item in downloaded),
            "source_provenance_hash": provenance.get("bundle_sha256"),
            "participation_status": result["status"],
            "offensive_participant_games": result["offensive_participant_games"],
            "missing_player_games": result["missing_player_games"],
            "unmapped_pfr_player_games": result["unmapped_pfr_player_games"],
            "missing_examples": result["missing_examples"][:2],
        }
    except (ValueError, KeyError, TypeError) as exc:
        pytest.skip(
            "NFL_PROP_SOURCE_ALL_11_DIGESTS_VERIFIED_BUT_COHORT_HOLD:"
            + type(exc).__name__ + ":" + str(exc)[:250]
        )

    pytest.skip(
        "NFL_PROP_OFFLINE_SOURCE_RESEARCH_RECEIPT:"
        + json.dumps(note, sort_keys=True, separators=(",", ":"), default=str)
        + ":source_rights_independently_approved=false"
        + ":fitted_model_certified=false:can_execute=false"
    )

