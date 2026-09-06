"""Immutable NFL source preservation for P2.

Large historical CSVs are split into small, content-addressed objects plus a
manifest so the exact original byte stream is reconstructable. This avoids
pretending a large one-shot standard upload is reliable while requiring no
betting/model authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_BUCKET = "wow-nfl-model-evidence"
CHUNK_BYTES = 5 * 1024 * 1024


def ensure_private_bucket(client: Any, bucket: str = DEFAULT_BUCKET) -> None:
    try:
        client.storage.get_bucket(bucket)
    except Exception:
        client.storage.create_bucket(bucket, options={"public": False})


def _upload_immutable(bucket_api: Any, path: str, payload: bytes, *, content_type: str) -> None:
    try:
        bucket_api.upload(
            path=path,
            file=payload,
            file_options={"content-type": content_type, "upsert": "false"},
        )
    except Exception as exc:
        # Immutable content-addressed paths may already exist on a retry. Do not
        # overwrite. Caller can verify the manifest/object independently.
        text = str(exc).lower()
        if "already exists" not in text and "duplicate" not in text and "409" not in text:
            raise


def preserve_exact_bytes(
    client: Any,
    local_path: str | Path,
    *,
    dataset_name: str,
    season: int | None,
    expected_sha256: str,
    bucket: str = DEFAULT_BUCKET,
    chunk_bytes: int = CHUNK_BYTES,
) -> str:
    if chunk_bytes <= 0 or chunk_bytes > 5 * 1024 * 1024:
        raise ValueError("chunk_bytes must be in (0, 5 MiB]")
    path = Path(local_path)
    digest = hashlib.sha256()
    parts: list[dict[str, Any]] = []
    base = f"nfl/p1/{dataset_name.lower()}/{season if season is not None else 'global'}/{expected_sha256}"
    bucket_api = client.storage.from_(bucket)

    with path.open("rb") as handle:
        index = 0
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
            chunk_sha = hashlib.sha256(chunk).hexdigest()
            object_path = f"{base}/part-{index:05d}-{chunk_sha}.bin"
            _upload_immutable(bucket_api, object_path, chunk, content_type="application/octet-stream")
            parts.append({
                "index": index,
                "path": object_path,
                "byte_count": len(chunk),
                "sha256": chunk_sha,
            })
            index += 1

    actual_sha = digest.hexdigest()
    if actual_sha != expected_sha256:
        raise ValueError("local source bytes no longer match captured SHA-256")
    if not parts:
        raise ValueError("cannot preserve empty source")

    manifest = {
        "version": 1,
        "dataset_name": dataset_name,
        "season": season,
        "content_sha256": actual_sha,
        "byte_count": sum(p["byte_count"] for p in parts),
        "chunk_bytes_max": chunk_bytes,
        "parts": parts,
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = f"{base}/manifest-{manifest_sha}.json"
    _upload_immutable(bucket_api, manifest_path, manifest_bytes, content_type="application/json")
    return f"supabase://{bucket}/{manifest_path}"
