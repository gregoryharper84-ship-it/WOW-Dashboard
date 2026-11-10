"""Fail-closed repair loop for governed prop evidence acquisition.

This is the first P0 acquisition-repair layer. It does not fabricate evidence
or scrape unapproved providers. When the caller's snapshot is missing or
incomplete, it searches the backend-only governed Supabase evidence ledger for
newer exact-identity snapshots and re-validates each candidate through the
existing wow_prop_evidence_snapshot contract.

External provider A/B/C acquisition remains a separate upstream responsibility.
can_execute is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException


REPAIRABLE_EVIDENCE_CODES = {
    "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
    "RUN_INVALID_ACQUISITION_INCOMPLETE",
    "PROP_EVIDENCE_INCOMPLETE",
    "PROP_EVIDENCE_STALE",
}
# Read two observations to detect a non-unique maximum capture time.
# An older PASS row is never a fallback after newer evidence.
MAX_AUTHORITY_OBSERVATIONS = 2
AMBIGUOUS_LATEST_SNAPSHOT = "PROP_LATEST_SNAPSHOT_AMBIGUOUS"


class SnapshotAuthorityAmbiguous(ValueError):
    """Cannot prove a unique, timezone-aware latest observation."""


def _captured_instant(row: dict[str, Any]) -> datetime:
    raw = row.get("captured_at")
    if not isinstance(raw, str) or not raw.strip():
        raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT)
    try:
        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT) from exc
    if instant.utcoffset() is None:
        raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT)
    return instant.astimezone(timezone.utc)



def _attempt(
    *,
    path: str,
    source_snapshot_id: str | None,
    code: str | None,
    status: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "source_snapshot_id": source_snapshot_id,
        "code": code,
        "status": status,
    }


def _annotate(
    payload: dict[str, Any],
    *,
    requested_source_snapshot_id: str,
    attempts: list[dict[str, Any]],
    repair_status: str,
) -> dict[str, Any]:
    result = dict(payload)
    result["requested_source_snapshot_id"] = requested_source_snapshot_id
    result["effective_source_snapshot_id"] = result.get("source_snapshot_id") or requested_source_snapshot_id
    result["acquisition_repair_status"] = repair_status
    result["acquisition_attempts"] = attempts
    result["can_execute"] = False
    return result


def _fallback_snapshot_ids(client: Any, req: Any) -> list[str]:
    """Return only the latest exact-identity observation before event start."""
    query = (
        client.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id,captured_at")
        .eq("event_id", req.event_id)
        .eq("sport", req.sport)
        .eq("player", req.player)
        .eq("stat_type", req.stat_type)
        .eq("line", req.line)
        .lt("captured_at", req.event_start_time)
        .order("captured_at", desc=True)
        .limit(MAX_AUTHORITY_OBSERVATIONS)
    )
    response = query.execute()
    rows = response.data or []
    if not isinstance(rows, list):
        raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT)
    if not rows:
        return []
    if not isinstance(rows[0], dict):
        raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT)
    newest_time = _captured_instant(rows[0])
    if len(rows) > 1:
        if not isinstance(rows[1], dict):
            raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT)
        if _captured_instant(rows[1]) == newest_time:
            # Different immutable snapshots at one capture instant have no
            # ordering authority. Never arbitrarily select a clean one.
            raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT)
    newest_id = rows[0].get("source_snapshot_id")
    if not newest_id:
        raise SnapshotAuthorityAmbiguous(AMBIGUOUS_LATEST_SNAPSHOT)
    return [str(newest_id)]


def _authority_hold(blocker: str) -> dict[str, Any]:
    """Fail closed when latest-snapshot authority cannot be established."""
    return {
        "ok": False,
        "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
        "hydration_status": "FAILED",
        "blockers": [blocker],
        "probability_publishable": False,
        "can_execute": False,
    }


def repair_prop_evidence(
    req: Any,
    *,
    primary_fetch: Callable[[Any], dict[str, Any]],
    client: Any,
) -> dict[str, Any]:
    """Attempt requested snapshot, then exact governed-snapshot fallbacks.

    Every fallback is passed back through the original evidence validator. The
    repair layer never treats a raw table row as model-ready evidence, and the
    lookup excludes snapshots captured at or after event start. A newer failed
    observation is authoritative, not a reason to search older PASS records.
    """
    requested_id = str(req.source_snapshot_id)
    primary = primary_fetch(req)
    primary_code = str(primary.get("code") or "") if isinstance(primary, dict) else ""
    attempts = [
        _attempt(
            path="REQUESTED_SNAPSHOT",
            source_snapshot_id=requested_id,
            code=primary_code or None,
            status="PASS" if primary_code == "PROP_EVIDENCE_READY" else "FAILED",
        )
    ]

    if isinstance(primary, dict) and primary.get("ok") is True and primary_code == "PROP_EVIDENCE_READY":
        # A requested READY snapshot can predate a newer ruled-out player.
        # Always prove latest identity before accepting a caller-selected ID.
        try:
            newest_ids = _fallback_snapshot_ids(client, req)
        except SnapshotAuthorityAmbiguous:
            return _annotate(
                _authority_hold(AMBIGUOUS_LATEST_SNAPSHOT),
                requested_source_snapshot_id=requested_id,
                attempts=attempts,
                repair_status="LATEST_AUTHORITY_AMBIGUOUS",
            )
        except Exception:
            return _annotate(
                _authority_hold("PROP_EVIDENCE_BRIDGE_UNAVAILABLE"),
                requested_source_snapshot_id=requested_id,
                attempts=attempts,
                repair_status="LATEST_AUTHORITY_LOOKUP_FAILED",
            )
        if not newest_ids:
            return _annotate(
                _authority_hold("PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"),
                requested_source_snapshot_id=requested_id,
                attempts=attempts,
                repair_status="LATEST_AUTHORITY_UNVERIFIED",
            )
        newest_id = newest_ids[0]
        if newest_id != requested_id:
            try:
                newest_result = primary_fetch(req.model_copy(update={"source_snapshot_id": newest_id}))
            except Exception:
                newest_result = _authority_hold("PROP_EVIDENCE_BRIDGE_UNAVAILABLE")
            newest_code = str(newest_result.get("code") or "") if isinstance(newest_result, dict) else ""
            ready = (
                isinstance(newest_result, dict)
                and newest_result.get("ok") is True
                and newest_code == "PROP_EVIDENCE_READY"
            )
            attempts.append(
                _attempt(
                    path="LATEST_SNAPSHOT_AUTHORITY",
                    source_snapshot_id=newest_id,
                    code=newest_code or None,
                    status="PASS" if ready else "FAILED",
                )
            )
            return _annotate(
                newest_result if isinstance(newest_result, dict)
                else _authority_hold("PROP_EVIDENCE_BRIDGE_INVALID_RESPONSE"),
                requested_source_snapshot_id=requested_id,
                attempts=attempts,
                repair_status="LATEST_OBSERVATION_SELECTED" if ready else "LATEST_OBSERVATION_NOT_READY",
            )
        return _annotate(
            primary,
            requested_source_snapshot_id=requested_id,
            attempts=attempts,
            repair_status="NOT_NEEDED",
        )

    # The RPC includes its stored blockers on incomplete snapshots. Replacing
    # an explicit hold with an earlier clean observation would defeat the
    # source-authoritative participation gate.
    if isinstance(primary, dict) and primary.get("blockers"):
        return _annotate(
            primary,
            requested_source_snapshot_id=requested_id,
            attempts=attempts,
            repair_status="NOT_REPAIRABLE",
        )

    if primary_code not in REPAIRABLE_EVIDENCE_CODES:
        return _annotate(
            primary,
            requested_source_snapshot_id=requested_id,
            attempts=attempts,
            repair_status="NOT_REPAIRABLE",
        )

    try:
        fallback_ids = _fallback_snapshot_ids(client, req)
    except SnapshotAuthorityAmbiguous:
        return _annotate(
            _authority_hold(AMBIGUOUS_LATEST_SNAPSHOT),
            requested_source_snapshot_id=requested_id,
            attempts=attempts,
            repair_status="LATEST_AUTHORITY_AMBIGUOUS",
        )
    except Exception as exc:
        attempts.append(
            _attempt(
                path="GOVERNED_SNAPSHOT_LOOKUP",
                source_snapshot_id=None,
                code=type(exc).__name__,
                status="FAILED",
            )
        )
        return _annotate(
            primary,
            requested_source_snapshot_id=requested_id,
            attempts=attempts,
            repair_status="FALLBACK_LOOKUP_FAILED",
        )

    attempted_ids = {requested_id}
    for fallback_id in fallback_ids:
        if fallback_id in attempted_ids:
            continue
        attempted_ids.add(fallback_id)
        fallback_req = req.model_copy(update={"source_snapshot_id": fallback_id})
        try:
            candidate = primary_fetch(fallback_req)
        except HTTPException as exc:
            attempts.append(
                _attempt(
                    path="GOVERNED_SNAPSHOT_FALLBACK",
                    source_snapshot_id=fallback_id,
                    code=(exc.detail.get("code") if isinstance(exc.detail, dict) else "HTTP_EXCEPTION"),
                    status="FAILED",
                )
            )
            continue
        except Exception as exc:  # keep the original row blocker; do not globalize
            attempts.append(
                _attempt(
                    path="GOVERNED_SNAPSHOT_FALLBACK",
                    source_snapshot_id=fallback_id,
                    code=type(exc).__name__,
                    status="FAILED",
                )
            )
            continue

        candidate_code = str(candidate.get("code") or "") if isinstance(candidate, dict) else ""
        ready = isinstance(candidate, dict) and candidate.get("ok") is True and candidate_code == "PROP_EVIDENCE_READY"
        attempts.append(
            _attempt(
                path="GOVERNED_SNAPSHOT_FALLBACK",
                source_snapshot_id=fallback_id,
                code=candidate_code or None,
                status="PASS" if ready else "FAILED",
            )
        )
        if ready:
            return _annotate(
                candidate,
                requested_source_snapshot_id=requested_id,
                attempts=attempts,
                repair_status="RECOVERED_FROM_GOVERNED_SNAPSHOT",
            )
        # Never continue to older PASS records after the newest observation
        # failed validation: doing so could resurrect a ruled-out player.
        return _annotate(
            candidate if isinstance(candidate, dict) else primary,
            requested_source_snapshot_id=requested_id,
            attempts=attempts,
            repair_status="NEWEST_EVIDENCE_NOT_READY",
        )

    return _annotate(
        primary,
        requested_source_snapshot_id=requested_id,
        attempts=attempts,
        repair_status="FALLBACKS_EXHAUSTED",
    )
