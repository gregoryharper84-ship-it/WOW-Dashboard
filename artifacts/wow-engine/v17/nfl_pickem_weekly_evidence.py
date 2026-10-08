"""Fail-closed, non-serving weekly NFL Pick'em evidence reconciliation.

Phase-1 Class B integration seam for issue #1530. This module does NOT fetch
official NFL data, assert that external evidence is genuine, persist a report,
schedule a job, publish a report, or replace the fitted NFL specialist.

Its only positive result is a *candidate* audit after exact event reconciliation;
independent source attestation, durable storage, dispatch and production acceptance
are separate mandatory gates. No wager or model changes.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from v17.nfl_pickem_governed_accuracy_audit import (
    AccuracyAuditError,
    audit_governed_pickem_week,
)

CAN_EXECUTE = False
REPORT_VERSION = "NFL_PICKEM_WEEKLY_EVIDENCE_V1"
BLOCKED = "EVIDENCE_BLOCKED_NO_PUBLICATION"
RECONCILED = "AUDIT_CANDIDATE_UNVERIFIED_NO_PUBLICATION"
_ALLOWED_SCHEDULE_STATUS = frozenset({"SCHEDULED", "POSTPONED", "CANCELLED"})


class WeeklyEvidenceError(ValueError):
    """Untrusted or malformed evidence; never turn a partial week into accuracy."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _required_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeeklyEvidenceError(code)
    return value.strip()


def _time(value: Any, code: str) -> datetime:
    text = _required_text(value, code)
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WeeklyEvidenceError(code) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise WeeklyEvidenceError(code)
    return result.astimezone(timezone.utc)


def _rows_by_event(value: Any, label: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, (tuple, list)):
        raise WeeklyEvidenceError(f"PICKEM_WEEKLY_{label}_ROWS_INVALID")
    result: dict[str, Mapping[str, Any]] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise WeeklyEvidenceError(f"PICKEM_WEEKLY_{label}_ROW_INVALID")
        key = _required_text(
            row.get("official_event_id"), f"PICKEM_WEEKLY_{label}_EVENT_ID_MISSING"
        )
        if key in result:
            raise WeeklyEvidenceError(f"PICKEM_WEEKLY_{label}_DUPLICATE_EVENT")
        result[key] = row
    return result


def _settlement_history(value: Any) -> tuple[dict[str, Mapping[str, Any]], dict[str, int]]:
    """Select only the last independently attested revision; keep history cardinality.

    Version gaps and duplicate revision identities block publication. No mutation
    of prior receipts or predictions is performed.
    """
    if not isinstance(value, (list, tuple)):
        raise WeeklyEvidenceError("PICKEM_WEEKLY_SETTLEMENT_ROWS_INVALID")
    groups: dict[str, dict[int, Mapping[str, Any]]] = {}
    identities: set[str] = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise WeeklyEvidenceError("PICKEM_WEEKLY_SETTLEMENT_ROW_INVALID")
        event = _required_text(
            row.get("official_event_id"), "PICKEM_WEEKLY_SETTLEMENT_EVENT_ID_MISSING"
        )
        revision = row.get("settlement_revision")
        if type(revision) is not int or revision < 1:
            raise WeeklyEvidenceError("PICKEM_WEEKLY_SETTLEMENT_REVISION_INVALID")
        receipt_id = _required_text(
            row.get("settlement_receipt_id"), "PICKEM_WEEKLY_SETTLEMENT_RECEIPT_MISSING"
        )
        if receipt_id in identities:
            raise WeeklyEvidenceError("PICKEM_WEEKLY_SETTLEMENT_RECEIPT_REUSED")
        identities.add(receipt_id)
        event_versions = groups.setdefault(event, {})
        if revision in event_versions:
            raise WeeklyEvidenceError("PICKEM_WEEKLY_SETTLEMENT_REVISION_DUPLICATE")
        event_versions[revision] = row

    latest: dict[str, Mapping[str, Any]] = {}
    counts: dict[str, int] = {}
    for event, revisions in groups.items():
        ordered = sorted(revisions)
        if ordered != list(range(1, ordered[-1] + 1)):
            raise WeeklyEvidenceError("PICKEM_WEEKLY_SETTLEMENT_REVISION_GAP")
        previous_time: datetime | None = None
        for revision in ordered:
            row = revisions[revision]
            _required_text(row.get("settlement_source"),
                           "PICKEM_WEEKLY_SETTLEMENT_SOURCE_MISSING")
            issued = _time(row.get("settled_at"),
                           "PICKEM_WEEKLY_SETTLEMENT_TIME_INVALID")
            if previous_time is not None and issued <= previous_time:
                raise WeeklyEvidenceError("PICKEM_WEEKLY_SETTLEMENT_REVISION_TIME_NOT_MONOTONIC")
            previous_time = issued
            if row.get("official_final_status") not in {"FINAL", "FINAL_OVERTIME"}:
                # An unsettled latest revision must remain blocked; the earlier
                # observed final may have been revoked.
                if revision == ordered[-1]:
                    latest[event] = row
                    break
                raise WeeklyEvidenceError("PICKEM_WEEKLY_NONFINAL_HISTORY_REVISION")
        else:
            latest[event] = revisions[ordered[-1]]
        counts[event] = len(ordered)
    return latest, counts


def _blocker(code: str, *, event_id: str | None = None) -> dict[str, str]:
    return {"code": code, **({"official_event_id": event_id} if event_id else {})}


def _base(
    season: int, week: int, expected_count: int, events: int,
    picks: int, finals: int, blockers: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "version": REPORT_VERSION,
        "status": BLOCKED,
        "season": season,
        "week": week,
        "expected_game_count": expected_count,
        "manifest_event_count": events,
        "prediction_receipt_event_count": picks,
        "settlement_event_count": finals,
        "blocker_count": len(blockers),
        "blockers": sorted(blockers, key=lambda x: (x["code"], x.get("official_event_id", ""))),
        "report": None,
        "candidate_receipt_id": None,
        "source_authenticity_verified": False,
        "durable_persistence_verified": False,
        "scheduled_delivery_verified": False,
        "publication_allowed": False,
        "can_execute": False,
    }


def reconcile_weekly_evidence(
    *,
    season: int,
    week: int,
    official_manifest: Mapping[str, Any],
    immutable_predictions: Sequence[Mapping[str, Any]],
    official_settlement_history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile supplied evidence, never generate sporting probabilities.

    Source adapters are NOT implemented here. A structurally complete audit
    remains unpublished until an independent reviewer validates actual source
    identities/receipts and the real service proves durable persistence, revision
    history, scheduled delivery and recipient readback.
    """
    if type(season) is not int or not 2020 <= season <= 2100 or (
        type(week) is not int or not 1 <= week <= 22
    ):
        raise WeeklyEvidenceError("PICKEM_WEEKLY_SEASON_WEEK_INVALID")
    if not isinstance(official_manifest, Mapping):
        raise WeeklyEvidenceError("PICKEM_WEEKLY_MANIFEST_INVALID")
    if official_manifest.get("season") != season or official_manifest.get("week") != week:
        raise WeeklyEvidenceError("PICKEM_WEEKLY_MANIFEST_SEASON_WEEK_MISMATCH")
    expected_count = official_manifest.get("expected_game_count")
    if type(expected_count) is not int or not 1 <= expected_count <= 20:
        raise WeeklyEvidenceError("PICKEM_WEEKLY_MANIFEST_EXPECTED_COUNT_INVALID")
    manifest_id = _required_text(
        official_manifest.get("manifest_receipt_id"), "PICKEM_WEEKLY_MANIFEST_RECEIPT_MISSING"
    )
    _required_text(
        official_manifest.get("schedule_source_receipt_id"),
        "PICKEM_WEEKLY_SCHEDULE_PROVENANCE_MISSING",
    )
    _required_text(
        official_manifest.get("schedule_snapshot_id"),
        "PICKEM_WEEKLY_SCHEDULE_SNAPSHOT_MISSING",
    )
    if official_manifest.get("manifest_status") != "FROZEN":
        raise WeeklyEvidenceError("PICKEM_WEEKLY_MANIFEST_NOT_FROZEN")
    frozen_at_text = _required_text(
        official_manifest.get("manifest_frozen_at"),
        "PICKEM_WEEKLY_MANIFEST_FROZEN_AT_MISSING",
    )
    _time(frozen_at_text, "PICKEM_WEEKLY_MANIFEST_FROZEN_AT_INVALID")

    manifest = _rows_by_event(official_manifest.get("events"), "MANIFEST")
    picks = _rows_by_event(immutable_predictions, "PREDICTIONS")
    settlements, revision_counts = _settlement_history(official_settlement_history)
    expected_ids = set(manifest)
    blockers: list[dict[str, str]] = []
    if len(expected_ids) != expected_count:
        blockers.append(_blocker("PICKEM_WEEKLY_MANIFEST_COUNT_MISMATCH"))
    for event_id in sorted(expected_ids):
        row = manifest[event_id]
        status = row.get("schedule_status")
        if status not in _ALLOWED_SCHEDULE_STATUS:
            blockers.append(_blocker("PICKEM_WEEKLY_SCHEDULE_STATUS_UNVERIFIED", event_id=event_id))
        elif status != "SCHEDULED":
            blockers.append(_blocker(f"PICKEM_WEEKLY_SCHEDULE_{status}_NEEDS_REVISION", event_id=event_id))
        if event_id not in picks:
            blockers.append(_blocker("PICKEM_WEEKLY_PREGAME_PREDICTION_MISSING", event_id=event_id))
        if event_id not in settlements:
            blockers.append(_blocker("PICKEM_WEEKLY_OFFICIAL_FINAL_MISSING", event_id=event_id))
        elif settlements[event_id].get("official_final_status") not in {"FINAL", "FINAL_OVERTIME"}:
            blockers.append(_blocker("PICKEM_WEEKLY_OFFICIAL_FINAL_NOT_VERIFIED", event_id=event_id))
    for event_id in sorted(set(picks) - expected_ids):
        blockers.append(_blocker("PICKEM_WEEKLY_UNSCHEDULED_PREDICTION", event_id=event_id))
    for event_id in sorted(set(settlements) - expected_ids):
        blockers.append(_blocker("PICKEM_WEEKLY_UNSCHEDULED_SETTLEMENT", event_id=event_id))
    if blockers:
        return _base(
            season, week, expected_count, len(manifest), len(picks), len(settlements), blockers
        )

    try:
        audited = audit_governed_pickem_week(
            season=season,
            week=week,
            expected_game_count=expected_count,
            manifest_receipt_id=manifest_id,
            manifest_frozen_at=frozen_at_text,
            manifest=list(manifest.values()),
            picks=list(picks.values()),
            settlements=list(settlements.values()),
        )
    except AccuracyAuditError as exc:
        return _base(
            season, week, expected_count, len(manifest), len(picks), len(settlements),
            [_blocker(exc.code)],
        )
    # The hash is an idempotent candidate identity, NOT a cryptographic source
    # authenticity attestation or durable storage/dispatch receipt.
    canonical = {
        "report_version": REPORT_VERSION,
        "season": season,
        "week": week,
        "manifest_receipt_id": manifest_id,
        "schedule_source_receipt_id": official_manifest["schedule_source_receipt_id"],
        "selected_model_receipt_ids": sorted(
            _required_text(row.get("source_prediction_id"),
                           "PICKEM_WEEKLY_PREDICTION_RECEIPT_MISSING")
            for row in picks.values()
        ),
        "latest_settlement_receipts": sorted(
            _required_text(row.get("settlement_receipt_id"),
                           "PICKEM_WEEKLY_SETTLEMENT_RECEIPT_MISSING")
            for row in settlements.values()
        ),
        "revision_counts": sorted(revision_counts.items()),
    }
    # Include every immutable revision payload in the key: a changed winner with
    # an accidentally reused latest ID must not silently dedupe a regrade.
    # A reused receipt ID with changed event/prediction contents must also
    # produce a different candidate identity (never silent overwrite).
    for label, rows in (
        ("manifest_events", manifest.values()),
        ("immutable_predictions", picks.values()),
    ):
        try:
            serialized = json.dumps(
                sorted((dict(row) for row in rows),
                       key=lambda row: str(row["official_event_id"])),
                sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise WeeklyEvidenceError(
                f"PICKEM_WEEKLY_{label.upper()}_NOT_CANONICAL_JSON"
            ) from exc
        canonical[f"{label}_sha256"] = hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()

    canonical["settlement_history_sha256"] = hashlib.sha256(
        json.dumps(
            sorted(
                (dict(row) for row in official_settlement_history),
                key=lambda r: (str(r.get("official_event_id")), int(r["settlement_revision"])),
            ),
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    key = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
    ).hexdigest()
    result = _base(season, week, expected_count, len(manifest),
                   len(picks), len(settlements), [])
    result.update({
        "status": RECONCILED,
        "report": audited,
        "candidate_receipt_id": f"nfl-weekly-audit-{key}",
        "settlement_revision_counts": revision_counts,
        # This is an unverified report candidate only; it cannot be emailed or
        # called complete without independent source attestations + durable
        # receipts from a governed Class B serving integration.
        "publication_allowed": False,
    })
    return result


__all__ = [
    "BLOCKED", "CAN_EXECUTE", "RECONCILED", "REPORT_VERSION",
    "WeeklyEvidenceError", "reconcile_weekly_evidence",
]
