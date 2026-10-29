"""Private append-only, hash-chained DAFT prospective shadow snapshots.

Not a governed prediction ledger. No result backfill, no public artifacts,
no automatic model promotion, and no execution capability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import fcntl
from typing import Any, Mapping

from v17.daft_external_research import exact_contract_key, _time

CAN_EXECUTE = False
LEDGER_SCHEMA = "WOW_DAFT_PROSPECTIVE_SHADOW_V1"
MAX_LEDGER_BYTES = 10_000_000


def _serialized(data: Mapping[str, Any]) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _ledger_path(filename: str) -> Path:
    path = Path(filename).expanduser().absolute()
    repo = Path(__file__).resolve().parents[2]
    if repo == path or repo in path.parents:
        raise ValueError("Paid shadow ledger cannot be written in public repository")
    if path.is_symlink() or not path.parent.is_dir() or path.parent.stat().st_mode & 0o077:
        raise ValueError("Private ledger requires a private 0700 parent directory")
    return path


def append_prospective_shadow(
    filename: str,
    record: Mapping[str, Any],
    *,
    observed_at: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Freeze only attested, exact, pregame evidence into a private ledger.

    A source-provided timestamp cannot substitute for the actual ingestion
    clock. The current clock must also precede the event, with observation
    no more than 90 seconds old. This guards against later archive replay.
    """
    path = _ledger_path(filename)
    actual = now or datetime.now(timezone.utc)
    if actual.utcoffset() is None:
        raise ValueError("Current clock must be timezone-aware")
    actual = actual.astimezone(timezone.utc)
    observed = _time(observed_at)
    capture = _time(record.get("capture_at"))
    event = _time(record.get("event_start_at"))
    if (record.get("prospective_eligible") is not True
            or record.get("capture_attested") is not True
            or exact_contract_key(record.get("contract") or {}) is None
            or observed is None or capture is None or event is None
            or not capture <= observed <= actual < event
            or (actual - observed).total_seconds() > 90):
        raise ValueError("Cannot freeze retrospective, stale or unverified shadow observation")
    snapshot = {
        "schema": LEDGER_SCHEMA,
        "kind": "PREGAME_SHADOW_OBSERVATION",
        "observed_at": observed_at,
        "record": dict(record),
        "can_execute": False,
    }
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            raise ValueError("Private ledger requires 0600 file mode")
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0, os.SEEK_END)
            if stream.tell() > MAX_LEDGER_BYTES:
                raise ValueError("Private ledger size limit")
            stream.seek(0)
            records = [json.loads(line) for line in stream if line.strip()]
            previous = None
            for prior in records:
                prior_hash = prior.get("record_sha256")
                payload = {k: v for k, v in prior.items() if k != "record_sha256"}
                if (prior.get("previous_sha256") != previous
                        or sha256(_serialized(payload)).hexdigest() != prior_hash):
                    raise ValueError("Append prohibited: ledger hash chain invalid")
                previous = prior_hash
            identity = (record.get("message_id"), record.get("version_sha256"),
                        record.get("capture_at"))
            for row in records:
                stored = row.get("record") or {}
                if (stored.get("message_id"), stored.get("version_sha256"),
                        stored.get("capture_at")) == identity:
                    return {"status": "DUPLICATE_IMMUTABLE_OBSERVATION",
                            "record_sha256": row["record_sha256"], "can_execute": False}
            snapshot["previous_sha256"] = previous
            digest = sha256(_serialized(snapshot)).hexdigest()
            snapshot["record_sha256"] = digest
            stream.seek(0, os.SEEK_END)
            stream.write(_serialized(snapshot).decode("utf-8") + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # fdopen closes its own descriptor; handle pre-fdopen failures safely.
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return {"status": "FROZEN_RESEARCH_ONLY", "record_sha256": digest,
            "can_execute": False}
