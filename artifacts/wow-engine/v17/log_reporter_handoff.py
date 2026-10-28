"""Read-only authenticated-source preflight for existing WOW Reporter intake.

The GitHub/Render/Supabase supervisor (never raw log text) supplies the source
run attestation and ledger snapshot. This pure planner neither grants a lease
nor writes GitHub, Redis, Supabase, a pull request, or a wager. It deliberately
refuses to turn a *candidate* into an authorized engineering dispatch.
"""
from __future__ import annotations
import hashlib
import json
import re
from typing import Any, Mapping

from v17.log_incident_intake import SERVICE, ALLOWED_TYPED_CODES

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
WORKFLOW = "wow-v17-log-incident-intake.yml"
ARTIFACT = "wow-v17-log-incident-candidates"
SHA = re.compile(r"^[0-9a-f]{40}$")
HEX = re.compile(r"^[0-9a-f]{64}$")
ROUTES = frozenset({"ROOT_PROBE", "HEALTH_PROBE", "SCOUT_HANDOFF",
                    "INTERACTIVE_SCORE", "OTHER_ROUTE", "PICKEM_QUEUE_CLAIM",
                    "PROP_SELF_ACCEPTANCE", "FITTED_MODEL", "PERSISTENCE"})
FAILURES = ALLOWED_TYPED_CODES | frozenset({"HTTP_5XX", "HTTP_401", "HTTP_422"})


def _hold(reason: str) -> dict[str, Any]:
    return {"status": "HELD", "reason": reason, "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER", "dispatch_authorized": False}


def plan_reporter_handoff(
    report: Mapping[str, Any], *, provenance: Mapping[str, Any],
    protected_main_sha: str, known_fingerprints: Mapping[str, int],
    approved_issue_ids: frozenset[int], issue_states: Mapping[int, str],
) -> dict[str, Any]:
    """Return validated candidate re-entry decisions, not worker commands.

    Caller MUST fetch provenance and ledger/issue states through authenticated
    sources, not accept them from the artifact or an LLM.
    """
    if not SHA.fullmatch(protected_main_sha):
        return _hold("MAIN_SHA_UNVERIFIED")
    if (provenance.get("repository") != REPO
            or provenance.get("workflow") != WORKFLOW
            or provenance.get("artifact_name") != ARTIFACT
            or provenance.get("branch") != "main"
            or provenance.get("head_sha") != protected_main_sha
            or provenance.get("status") != "completed"
            or provenance.get("conclusion") != "success"
            or type(provenance.get("run_id")) is not int
            or provenance["run_id"] <= 0):
        return _hold("SOURCE_PROVENANCE_UNVERIFIED")
    if (report.get("source") != "DATADOG" or report.get("service") != SERVICE
            or report.get("status") not in {"CANDIDATES", "NO_ACTION"}
            or report.get("can_execute") is not False
            or report.get("terminal_authority") != "V17_TERMINAL_REDUCER"
            or report.get("DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS") is not True
            or report.get("needs_durable_reporter_dedupe") is not True):
        return _hold("REPORTER_PACKET_INVALID")
    findings = report.get("findings")
    count = report.get("events_inspected")
    if (not isinstance(findings, list) or len(findings) > 100
            or type(count) is not int or count < 0 or count > 1000):
        return _hold("REPORTER_PACKET_INVALID")
    if (report["status"] == "NO_ACTION" and findings
            or report["status"] == "CANDIDATES" and not findings):
        return _hold("REPORTER_PACKET_INVALID")

    decisions: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for row in findings:
        if not isinstance(row, Mapping):
            return _hold("CANDIDATE_INVALID")
        route, code = row.get("failure_boundary"), row.get("typed_failure")
        if (row.get("source") != "DATADOG" or row.get("service") != SERVICE
                or route not in ROUTES or code not in FAILURES
                or row.get("can_execute") is not False
                or row.get("state") != "CANDIDATE_UNVERIFIED"
                or row.get("next_owner") != "REPORTER_AGENT"
                or row.get("severity") not in {"P1", "P2"}):
            return _hold("CANDIDATE_INVALID")
        fingerprint = row.get("fingerprint")
        expected = hashlib.sha256(json.dumps([SERVICE, route, code],
                                             separators=(",", ":")).encode()).hexdigest()
        if not isinstance(fingerprint, str) or not HEX.fullmatch(fingerprint) or fingerprint != expected:
            return _hold("CANDIDATE_FINGERPRINT_INVALID")
        occurrences = row.get("count")
        if type(occurrences) is not int or not 1 <= occurrences <= count:
            return _hold("CANDIDATE_COUNT_INVALID")
        if fingerprint in fingerprints:
            return _hold("DUPLICATED_CANDIDATE")
        fingerprints.add(fingerprint)
        issue = known_fingerprints.get(fingerprint)
        if issue is None:
            decisions.append({"fingerprint": fingerprint, "state": "REPORTER_INTAKE_REQUIRED",
                              "owner": "REPORTER_AGENT", "issue_number": None,
                              "dispatch_authorized": False})
            continue
        if type(issue) is not int or issue <= 0:
            return _hold("INCIDENT_ID_INVALID")
        state = issue_states.get(issue)
        if state not in {"open", "closed"}:
            return _hold("INCIDENT_STATE_UNVERIFIED")
        if state == "closed":
            stage = "RECURRENCE_REOPEN_REQUIRED"
        elif issue not in approved_issue_ids:
            stage = "EXISTING_INCIDENT_NOT_ADMITTED"
        else:
            stage = "EXISTING_APPROVED_ISSUE_REVIEW"
        decisions.append({"fingerprint": fingerprint, "state": stage,
                          "owner": "REPORTER_AGENT", "issue_number": issue,
                          "dispatch_authorized": False})
    return {"status": "REPORTER_PRECHECK" if decisions else "NO_ACTION",
            "run_id": provenance["run_id"], "decisions": decisions,
            "can_execute": False, "dispatch_authorized": False,
            "terminal_authority": "V17_TERMINAL_REDUCER"}
