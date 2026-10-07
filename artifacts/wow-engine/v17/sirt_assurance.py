"""SIRT Class A independent assurance: evidence-only, fail-closed, no probability authority.

Three policies: watchdog-of-watchdog health, exact-head lifecycle closure, and
confirmed-root-cause recurrence. This module never certifies a model or executes
a wager. It deliberately reuses existing auditor and postmortem records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
VERIFIER = "INDEPENDENT_VERIFICATION"
CAN_EXECUTE = False
ALLOWED_HEALTH = {"RUNNING"}
ALLOWED_PROBE = {"PASS", "HEALTHY"}
AUDITOR_ID = "WOW_ENGINEERING_AUDITOR"


def _time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def _now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("SIRT_CLOCK_MUST_BE_AWARE")
    return value.astimezone(timezone.utc)


def _age_seconds(now: datetime, timestamp: Any) -> float | None:
    parsed = _time(timestamp)
    if parsed is None or parsed > now + timedelta(minutes=2):
        return None
    return max(0.0, (now - parsed).total_seconds())


def _signal(kind: str, identity: str, severity: str, reason: str) -> dict[str, str]:
    return {"type": kind, "identity": identity, "severity": severity, "reason": reason}


def assess_sentinel(
    *,
    runtime: dict[str, Any] | None,
    work_items: list[dict[str, Any]] | None = None,
    handoffs: list[dict[str, Any]] | None = None,
    probes: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    heartbeat_max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Read only. An absent input cannot be interpreted as a healthy system."""
    observed = _now(now)
    signals: list[dict[str, str]] = []
    runtime = runtime or {}
    identity = str(runtime.get("auditor_id") or AUDITOR_ID)
    age = _age_seconds(observed, runtime.get("last_heartbeat_at"))
    if age is None:
        signals.append(_signal("AUDITOR_HEALTH", identity, "P0", "HEARTBEAT_UNVERIFIABLE"))
    elif age > heartbeat_max_age_seconds:
        signals.append(_signal("AUDITOR_HEALTH", identity, "P0", "HEARTBEAT_STALE"))
    if str(runtime.get("status") or "").upper() not in ALLOWED_HEALTH:
        signals.append(_signal("AUDITOR_HEALTH", identity, "P1", "AUDITOR_NOT_RUNNING"))
    if runtime.get("can_execute") is not False or runtime.get("terminal_authority") != TERMINAL_AUTHORITY:
        signals.append(_signal("GOVERNANCE_DRIFT", identity, "P0", "AUTHORITY_UNVERIFIED"))
    if work_items is not None:
        for row in work_items:
            if str(row.get("state") or "").upper() != "OPEN":
                continue
            severity = str(row.get("severity") or "P3").upper()
            due = _time(row.get("next_audit_at"))
            if due is None:
                signals.append(_signal("WORK_LIFECYCLE", str(row.get("fingerprint") or "UNKNOWN"), "P1", "DEADLINE_UNKNOWN"))
            elif due < observed and severity in {"P0", "P1"}:
                signals.append(_signal("WORK_LIFECYCLE", str(row.get("fingerprint") or "UNKNOWN"), severity, "WORK_OVERDUE"))
    if handoffs is not None:
        for row in handoffs:
            identity = str(row.get("handoff_id") or row.get("work_item_id") or "UNKNOWN")
            if not row.get("work_item_id") or not row.get("from_owner") or not row.get("to_owner"):
                signals.append(_signal("HANDOFF", identity, "P1", "HANDOFF_IDENTITY_INCOMPLETE"))
            elif str(row.get("status") or "").upper() not in {"ACCEPTED", "REJECTED", "COMPLETED"}:
                age = _age_seconds(observed, row.get("created_at"))
                if age is None or age > 900:
                    signals.append(_signal("HANDOFF", identity, "P1", "HANDOFF_UNACKNOWLEDGED"))
    if findings is not None:
        for row in findings:
            if str(row.get("status") or "").upper() != "OPEN":
                continue
            severity = str(row.get("severity") or "P3").upper()
            if severity in {"P0", "P1"}:
                signals.append(_signal(
                    "ACTIVE_AUDIT_FINDING", str(row.get("component") or "UNKNOWN"),
                    severity, "CRITICAL_FINDING_UNRESOLVED",
                ))
    if probes is not None:
        for row in probes:
            identity = str(row.get("probe_id") or "UNKNOWN")
            age = _age_seconds(observed, row.get("observed_at"))
            if age is None or age > 900 or not row.get("evidence_ref"):
                signals.append(_signal("PRODUCTION_EVIDENCE", identity, "P1", "PROBE_UNVERIFIABLE"))
            elif str(row.get("status") or "").upper() not in ALLOWED_PROBE:
                signals.append(_signal("PRODUCTION_EVIDENCE", identity, "P1", "PROBE_NOT_PASSING"))
    signals.sort(key=lambda x: (x["type"], x["identity"], x["reason"]))
    return {
        "status": "BLOCKED" if signals else "OBSERVED_HEALTHY",
        "scope": "SIRT_ASSURANCE_ONLY_NOT_PRODUCT_READINESS",
        "checked_at": observed.isoformat(),
        "signals": signals,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


CLOSURE_EVIDENCE = (
    "diagnosis_evidence_ref",
    "tests_evidence_ref",
    "review_evidence_ref",
    "certification_evidence_ref",
    "merge_evidence_ref",
    "deployment_evidence_ref",
    "production_acceptance_evidence_ref",
    "independent_verification_evidence_ref",
)


def assess_class_a_closure(receipt: dict[str, Any]) -> dict[str, Any]:
    """Proof gate only; SIRT never grants certification or terminal authority."""
    missing = [key.upper() + "_MISSING" for key in CLOSURE_EVIDENCE if not receipt.get(key)]
    sha = str(receipt.get("candidate_head_sha") or "")
    certified = str(receipt.get("certified_head_sha") or "")
    if not sha or sha != certified:
        missing.append("CERTIFICATION_HEAD_MISMATCH")
    base = str(receipt.get("current_base_sha") or "")
    if not base or base != str(receipt.get("certified_base_sha") or ""):
        missing.append("CERTIFICATION_BASE_STALE")
    merged = str(receipt.get("merge_commit_sha") or "")
    deployed = str(receipt.get("deployed_commit_sha") or "")
    if not merged or not deployed:
        missing.append("DEPLOYED_REVISION_UNVERIFIED")
    elif merged != deployed and not receipt.get("verified_ancestry_evidence_ref"):
        missing.append("DEPLOYED_ANCESTRY_UNVERIFIED")
    if receipt.get("independent_verifier") != VERIFIER:
        missing.append("INDEPENDENT_VERIFIER_MISSING")
    if receipt.get("implementation_owner") == receipt.get("verification_owner") or not receipt.get("verification_owner"):
        missing.append("VERIFICATION_INDEPENDENCE_UNPROVEN")
    if receipt.get("can_execute") is not False or receipt.get("terminal_authority") != TERMINAL_AUTHORITY:
        missing.append("GOVERNANCE_INVARIANT_UNVERIFIED")
    if receipt.get("production_acceptance_status") != "PASS":
        missing.append("PRODUCTION_ACCEPTANCE_NOT_PASS")
    if receipt.get("independent_verification_status") != "PASS":
        missing.append("INDEPENDENT_VERIFICATION_NOT_PASS")
    return {
        "status": "EVIDENCE_COMPLETE_PENDING_TERMINAL_AUTHORITY" if not missing else "CLOSURE_BLOCKED",
        "closure_eligible_for_independent_review": not missing,
        "blockers": sorted(set(missing)),
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def failure_families(records: list[dict[str, Any]], audit_findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Cluster only *confirmed* shared root cause IDs, never inferred titles."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unclassified: list[str] = []
    for row in records:
        ref = str(row.get("postmortem_id") or row.get("incident_id") or "")
        if not ref:
            continue
        cause = str(row.get("confirmed_root_cause_id") or row.get("root_cause_code") or "").strip()
        if str(row.get("root_cause_status") or "").upper() != "CONFIRMED" or not cause:
            unclassified.append(ref)
            continue
        grouped[cause].append(row)
    families = []
    for cause, group in sorted(grouped.items()):
        refs = sorted(str(r.get("postmortem_id") or r.get("incident_id")) for r in group)
        domains = sorted({str(r.get("primary_subsystem") or r.get("domain") or "UNKNOWN") for r in group})
        fingerprint = hashlib.sha256(("SIRT_FAILURE|" + cause).encode()).hexdigest()
        repeat = len(group) > 1
        families.append({
            "fingerprint": fingerprint,
            "confirmed_root_cause_id": cause,
            "incident_ids": refs,
            "occurrences": len(group),
            "affected_subsystems": domains,
            "recurring": repeat,
            "opportunity": "PREVENTIVE_ARCHITECTURE_REVIEW" if repeat else "CONTINUE_OBSERVATION",
            "classification": "EVIDENCE_BACKED_NO_AUTOMATIC_CLOSURE",
        })
    # Symptom cohorts are NOT confirmed root cause; do not merge their identities.
    cohorts: dict[tuple[str, str], int] = defaultdict(int)
    for finding in audit_findings or []:
        if str(finding.get("status") or "").upper() == "OPEN":
            key = (str(finding.get("finding_type") or "UNKNOWN"),
                   str(finding.get("severity") or "UNKNOWN"))
            cohorts[key] += 1
    symptoms = [
        {"finding_type": typ, "severity": sev, "open_count": count,
         "classification": "SYMPTOM_CLUSTER_ROOT_CAUSE_UNPROVEN"}
        for (typ, sev), count in sorted(cohorts.items())
    ]
    return {
        "families": families,
        "open_symptom_cohorts": symptoms,
        "unclassified_incident_ids": sorted(unclassified),
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def read_auditor_runtime(url: str, key: str, *, timeout_seconds: int = 8) -> dict[str, Any]:
    """Independent external health probe; credentials are never printed."""
    base = url.rstrip("/")
    if not base.startswith("https://") or not key:
        raise ValueError("SIRT_MONITOR_CONFIGURATION_MISSING")
    query = quote(AUDITOR_ID, safe="")
    endpoint = base + "/rest/v1/wow_engineering_auditor_runtime?auditor_id=eq." + query + "&select=auditor_id,status,last_heartbeat_at,last_error_code,can_execute,terminal_authority"
    request = Request(endpoint, headers={
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
        "User-Agent": "wow-sirt-class-a-monitor",
    })
    with urlopen(request, timeout=timeout_seconds) as response:
        rows = json.load(response)
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError("SIRT_AUDITOR_RUNTIME_RECEIPT_UNAVAILABLE")
    return rows[0]


def read_audit_rows(url: str, key: str, *, kind: str, timeout_seconds: int = 8) -> list[dict[str, Any]]:
    """Bounded, service-role-only read of existing auditor tables; no mutations."""
    queries = {
        "work_items": ("wow_engineering_audit_work_items",
                       "fingerprint,state,severity,next_audit_at", "state=eq.OPEN"),
        "findings": ("wow_engineering_audit_findings",
                     "finding_type,status,severity,component", "status=eq.OPEN"),
    }
    if kind not in queries or not url.startswith("https://") or not key:
        raise ValueError("SIRT_MONITOR_CONFIGURATION_MISSING")
    table, columns, filter_expr = queries[kind]
    endpoint = url.rstrip("/") + "/rest/v1/" + table + "?select=" + columns + "&" + filter_expr + "&limit=1000"
    request = Request(endpoint, headers={
        "apikey": key, "Authorization": "Bearer " + key,
        "Accept": "application/json", "User-Agent": "wow-sirt-class-a-monitor",
    })
    with urlopen(request, timeout=timeout_seconds) as response:
        rows = json.load(response)
    if not isinstance(rows, list) or len(rows) >= 1000 or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("SIRT_MONITOR_AUDIT_ROWS_INCOMPLETE")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="WOW SIRT evidence-only assurance")
    parser.add_argument("mode", choices=("sentinel", "failures"))
    parser.add_argument("--incident-ledger", default=str(Path(__file__).with_name("incident-ledger.json")))
    args = parser.parse_args()
    if args.mode == "failures":
        try:
            data = json.loads(Path(args.incident_ledger).read_text(encoding="utf-8"))
            url = os.getenv("SUPABASE_URL", "")
            key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", "")
            findings = None
            if url and key:
                try:
                    findings = read_audit_rows(url, key, kind="findings")
                except (ValueError, RuntimeError, HTTPError, URLError, TimeoutError, OSError):
                    # Do not infer an empty cohort from unreachable persistent evidence.
                    findings = None
            result = failure_families(data["records"], findings)
            result["live_symptom_cohort_source"] = "OBSERVED" if findings is not None else "UNVERIFIED"
        except (OSError, ValueError, KeyError, TypeError):
            print(json.dumps({"status": "SIRT_FAILURE_LEDGER_UNAVAILABLE", "can_execute": False}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    try:
        runtime = read_auditor_runtime(
            os.getenv("SUPABASE_URL", ""),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", ""),
        )
        work_items = read_audit_rows(os.getenv("SUPABASE_URL", ""),
                                     os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", ""),
                                     kind="work_items")
        findings = read_audit_rows(os.getenv("SUPABASE_URL", ""),
                                   os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", ""),
                                   kind="findings")
        result = assess_sentinel(runtime=runtime, work_items=work_items, findings=findings)
    except (ValueError, RuntimeError, HTTPError, URLError, TimeoutError, OSError) as exc:
        # No URLs, credentials, or request bodies in diagnostic output.
        result = {"status": "BLOCKED", "reason": (
            "SIRT_MONITOR_CONFIGURATION_MISSING" if isinstance(exc, ValueError)
            else "SIRT_MONITOR_PROBE_UNAVAILABLE"
        ), "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY}
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "OBSERVED_HEALTHY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
