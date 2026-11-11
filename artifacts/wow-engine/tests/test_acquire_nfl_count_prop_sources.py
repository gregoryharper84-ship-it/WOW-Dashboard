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
