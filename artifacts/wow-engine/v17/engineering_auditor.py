"""Continuous engineering audit control plane for WOW V17.

The auditor observes code/work health and persists evidence. It never writes
sporting-model code, never publishes probabilities, and never gains execution
authority. Findings are routed into the existing engineering backlog so they
must terminate through the governed engineering lifecycle.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

AUDITOR_ID = "WOW_ENGINEERING_AUDITOR"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
AUDITOR_VERSION = "1.0.0"

SLA_BY_SEVERITY = {
    "P0": timedelta(minutes=30),
    "P1": timedelta(hours=2),
    "P2": timedelta(hours=8),
    "P3": timedelta(hours=24),
    "P4": timedelta(hours=72),
}
TERMINAL_SOURCE_STATES = {"CLOSED", "MERGED", "COMPLETED", "RESOLVED", "CANCELED"}
TERMINAL_FINDING_STATUSES = {"RESOLVED", "DUPLICATE", "DEFERRED"}


@dataclass(frozen=True)
class AuditEvent:
    event_name: str
    action: str
    repository: str
    source_kind: str
    source_ref: str
    title: str
    state: str
    labels: tuple[str, ...] = ()
    draft: bool = False
    actor: str | None = None
    updated_at: datetime | None = None
    head_sha: str | None = None
    conclusion: str | None = None
    details: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "AuditEvent":
        labels = payload.get("labels") or []
        updated = payload.get("updated_at")
        if isinstance(updated, str) and updated:
            updated = parse_timestamp(updated)
        if updated is not None and not isinstance(updated, datetime):
            raise ValueError("updated_at must be an ISO-8601 timestamp")
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            raise ValueError("details must be an object")
        return cls(
            event_name=str(payload.get("event_name") or "").strip(),
            action=str(payload.get("action") or "").strip(),
            repository=str(payload.get("repository") or "").strip(),
            source_kind=str(payload.get("source_kind") or "").strip().upper(),
            source_ref=str(payload.get("source_ref") or "").strip(),
            title=str(payload.get("title") or "").strip(),
            state=str(payload.get("state") or "OPEN").strip().upper(),
            labels=tuple(str(v) for v in labels if str(v).strip()),
            draft=bool(payload.get("draft", False)),
            actor=str(payload.get("actor") or "").strip() or None,
            updated_at=updated,
            head_sha=str(payload.get("head_sha") or "").strip() or None,
            conclusion=str(payload.get("conclusion") or "").strip().lower() or None,
            details=details,
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_fingerprint(*parts: object) -> str:
    canonical = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def work_fingerprint(repository: str, source_kind: str, source_ref: str) -> str:
    return stable_fingerprint("WORK", repository.lower(), source_kind.upper(), source_ref)


def finding_fingerprint(finding_type: str, component: str) -> str:
    return stable_fingerprint("FINDING", finding_type.upper(), component)


def severity_from_labels(labels: Iterable[str], *, source_kind: str = "") -> str:
    normalized = {str(label).strip().lower().replace("_", "-") for label in labels}
    explicit = {
        "P0": {"p0", "priority:p0", "severity:p0", "critical", "sev0"},
        "P1": {"p1", "priority:p1", "severity:p1", "high", "sev1"},
        "P2": {"p2", "priority:p2", "severity:p2", "medium", "sev2"},
        "P3": {"p3", "priority:p3", "severity:p3", "low", "sev3"},
        "P4": {"p4", "priority:p4", "severity:p4", "backlog", "sev4"},
    }
    for severity in ("P0", "P1", "P2", "P3", "P4"):
        if normalized.intersection(explicit[severity]):
            return severity
    if str(source_kind).upper() == "GITHUB_PR":
        return "P2"
    return "P3"


def next_audit_at(last_progress_at: datetime, severity: str, *, draft: bool = False) -> datetime:
    severity = str(severity or "P3").upper()
    delay = SLA_BY_SEVERITY.get(severity, SLA_BY_SEVERITY["P3"])
    if draft:
        delay *= 2
    return last_progress_at.astimezone(timezone.utc) + delay


def is_terminal_source_state(state: str) -> bool:
    return str(state or "").upper() in TERMINAL_SOURCE_STATES


def is_code_health_event(event: AuditEvent) -> bool:
    return event.source_kind == "CODE_HEALTH_RUN"


def is_code_health_failure(conclusion: str | None) -> bool:
    return str(conclusion or "").lower() not in {"success", "neutral", "skipped"}


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return list(data) if isinstance(data, list) else []


class EngineeringAuditStore:
    """Persistence adapter over the existing Supabase service-role client."""

    def __init__(self, client: Any):
        self.client = client

    def _select_one(self, table: str, column: str, value: str) -> dict[str, Any] | None:
        response = self.client.table(table).select("*").eq(column, value).limit(1).execute()
        rows = _rows(response)
        return rows[0] if rows else None

    def _log_event(self, event_type: str, detail: dict[str, Any]) -> None:
        self.client.table("wow_agent_audit_events").insert(
            {"event_type": event_type, "actor": AUDITOR_ID, "detail_redacted": detail, "can_execute": False}
        ).execute()

    def upsert_work_event(self, event: AuditEvent, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        fingerprint = work_fingerprint(event.repository, event.source_kind, event.source_ref)
        existing = self._select_one("wow_engineering_audit_work_items", "fingerprint", fingerprint)
        source_state = event.state.upper()
        terminal = is_terminal_source_state(source_state)
        severity = severity_from_labels(event.labels, source_kind=event.source_kind)
        progress_at = event.updated_at or now
        comparable = {
            "action": event.action,
            "state": source_state,
            "labels": sorted(event.labels),
            "draft": event.draft,
            "head_sha": event.head_sha,
            "details": event.details or {},
        }
        meaningful_progress = existing is None or comparable != (existing.get("state_payload") or {})
        if existing and not meaningful_progress:
            progress_at = parse_timestamp(str(existing["last_meaningful_progress_at"]))
        due_at = next_audit_at(progress_at, severity, draft=event.draft)
        payload = {
            "fingerprint": fingerprint,
            "source_system": "GITHUB",
            "repository": event.repository,
            "source_kind": event.source_kind,
            "source_ref": event.source_ref,
            "title": event.title or f"{event.source_kind} {event.source_ref}",
            "state": "TERMINAL" if terminal else "OPEN",
            "source_status": source_state,
            "severity": severity,
            "draft": event.draft,
            "head_sha": event.head_sha,
            "labels": list(event.labels),
            "state_payload": comparable,
            "last_meaningful_progress_at": iso(progress_at),
            "expected_next_action": None if terminal else "Continue governed engineering lifecycle toward an allowed terminal state.",
            "next_audit_at": iso(due_at),
            "last_seen_at": iso(now),
            "closed_at": iso(now) if terminal else None,
            "terminal_state": source_state if terminal else None,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
            "updated_at": iso(now),
        }
        response = self.client.table("wow_engineering_audit_work_items").upsert(payload, on_conflict="fingerprint").execute()
        rows = _rows(response)
        work_item = rows[0] if rows else self._select_one("wow_engineering_audit_work_items", "fingerprint", fingerprint)
        if work_item is None:
            raise RuntimeError("AUDITOR_WORK_ITEM_UPSERT_NO_RECEIPT")
        if terminal:
            self.resolve_finding("STALE_WORK", fingerprint, resolution="SOURCE_REACHED_TERMINAL_STATE", now=now)
        elif meaningful_progress:
            self.resolve_finding("STALE_WORK", fingerprint, resolution="MEANINGFUL_PROGRESS_RESUMED", now=now)
        self._log_event(
            "ENGINEERING_AUDITOR_WORK_EVENT",
            {
                "event_name": event.event_name,
                "action": event.action,
                "source_kind": event.source_kind,
                "source_ref": event.source_ref,
                "work_fingerprint": fingerprint,
                "meaningful_progress": meaningful_progress,
                "state": payload["state"],
                "can_execute": False,
            },
        )
        self.touch_runtime(last_event_processed_at=now)
        return work_item

    def ingest_code_health(self, event: AuditEvent, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        check_name = str((event.details or {}).get("check_name") or "V17_ENGINEERING_CODE_HEALTH")
        component = f"{event.repository}:{check_name}"
        if is_code_health_failure(event.conclusion):
            evidence = {
                "run_ref": event.source_ref,
                "head_sha": event.head_sha,
                "conclusion": event.conclusion or "unknown",
                "event_name": event.event_name,
                "details": event.details or {},
                "can_execute": False,
            }
            finding = self.open_finding(
                "CODE_HEALTH", component=component, severity="P1", source_ref=event.source_ref, evidence=evidence, now=now
            )
            self.touch_runtime(last_event_processed_at=now)
            return finding
        self.resolve_finding("CODE_HEALTH", component, resolution="LATEST_CODE_HEALTH_CHECK_PASSED", now=now)
        self._log_event(
            "ENGINEERING_AUDITOR_CODE_HEALTH_PASS",
            {"component": component, "run_ref": event.source_ref, "head_sha": event.head_sha, "can_execute": False},
        )
        self.touch_runtime(last_event_processed_at=now)
        return {"status": "RESOLVED_OR_CLEAR", "component": component, "can_execute": False}

    def ingest_event(self, event: AuditEvent, *, now: datetime | None = None) -> dict[str, Any]:
        if is_code_health_event(event):
            return self.ingest_code_health(event, now=now)
        if event.source_kind not in {"GITHUB_ISSUE", "GITHUB_PR"}:
            raise ValueError(f"unsupported source_kind: {event.source_kind}")
        return self.upsert_work_event(event, now=now)

    def open_finding(
        self,
        finding_type: str,
        *,
        component: str,
        severity: str,
        source_ref: str | None,
        evidence: dict[str, Any],
        work_item_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or utcnow()
        finding_type = finding_type.upper()
        fingerprint = finding_fingerprint(finding_type, component)
        existing = self._select_one("wow_engineering_audit_findings", "fingerprint", fingerprint)
        payload = {
            "fingerprint": fingerprint,
            "finding_type": finding_type,
            "work_item_id": work_item_id,
            "severity": severity,
            "component": component,
            "source_ref": source_ref,
            "status": "OPEN",
            "evidence": evidence,
            "first_detected_at": existing.get("first_detected_at") if existing else iso(now),
            "last_verified_at": iso(now),
            "resolved_at": None,
            "resolution": None,
            "terminal_status": None,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
            "updated_at": iso(now),
        }
        response = self.client.table("wow_engineering_audit_findings").upsert(payload, on_conflict="fingerprint").execute()
        rows = _rows(response)
        finding = rows[0] if rows else self._select_one("wow_engineering_audit_findings", "fingerprint", fingerprint)
        if finding is None:
            raise RuntimeError("AUDITOR_FINDING_UPSERT_NO_RECEIPT")
        self._mirror_finding_to_backlog(finding, now=now)
        self._log_event(
            "ENGINEERING_AUDITOR_FINDING_OPEN",
            {
                "finding_type": finding_type,
                "finding_fingerprint": fingerprint,
                "severity": severity,
                "component": component,
                "can_execute": False,
            },
        )
        return finding

    def resolve_finding(self, finding_type: str, component: str, *, resolution: str, now: datetime | None = None) -> bool:
        now = now or utcnow()
        fingerprint = finding_fingerprint(finding_type, component)
        existing = self._select_one("wow_engineering_audit_findings", "fingerprint", fingerprint)
        if not existing or str(existing.get("status") or "").upper() in TERMINAL_FINDING_STATUSES:
            return False
        self.client.table("wow_engineering_audit_findings").update(
            {
                "status": "RESOLVED",
                "last_verified_at": iso(now),
                "resolved_at": iso(now),
                "resolution": resolution,
                "terminal_status": "FIXED_AND_VERIFIED",
                "updated_at": iso(now),
            }
        ).eq("fingerprint", fingerprint).execute()
        self._close_backlog_ticket(fingerprint, now=now)
        self._log_event(
            "ENGINEERING_AUDITOR_FINDING_RESOLVED",
            {"finding_type": finding_type, "finding_fingerprint": fingerprint, "resolution": resolution, "can_execute": False},
        )
        return True

    def reconcile_due(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or utcnow()
        response = self.client.table("wow_engineering_audit_work_items").select("*").eq("state", "OPEN").lte("next_audit_at", iso(now)).execute()
        opened: list[dict[str, Any]] = []
        for item in _rows(response):
            fingerprint = str(item.get("fingerprint") or "")
            if not fingerprint:
                continue
            evidence = {
                "source_system": item.get("source_system"),
                "source_kind": item.get("source_kind"),
                "source_ref": item.get("source_ref"),
                "last_meaningful_progress_at": item.get("last_meaningful_progress_at"),
                "next_audit_at": item.get("next_audit_at"),
                "expected_next_action": item.get("expected_next_action"),
                "source_status": item.get("source_status"),
                "can_execute": False,
            }
            opened.append(
                self.open_finding(
                    "STALE_WORK",
                    component=fingerprint,
                    severity=str(item.get("severity") or "P3"),
                    source_ref=str(item.get("source_ref") or "") or None,
                    evidence=evidence,
                    work_item_id=str(item.get("work_item_id") or "") or None,
                    now=now,
                )
            )
        self.touch_runtime(last_reconcile_at=now)
        self.refresh_runtime_counts(now=now)
        return opened

    def reconcile_backlog(self, *, now: datetime | None = None) -> int:
        """Mirror the existing operational backlog without recursively auditing auditor tickets."""
        now = now or utcnow()
        response = self.client.table("wow_engineering_backlog").select("*").execute()
        count = 0
        for row in _rows(response):
            if str(row.get("source") or "").upper() == "ENGINEERING_AUDITOR":
                continue
            ticket_id = str(row.get("ticket_id") or "").strip()
            if not ticket_id:
                continue
            status = str(row.get("status") or "OPEN").upper()
            terminal = status == "CLOSED"
            priority = str(row.get("priority") or "P3").upper()
            severity = priority if priority in SLA_BY_SEVERITY else "P3"
            fingerprint = work_fingerprint("WOW-Dashboard", "ENGINEERING_BACKLOG", ticket_id)
            existing = self._select_one("wow_engineering_audit_work_items", "fingerprint", fingerprint)
            comparable = {"status": status, "terminal_state": row.get("terminal_state"), "evidence_notes": row.get("evidence_notes")}
            changed = existing is None or comparable != (existing.get("state_payload") or {})
            if existing and not changed:
                progress_at = parse_timestamp(str(existing["last_meaningful_progress_at"]))
            else:
                opened_at = row.get("opened_at")
                progress_at = parse_timestamp(str(opened_at)) if opened_at else now
                if existing:
                    progress_at = now
            due_at = next_audit_at(progress_at, severity)
            payload = {
                "fingerprint": fingerprint,
                "source_system": "SUPABASE",
                "repository": "gregoryharper84-ship-it/WOW-Dashboard",
                "source_kind": "ENGINEERING_BACKLOG",
                "source_ref": ticket_id,
                "title": str(row.get("title") or ticket_id),
                "state": "TERMINAL" if terminal else "OPEN",
                "source_status": status,
                "severity": severity,
                "draft": False,
                "labels": [],
                "state_payload": comparable,
                "last_meaningful_progress_at": iso(progress_at),
                "expected_next_action": None if terminal else "Advance the canonical engineering ticket toward an allowed terminal state.",
                "next_audit_at": iso(due_at),
                "last_seen_at": iso(now),
                "closed_at": row.get("closed_at") if terminal else None,
                "terminal_state": row.get("terminal_state") if terminal else None,
                "can_execute": False,
                "terminal_authority": TERMINAL_AUTHORITY,
                "updated_at": iso(now),
            }
            self.client.table("wow_engineering_audit_work_items").upsert(payload, on_conflict="fingerprint").execute()
            if terminal:
                self.resolve_finding("STALE_WORK", fingerprint, resolution="BACKLOG_ITEM_CLOSED", now=now)
            elif changed:
                self.resolve_finding("STALE_WORK", fingerprint, resolution="BACKLOG_ITEM_PROGRESS_RESUMED", now=now)
            count += 1
        self.touch_runtime(last_reconcile_at=now)
        return count

    def _mirror_finding_to_backlog(self, finding: dict[str, Any], *, now: datetime) -> None:
        fingerprint = str(finding.get("fingerprint") or "")
        ticket_id = f"AUDIT-{fingerprint[:12].upper()}"
        finding_type = str(finding.get("finding_type") or "AUDIT_FINDING")
        severity = str(finding.get("severity") or "P3")
        component = str(finding.get("component") or "UNKNOWN")
        evidence = finding.get("evidence") or {}
        payload = {
            "ticket_id": ticket_id,
            "title": f"{finding_type}: {component}",
            "class": "B" if finding_type == "GOVERNANCE_DRIFT" else "A",
            "status": "OPEN",
            "terminal_state": None,
            "source": "ENGINEERING_AUDITOR",
            "priority": severity,
            "scope": component,
            "probability_impact": "NONE",
            "schema_behavior_change": "NONE",
            "constraint_dependency": "V17_TERMINAL_REDUCER; can_execute=false",
            "validation_environment": "WOW V17 continuous engineering auditor",
            "description": f"Continuous Engineering Auditor opened {finding_type} for {component}.",
            "evidence_notes": json.dumps(evidence, sort_keys=True, default=str)[:12000],
            "opened_at": finding.get("first_detected_at") or iso(now),
            "closed_at": None,
            "created_by": "wow_engineering_auditor",
        }
        self.client.table("wow_engineering_backlog").upsert(payload, on_conflict="ticket_id").execute()

    def _close_backlog_ticket(self, finding_fingerprint_value: str, *, now: datetime) -> None:
        ticket_id = f"AUDIT-{finding_fingerprint_value[:12].upper()}"
        self.client.table("wow_engineering_backlog").update(
            {"status": "CLOSED", "terminal_state": "FIXED_AND_VERIFIED", "closed_at": iso(now)}
        ).eq("ticket_id", ticket_id).execute()

    def touch_runtime(
        self,
        *,
        instance_id: str | None = None,
        status: str | None = None,
        started_at: datetime | None = None,
        last_heartbeat_at: datetime | None = None,
        last_event_processed_at: datetime | None = None,
        last_reconcile_at: datetime | None = None,
        last_error_code: str | None = None,
    ) -> None:
        now = utcnow()
        payload: dict[str, Any] = {
            "auditor_id": AUDITOR_ID,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
            "updated_at": iso(now),
        }
        optional = {
            "instance_id": instance_id,
            "status": status,
            "started_at": iso(started_at),
            "last_heartbeat_at": iso(last_heartbeat_at),
            "last_event_processed_at": iso(last_event_processed_at),
            "last_reconcile_at": iso(last_reconcile_at),
            "last_error_code": last_error_code,
        }
        payload.update({k: v for k, v in optional.items() if v is not None})
        self.client.table("wow_engineering_auditor_runtime").upsert(payload, on_conflict="auditor_id").execute()

    def refresh_runtime_counts(self, *, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        due = _rows(self.client.table("wow_engineering_audit_work_items").select("work_item_id").eq("state", "OPEN").lte("next_audit_at", iso(now)).execute())
        open_findings = _rows(self.client.table("wow_engineering_audit_findings").select("finding_id").eq("status", "OPEN").execute())
        queue = _rows(self.client.table("wow_engineering_audit_work_items").select("work_item_id").eq("state", "OPEN").execute())
        counts = {"queue_depth": len(queue), "open_finding_count": len(open_findings), "overdue_work_item_count": len(due)}
        self.client.table("wow_engineering_auditor_runtime").update({**counts, "updated_at": iso(now)}).eq("auditor_id", AUDITOR_ID).execute()
        return counts

    def health(self) -> dict[str, Any]:
        runtime = self._select_one("wow_engineering_auditor_runtime", "auditor_id", AUDITOR_ID) or {}
        return {
            "auditor_id": AUDITOR_ID,
            "auditor_version": AUDITOR_VERSION,
            "status": runtime.get("status") or "UNKNOWN",
            "last_heartbeat_at": runtime.get("last_heartbeat_at"),
            "last_event_processed_at": runtime.get("last_event_processed_at"),
            "last_reconcile_at": runtime.get("last_reconcile_at"),
            "queue_depth": int(runtime.get("queue_depth") or 0),
            "open_finding_count": int(runtime.get("open_finding_count") or 0),
            "overdue_work_item_count": int(runtime.get("overdue_work_item_count") or 0),
            "last_error_code": runtime.get("last_error_code"),
            "terminal_authority": TERMINAL_AUTHORITY,
            "probability_behavior_change": False,
            "can_execute": False,
        }
