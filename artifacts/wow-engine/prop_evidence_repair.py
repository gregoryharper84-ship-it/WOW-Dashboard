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

from typing import Any, Callable

from fastapi import HTTPException


REPAIRABLE_EVIDENCE_CODES = {
    "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
    "RUN_INVALID_ACQUISITION_INCOMPLETE",
    "PROP_EVIDENCE_INCOMPLETE",
    "PROP_EVIDENCE_STALE",
}
MAX_GOVERNED_SNAPSHOT_FALLBACKS = 3


def _attempt(
    *,
    path: str,
    source_snapshot_id: str | None,
    code: str | None,
    status: str,
) -> dict[str, Any]:
    """Record one acquisition attempt without changing its outcome."""
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
    """Attach requested/effective snapshot identity and repair history."""
    result = dict(payload)
    result["requested_source_snapshot_id"] = requested_source_snapshot_id
    result["effective_source_snapshot_id"] = result.get("source_snapshot_id") or requested_source_snapshot_id
    result["acquisition_repair_status"] = repair_status
    result["acquisition_attempts"] = attempts
    result["can_execute"] = False
    return result


def _canonical_identity(req: Any) -> tuple[str, str, str, str]:
    """Normalize lookup keys exactly as the governed snapshot writer stores them.

    ``pick_request_runtime_core._validate_evidence`` / ``_snapshot_payload``
    persist ``event_id`` stripped, ``sport`` uppercase, ``player``
    whitespace-collapsed and ``stat_type`` as the canonical alias-resolved
    label. The primary ``wow_prop_evidence_snapshot`` RPC compares event id and
    player exactly, so a padded caller identity could never read the row it
    wrote. The same canonical keys are therefore used for both the fallback
    lookup and the fallback revalidation request.

    This changes identity comparison only. It cannot create evidence, relax
    calibration, or publish a probability.
    """
    # Reuse the writer's own alias resolution. Imported lazily because the
    # governed runtime module loads the full agent graph and imports this
    # repair layer indirectly through api_prod_market.
    from pick_request_runtime_core import _canonical_stat

    event_id = str(getattr(req, "event_id", "") or "").strip()
    sport = str(getattr(req, "sport", "") or "").strip().upper()
    player = " ".join(str(getattr(req, "player", "") or "").split())
    stat_type = _canonical_stat(sport, str(getattr(req, "stat_type", "") or ""))
    return event_id, sport, player, stat_type


def _fallback_snapshot_ids(client: Any, req: Any) -> list[str]:
    """Return newest exact-identity snapshots captured strictly before start."""
    event_id, sport, player, stat_type = _canonical_identity(req)
    query = (
        client.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id,captured_at")
        .eq("event_id", event_id)
        .eq("sport", sport)
        .eq("player", player)
        .eq("stat_type", stat_type)
        .eq("line", req.line)
        .eq("hydration_status", "PASS")
        .lt("captured_at", req.event_start_time)
        .order("captured_at", desc=True)
        .limit(MAX_GOVERNED_SNAPSHOT_FALLBACKS)
    )
    response = query.execute()
    rows = response.data or []
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("source_snapshot_id")
        if value:
            ids.append(str(value))
    return ids


def repair_prop_evidence(
    req: Any,
    *,
    primary_fetch: Callable[[Any], dict[str, Any]],
    client: Any,
) -> dict[str, Any]:
    """Attempt requested snapshot, then exact governed-snapshot fallbacks.

    Every fallback is passed back through the original evidence validator. The
    repair layer never treats a raw table row as model-ready evidence, and the
    lookup excludes snapshots captured at or after event start.
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
        return _annotate(
            primary,
            requested_source_snapshot_id=requested_id,
            attempts=attempts,
            repair_status="NOT_NEEDED",
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

    event_id, sport, player, stat_type = _canonical_identity(req)
    attempted_ids = {requested_id}
    for fallback_id in fallback_ids:
        if fallback_id in attempted_ids:
            continue
        attempted_ids.add(fallback_id)
        # The RPC revalidates against the persisted identity, so the fallback
        # request must carry the same canonical keys used to locate the row.
        # ``model_copy`` leaves the caller's request object untouched.
        fallback_req = req.model_copy(
            update={
                "source_snapshot_id": fallback_id,
                "event_id": event_id,
                "sport": sport,
                "player": player,
                "stat_type": stat_type,
            }
        )
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

    return _annotate(
        primary,
        requested_source_snapshot_id=requested_id,
        attempts=attempts,
        repair_status="FALLBACKS_EXHAUSTED",
    )
