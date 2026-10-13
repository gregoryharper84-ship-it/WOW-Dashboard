"""Bounded, resumable WNBA historical close-proxy acquisition checkpoint.

Research evidence only. Callers must persist the returned JSON checkpoint
durably between invocations; no live admission or certification is granted.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

from v17.spread_espn_close_proxy_source import acquire_wnba_close_proxies

CAN_EXECUTE = False
MAX_EVENTS_PER_BATCH = 25
SCHEMA_VERSION = 1


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def advance_wnba_historical_batch(
    *,
    identities: Mapping[str, Mapping[str, Any]],
    holdout_event_ids: Sequence[str],
    summary_loader: Callable[[str], Mapping[str, Any]],
    checkpoint: Mapping[str, Any] | None = None,
    batch_size: int = MAX_EVENTS_PER_BATCH,
) -> dict[str, Any]:
    """Acquire at most one bounded chunk of an immutable holdout.

    A failed event is recorded and not silently treated as successful evidence.
    A new invocation may resume from the output checkpoint. Checkpoint hashes
    protect against accidental mutation or a different holdout/identity set.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_EVENTS_PER_BATCH:
        raise ValueError("WNBA_BATCH_SIZE_OUT_OF_BOUNDS")
    ids = sorted(str(i).strip() for i in holdout_event_ids)
    if not ids or any(not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("WNBA_HOLDOUT_IDENTITIES_INVALID")
    scope = [{"event_id": i, "identity": dict(identities.get(i) or {})} for i in ids]
    scope_hash = _digest(scope)

    if checkpoint is None:
        completed: dict[str, Any] = {}
        failures: dict[str, str] = {}
        attempted: list[str] = []
    else:
        if (checkpoint.get("schema_version") != SCHEMA_VERSION
                or checkpoint.get("scope_sha256") != scope_hash
                or checkpoint.get("evidence_class") != "ESPN_HISTORICAL_CLOSE_PROXY"):
            raise ValueError("WNBA_BATCH_CHECKPOINT_SCOPE_MISMATCH")
        completed = dict(checkpoint.get("evidence_by_event") or {})
        failures = dict(checkpoint.get("blockers_by_event") or {})
        attempted = list(checkpoint.get("attempted_event_ids") or [])
        if (len(attempted) != len(set(attempted)) or not set(attempted).issubset(ids)
                or set(completed) & set(failures)
                or set(completed) | set(failures) != set(attempted)):
            raise ValueError("WNBA_BATCH_CHECKPOINT_INVALID")
        expected_integrity = _digest({
            "attempted_event_ids": attempted,
            "evidence_by_event": completed,
            "blockers_by_event": failures,
        })
        if expected_integrity != checkpoint.get("checkpoint_sha256"):
            raise ValueError("WNBA_BATCH_CHECKPOINT_INTEGRITY_MISMATCH")
        for event_id, item in completed.items():
            if (not isinstance(item, dict) or item.get("event_id") != event_id
                    or item.get("evidence_class") != "ESPN_HISTORICAL_CLOSE_PROXY"
                    or item.get("live_card_receipt_eligible") is not False
                    or item.get("probability_publishable") is not False
                    or item.get("can_execute") is not False):
                raise ValueError("WNBA_BATCH_CHECKPOINT_EVIDENCE_INVALID")

    pending = [event_id for event_id in ids if event_id not in set(attempted)]
    selected = pending[:batch_size]
    if selected:
        evidence, audit = acquire_wnba_close_proxies(
            identities=identities,
            test_event_ids=selected,
            summary_loader=summary_loader,
        )
        newly_complete = {event_id: close.payload() for event_id, close in evidence.items()}
        # Preserve every failed identity with an explicit typed code.
        if len(newly_complete) + sum(audit["blocker_counts"].values()) != len(selected):
            raise ValueError("WNBA_BATCH_ACQUISITION_RECONCILIATION_FAILED")
        completed.update(newly_complete)
        source_blockers = audit.get("blockers_by_event") or {}
        for event_id in selected:
            if event_id not in newly_complete:
                failures[event_id] = str(source_blockers.get(event_id) or "WNBA_HISTORICAL_BATCH_EVENT_UNBOUND")
        attempted.extend(selected)

    integrity = {
        "attempted_event_ids": attempted,
        "evidence_by_event": completed,
        "blockers_by_event": failures,
    }
    remaining = len(ids) - len(attempted)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope_sha256": scope_hash,
        **integrity,
        "checkpoint_sha256": _digest(integrity),
        "holdout_event_n": len(ids),
        "attempted_n": len(attempted),
        "bound_event_n": len(completed),
        "blocked_event_n": len(failures),
        "remaining_event_n": remaining,
        "status": "BATCH_COMPLETE_REVIEW_REQUIRED" if remaining == 0 else "BATCH_IN_PROGRESS",
        "evidence_class": "ESPN_HISTORICAL_CLOSE_PROXY",
        "source_quote_timestamp_state": "HISTORICAL_FINAL_SUMMARY_CLOSE_PROXY_TIMESTAMP_UNAVAILABLE",
        "historical_certification_evidence_only": True,
        "live_card_receipt_eligible": False,
        "clv_evidence": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
        "global_terminal_reducer": "V17_TERMINAL_REDUCER",
    }


def write_checkpoint_atomic(path: str | Path, checkpoint: Mapping[str, Any]) -> None:
    """Durably replace one checkpoint under an external single-writer lease.

    The caller must enforce one writer per run/holdout, and must use a persistent
    volume or CI artifact upload to retain the file beyond worker lifetime.
    """
    target = Path(path)
    if not target.parent.is_dir():
        raise ValueError("WNBA_CHECKPOINT_DIRECTORY_MISSING")
    if target.is_symlink():
        raise ValueError("WNBA_CHECKPOINT_SYMLINK_FORBIDDEN")
    if _digest({
        "attempted_event_ids": checkpoint.get("attempted_event_ids"),
        "evidence_by_event": checkpoint.get("evidence_by_event"),
        "blockers_by_event": checkpoint.get("blockers_by_event"),
    }) != checkpoint.get("checkpoint_sha256"):
        raise ValueError("WNBA_BATCH_CHECKPOINT_INTEGRITY_MISMATCH")
    data = (json.dumps(dict(checkpoint), sort_keys=True, allow_nan=False) + "\\n").encode()
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=".wnba-", delete=False) as output:
            tmp = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, target)
        tmp = None
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def read_checkpoint(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if target.is_symlink():
        raise ValueError("WNBA_CHECKPOINT_SYMLINK_FORBIDDEN")
    payload = json.loads(target.read_text())
    if not isinstance(payload, dict):
        raise ValueError("WNBA_BATCH_CHECKPOINT_INVALID")
    integrity = {
        "attempted_event_ids": payload.get("attempted_event_ids"),
        "evidence_by_event": payload.get("evidence_by_event"),
        "blockers_by_event": payload.get("blockers_by_event"),
    }
    if _digest(integrity) != payload.get("checkpoint_sha256"):
        raise ValueError("WNBA_BATCH_CHECKPOINT_INTEGRITY_MISMATCH")
    return payload
