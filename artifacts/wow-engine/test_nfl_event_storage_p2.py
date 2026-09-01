import hashlib
from pathlib import Path

import pytest

from nfl_event_storage_p2 import preserve_exact_bytes


class _Bucket:
    def __init__(self):
        self.objects = {}
    def upload(self, *, path, file, file_options):
        if path in self.objects:
            raise RuntimeError("409 already exists")
        self.objects[path] = bytes(file)


class _Storage:
    def __init__(self):
        self.bucket = _Bucket()
    def from_(self, name):
        return self.bucket


class _Client:
    def __init__(self):
        self.storage = _Storage()


def test_preserves_exact_bytes_in_ordered_small_chunks(tmp_path: Path):
    raw = (b"abcdef0123456789" * 900000) + b"tail"
    source = tmp_path / "pbp.csv"
    source.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    client = _Client()
    uri = preserve_exact_bytes(
        client, source, dataset_name="PLAY_BY_PLAY", season=2025,
        expected_sha256=sha, chunk_bytes=1024 * 1024,
    )
    assert uri.startswith("supabase://wow-nfl-model-evidence/")
    part_paths = sorted(p for p in client.storage.bucket.objects if "/part-" in p)
    rebuilt = b"".join(client.storage.bucket.objects[p] for p in part_paths)
    assert rebuilt == raw
    assert all(len(client.storage.bucket.objects[p]) <= 1024 * 1024 for p in part_paths)
    assert any("/manifest-" in p for p in client.storage.bucket.objects)


def test_sha_mismatch_fails_closed(tmp_path: Path):
    source = tmp_path / "x.csv"
    source.write_bytes(b"hello")
    with pytest.raises(ValueError, match="SHA-256"):
        preserve_exact_bytes(
            _Client(), source, dataset_name="SCHEDULES", season=None,
            expected_sha256="0" * 64, chunk_bytes=2,
        )


def test_chunk_size_cannot_exceed_standard_upload_safety_limit(tmp_path: Path):
    source = tmp_path / "x.csv"
    source.write_bytes(b"hello")
    sha = hashlib.sha256(b"hello").hexdigest()
    with pytest.raises(ValueError):
        preserve_exact_bytes(
            _Client(), source, dataset_name="SCHEDULES", season=None,
            expected_sha256=sha, chunk_bytes=5 * 1024 * 1024 + 1,
        )
