#!/usr/bin/env python3
"""Recover orphaned QA App content-ledger reservations without weakening gates.

A cancelled, killed, timed-out or disconnected reviewer can leave a content
digest reserved as in_progress. This worker, using the separate QA App
identity, may complete that exact reservation as HOLD or (if a semantic
REJECT was already received) REJECT. It never publishes PASS, never deletes
check history, and fails closed when provenance or the provider decision
cannot be established.

Designed as a sibling of .github/scripts/wow_simple_security_review.py so
Engineering can integrate it with PR #2275 without rewriting that branch.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

_REVIEW_PATH = Path(__file__).resolve().parent / "wow_simple_security_review.py"
_SPEC = importlib.util.spec_from_file_location("wow_simple_security_review", _REVIEW_PATH)
review = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(review)
Hold = review.Hold

REPO = review.REPO
QA_APP_ID = review.QA_APP_ID
CHECK_NAME = review.CHECK_NAME
LEDGER_SENTINEL_SHA = getattr(
    review, "LEDGER_SENTINEL_SHA", "79fa0c275a58cd9d397c5a1bc81d641b6dad7f75")
LEDGER_NAME_PREFIX = getattr(
    review, "LEDGER_NAME_PREFIX", "WOW Semantic Content Ledger ")
REVIEW_WORKFLOW = "wow-v17-simple-security-review"
REVIEW_WORKFLOW_PATH = ".github/workflows/wow-v17-simple-security-review.yml"
REVIEW_STEP_NAME = "Review exact PR diff and publish QA App check"
RESERVATION_MARK = "WOW_LEDGER_RESERVATION_V1"
LEASE_MARK = "WOW_LEDGER_RECOVERY_LEASE_V1"
RECEIPT_MARK = "WOW_LEDGER_RECOVERY_V1"
DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
INT_RE = re.compile(r"^[1-9][0-9]{0,18}$")
RETRYABLE_GITHUB = {408, 429, 500, 502, 503, 504}
MAX_PATCH_ATTEMPTS = 3
MAX_SCAN_PAGES = 10
MAX_RECOVERIES = 20
LEASE_TTL_SECONDS = 600
TERMINAL_RUN = frozenset({"cancelled", "failure", "timed_out", "startup_failure"})
ALLOWED_RECOVERY_DECISIONS = frozenset({"HOLD", "REJECT"})


class NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


HTTP_CLIENT = build_opener(NoCredentialRedirect)


def request_json(url, *, token=None, data=None, headers=None, method=None):
    if not url.startswith("https://api.github.com/repos/" + REPO + "/"):
        raise Hold("REVIEW_ENDPOINT_UNTRUSTED")
    check_patch = re.fullmatch(
        r"https://api.github.com/repos/" + re.escape(REPO) + r"/check-runs/[0-9]+", url)
    if method is None:
        if data is not None:
            raise Hold("REVIEW_HTTP_METHOD_UNTRUSTED")
    elif method != "PATCH" or data is None or not check_patch:
        raise Hold("REVIEW_HTTP_METHOD_UNTRUSTED")
    h = {"Accept": "application/vnd.github+json", "User-Agent": "wow-qa-ledger-recovery-v1"}
    if headers:
        h.update(headers)
    if token:
        h["Authorization"] = "Bearer " + token
    body = None if data is None else json.dumps(data, separators=(",", ":")).encode()
    if body is not None:
        h["Content-Type"] = "application/json"
    try:
        with HTTP_CLIENT.open(Request(url, data=body, headers=h, method=method), timeout=60) as response:
            raw = response.read(250000)
        return json.loads(raw)
    except HTTPError as exc:
        raise Hold("GITHUB_HTTP_" + str(exc.code)) from None
    except (URLError, TimeoutError):
        raise Hold("GITHUB_TRANSPORT") from None
    except (ValueError, UnicodeError):
        raise Hold("RESPONSE_JSON_INVALID") from None


def gh(path, token, *, data=None, method=None):
    return request_json("https://api.github.com/repos/" + REPO + path,
                        token=token, data=data, method=method)


def patch_check_run(check_run_id, qa_token, payload, *, attempts=MAX_PATCH_ATTEMPTS):
    if type(check_run_id) is not int or check_run_id <= 0:
        raise Hold("LEDGER_RECOVERY_CHECK_ID_INVALID")
    if not qa_token:
        raise Hold("CONTENT_LEDGER_QA_APP_TOKEN_MISSING")
    last = None
    url = "https://api.github.com/repos/" + REPO + f"/check-runs/{check_run_id}"
    for _ in range(attempts):
        try:
            result = request_json(url, token=qa_token, data=payload, method="PATCH")
            app = result.get("app") if type(result) is dict else None
            if type(app) is not dict or app.get("id") != QA_APP_ID:
                raise Hold("LEDGER_RECOVERY_QA_APP_IDENTITY_UNVERIFIED")
            if result.get("id") != check_run_id:
                raise Hold("LEDGER_RECOVERY_CHECK_ID_MISMATCH")
            return result
        except Hold as exc:
            last = exc
            if str(exc) not in {"GITHUB_HTTP_408", "GITHUB_HTTP_429", "GITHUB_HTTP_500",
                                "GITHUB_HTTP_502", "GITHUB_HTTP_503", "GITHUB_HTTP_504",
                                "GITHUB_TRANSPORT"}:
                raise
            time.sleep(0)
    raise last


def _require_token_pair(read_token, qa_token):
    if not read_token:
        raise Hold("LEDGER_RECOVERY_READ_TOKEN_MISSING")
    if not qa_token:
        raise Hold("CONTENT_LEDGER_QA_APP_TOKEN_MISSING")
    if qa_token == read_token:
        raise Hold("LEDGER_RECOVERY_QA_APP_TOKEN_NOT_DISTINCT")


def _parse_block(text, mark):
    if type(text) is not str or not text:
        return None
    lines = text.splitlines()
    try:
        start = lines.index(mark)
    except ValueError:
        return None
    values = {}
    for line in lines[start + 1:]:
        if not line or line.startswith("WOW_"):
            break
        if "=" not in line:
            raise Hold("LEDGER_RECOVERY_EVIDENCE_MALFORMED")
        key, value = line.split("=", 1)
        if not key or key in values or not re.fullmatch(r"[a-z_]+", key):
            raise Hold("LEDGER_RECOVERY_EVIDENCE_MALFORMED")
        values[key] = value
    return values


def _format_block(mark, values):
    body = "\n".join(f"{key}={values[key]}" for key in values)
    return mark + "\n" + body + "\n"


def reservation_evidence(target, reservation, *, run_id, run_attempt, actor,
                         provider_invoked=False, semantic_decision="none"):
    digest = reservation.get("digest")
    check_id = reservation.get("id")
    if (type(digest) is not str or not DIGEST_RE.fullmatch(digest)
            or type(check_id) is not int or check_id <= 0):
        raise Hold("LEDGER_RECOVERY_RESERVATION_IDENTITY_INVALID")
    pr = target.get("pr")
    head = target.get("head_sha")
    base = target.get("base_sha")
    if (type(pr) is not int or pr <= 0 or type(head) is not str or not SHA_RE.fullmatch(head)
            or type(base) is not str or not SHA_RE.fullmatch(base)):
        raise Hold("LEDGER_RECOVERY_TARGET_IDENTITY_INVALID")
    if type(actor) is not str or not actor or len(actor) > 100 or "\n" in actor:
        raise Hold("LEDGER_RECOVERY_ACTOR_INVALID")
    if type(run_id) is not int or run_id <= 0 or type(run_attempt) is not int or run_attempt <= 0:
        raise Hold("LEDGER_RECOVERY_RUN_IDENTITY_INVALID")
    if semantic_decision not in {"none", "HOLD", "REJECT", "PASS"}:
        raise Hold("LEDGER_RECOVERY_DECISION_MARK_INVALID")
    return {
        "digest": digest,
        "check_run_id": str(check_id),
        "pr": str(pr),
        "head": head,
        "base": base,
        "run_id": str(run_id),
        "run_attempt": str(run_attempt),
        "actor": actor,
        "workflow": REVIEW_WORKFLOW,
        "provider_invoked": "true" if provider_invoked else "false",
        "semantic_decision": semantic_decision,
    }


def attach_reservation_evidence(reservation, target, *, run_id, run_attempt, actor,
                                qa_token, provider_invoked=False, semantic_decision="none"):
    """Persist exact-SHA provenance onto the in_progress reservation. Never completes it."""
    evidence = reservation_evidence(
        target, reservation, run_id=run_id, run_attempt=run_attempt, actor=actor,
        provider_invoked=provider_invoked, semantic_decision=semantic_decision)
    payload = {"status": "in_progress",
               "output": {"title": "IN_PROGRESS — content review reservation",
                          "summary": _format_block(RESERVATION_MARK, evidence)[:65000]}}
    result = patch_check_run(reservation["id"], qa_token, payload)
    if result.get("status") != "in_progress":
        raise Hold("LEDGER_RECOVERY_RESERVATION_STATUS_CHANGED")
    return evidence


def parse_reservation_evidence(summary):
    raw = _parse_block(summary, RESERVATION_MARK)
    if raw is None:
        raise Hold("LEDGER_RECOVERY_PROVENANCE_UNVERIFIED")
    required = ("digest", "check_run_id", "pr", "head", "base", "run_id",
                "run_attempt", "actor", "workflow", "provider_invoked", "semantic_decision")
    if set(raw) != set(required):
        raise Hold("LEDGER_RECOVERY_PROVENANCE_UNVERIFIED")
    if (not DIGEST_RE.fullmatch(raw["digest"]) or not INT_RE.fullmatch(raw["check_run_id"])
            or not INT_RE.fullmatch(raw["pr"]) or not SHA_RE.fullmatch(raw["head"])
            or not SHA_RE.fullmatch(raw["base"]) or not INT_RE.fullmatch(raw["run_id"])
            or not INT_RE.fullmatch(raw["run_attempt"]) or not raw["actor"]
            or raw["workflow"] != REVIEW_WORKFLOW
            or raw["provider_invoked"] not in {"true", "false"}
            or raw["semantic_decision"] not in {"none", "HOLD", "REJECT", "PASS"}):
        raise Hold("LEDGER_RECOVERY_PROVENANCE_UNVERIFIED")
    return raw


def parse_lease(summary):
    raw = _parse_block(summary, LEASE_MARK)
    if raw is None:
        return None
    if set(raw) != {"lease_run_id", "lease_attempt", "leased_at", "lease_seq"}:
        raise Hold("LEDGER_RECOVERY_LEASE_MALFORMED")
    if (not INT_RE.fullmatch(raw["lease_run_id"]) or not INT_RE.fullmatch(raw["lease_attempt"])
            or not INT_RE.fullmatch(raw["lease_seq"])):
        raise Hold("LEDGER_RECOVERY_LEASE_MALFORMED")
    return raw


def _utc_parse(value):
    if type(value) is not str or not value:
        raise Hold("LEDGER_RECOVERY_TIMESTAMP_UNVERIFIED")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise Hold("LEDGER_RECOVERY_TIMESTAMP_UNVERIFIED")


def _now():
    return datetime.now(timezone.utc)


def verify_ledger_check(run, digest=None):
    if type(run) is not dict:
        raise Hold("CONTENT_LEDGER_CHECK_INVALID")
    app = run.get("app")
    name = run.get("name")
    if type(app) is not dict or app.get("id") != QA_APP_ID:
        raise Hold("LEDGER_RECOVERY_QA_APP_IDENTITY_UNVERIFIED")
    if type(name) is not str or not name.startswith(LEDGER_NAME_PREFIX):
        raise Hold("CONTENT_LEDGER_PROVENANCE_INVALID")
    extracted = name[len(LEDGER_NAME_PREFIX):]
    if not DIGEST_RE.fullmatch(extracted):
        raise Hold("CONTENT_LEDGER_PROVENANCE_INVALID")
    if digest is not None and extracted != digest:
        raise Hold("CONTENT_LEDGER_PROVENANCE_INVALID")
    if (run.get("head_sha") != LEDGER_SENTINEL_SHA
            or run.get("external_id") != extracted
            or type(run.get("id")) is not int or run["id"] <= 0):
        raise Hold("CONTENT_LEDGER_PROVENANCE_INVALID")
    return extracted


def load_check(check_run_id, read_token):
    if type(check_run_id) is not int or check_run_id <= 0:
        raise Hold("LEDGER_RECOVERY_CHECK_ID_INVALID")
    result = gh(f"/check-runs/{check_run_id}", read_token)
    verify_ledger_check(result)
    return result


def load_source_run(run_id, read_token):
    result = gh(f"/actions/runs/{run_id}", read_token)
    if (type(result) is not dict or result.get("id") != run_id
            or result.get("path") != REVIEW_WORKFLOW_PATH
            or result.get("name") != REVIEW_WORKFLOW):
        raise Hold("LEDGER_RECOVERY_SOURCE_RUN_UNVERIFIED")
    return result


def load_review_step(run_id, read_token):
    result = gh(f"/actions/runs/{run_id}/jobs?per_page=100", read_token)
    jobs = result.get("jobs") if type(result) is dict else None
    if type(jobs) is not list or not jobs or len(jobs) > 100:
        raise Hold("LEDGER_RECOVERY_JOBS_UNVERIFIED")
    steps = []
    for job in jobs:
        if type(job) is not dict or type(job.get("steps")) is not list:
            raise Hold("LEDGER_RECOVERY_JOBS_UNVERIFIED")
        steps.extend(job["steps"])
    named = [step for step in steps if type(step) is dict
             and step.get("name") == REVIEW_STEP_NAME]
    if len(named) != 1:
        raise Hold("LEDGER_RECOVERY_REVIEW_STEP_UNVERIFIED")
    return named[0]


def prior_semantic_reject_on_sha(sha, read_token):
    if not SHA_RE.fullmatch(sha):
        raise Hold("LEDGER_RECOVERY_HEAD_INVALID")
    path = (f"/commits/{sha}/check-runs?check_name=WOW%20Independent%20Security%20Review"
            "&filter=all&per_page=100")
    for page in range(1, 11):
        data = gh(path + f"&page={page}", read_token)
        if (type(data) is not dict or type(data.get("check_runs")) is not list
                or type(data.get("total_count")) is not int or data["total_count"] < 0):
            raise Hold("PRIOR_REVIEW_CHECKS_INVALID")
        for run in data["check_runs"]:
            app = (run.get("app") or {}) if type(run) is dict else {}
            title = str(((run.get("output") or {}) if type(run) is dict else {}).get("title", ""))
            if (app.get("id") == QA_APP_ID and run.get("name") == CHECK_NAME
                    and run.get("conclusion") == "failure" and title.startswith("REJECT")):
                return True
        if page * 100 >= data["total_count"]:
            return False
    raise Hold("PRIOR_REVIEW_CHECKS_UNBOUNDED")


def sibling_ledger_reject(digest, read_token, *, ignore_id=None):
    name = LEDGER_NAME_PREFIX + digest
    path = (f"/commits/{LEDGER_SENTINEL_SHA}/check-runs?"
            f"check_name=WOW%20Semantic%20Content%20Ledger%20{digest}&filter=all&per_page=100")
    total = None
    for page in range(1, MAX_SCAN_PAGES + 1):
        result = gh(path + f"&page={page}", read_token)
        if (type(result) is not dict or type(result.get("total_count")) is not int
                or result["total_count"] < 0 or result["total_count"] > 1000
                or type(result.get("check_runs")) is not list
                or (total is not None and total != result["total_count"])):
            raise Hold("CONTENT_LEDGER_INVENTORY_UNTRUSTED")
        total = result["total_count"]
        for run in result["check_runs"]:
            if type(run) is not dict:
                raise Hold("CONTENT_LEDGER_CHECK_INVALID")
            app = run.get("app")
            if type(app) is not dict or app.get("id") != QA_APP_ID:
                continue
            if run.get("name") != name:
                continue
            if ignore_id is not None and run.get("id") == ignore_id:
                continue
            if (run.get("status") == "completed" and run.get("conclusion") == "failure"
                    and ((run.get("output") or {}).get("title") == "REJECT — content review")):
                return True
        if page * 100 >= total:
            return False
    raise Hold("CONTENT_LEDGER_UNBOUNDED")


def decision_from_evidence(check, evidence, *, read_token):
    """Return HOLD or REJECT. Never PASS. Raise Hold when the decision is unproven."""
    if evidence["semantic_decision"] == "REJECT":
        return "REJECT", "CONFIRMED_SEMANTIC_REJECT"
    if evidence["semantic_decision"] == "PASS":
        # A stale reservation must never be converted automatically to PASS.
        pass
    source = load_source_run(int(evidence["run_id"]), read_token)
    if source.get("status") != "completed":
        raise Hold("LEDGER_RECOVERY_SOURCE_RUN_STILL_ACTIVE")
    if source.get("conclusion") not in TERMINAL_RUN:
        raise Hold("LEDGER_RECOVERY_SOURCE_RUN_NOT_FAILED")
    if prior_semantic_reject_on_sha(evidence["head"], read_token):
        return "REJECT", "CONFIRMED_SEMANTIC_REJECT_PR_CHECK"
    if sibling_ledger_reject(evidence["digest"], read_token, ignore_id=check["id"]):
        return "REJECT", "CONFIRMED_SEMANTIC_REJECT_LEDGER_SIBLING"
    step = load_review_step(int(evidence["run_id"]), read_token)
    invoked = evidence["provider_invoked"] == "true"
    step_started = bool(step.get("started_at"))
    if evidence["semantic_decision"] in {"none", "HOLD"} and not invoked and not step_started:
        return "HOLD", "SOURCE_RUN_CANCELLED_BEFORE_PROVIDER"
    if evidence["semantic_decision"] in {"none", "HOLD"} and not invoked:
        return "HOLD", "SOURCE_RUN_CANCELLED_AFTER_RESERVE_BEFORE_PROVIDER"
    raise Hold("LEDGER_RECOVERY_PROVIDER_DECISION_UNPROVEN")


def _summary_from(check):
    output = check.get("output")
    if output is None:
        return ""
    if type(output) is not dict:
        raise Hold("LEDGER_RECOVERY_OUTPUT_UNTRUSTED")
    summary = output.get("summary") or ""
    if type(summary) is not str:
        raise Hold("LEDGER_RECOVERY_OUTPUT_UNTRUSTED")
    return summary


def _retain_original_summary(summary):
    parts = []
    for mark in (RESERVATION_MARK, LEASE_MARK, RECEIPT_MARK):
        raw = _parse_block(summary, mark)
        if raw is None:
            continue
        if mark == RECEIPT_MARK:
            continue
        parts.append(_format_block(mark, raw).rstrip())
    return "\n".join(parts)


def _idempotent_receipt(summary, *, check_run_id, recovery_run_id):
    raw = _parse_block(summary, RECEIPT_MARK)
    if raw is None:
        return None
    if (raw.get("check_run_id") == str(check_run_id)
            and raw.get("recovery_run_id") == str(recovery_run_id)
            and raw.get("decision") in ALLOWED_RECOVERY_DECISIONS):
        return raw
    return None


def acquire_lease(check, evidence, *, qa_token, read_token, recovery_run_id, recovery_attempt):
    summary = _summary_from(check)
    existing = parse_lease(summary)
    if existing is not None:
        other_id = int(existing["lease_run_id"])
        if other_id != recovery_run_id:
            try:
                other = gh(f"/actions/runs/{other_id}", read_token)
            except Hold:
                other = None
            leased_at = _utc_parse(existing["leased_at"])
            age = (_now() - leased_at).total_seconds()
            still_active = (type(other) is dict and other.get("status") in {"queued", "in_progress"})
            if still_active or age < LEASE_TTL_SECONDS:
                raise Hold("LEDGER_RECOVERY_LEASE_HELD")
        seq = int(existing["lease_seq"]) + 1
    else:
        seq = 1
    reservation_block = _format_block(RESERVATION_MARK, evidence).rstrip()
    lease = {
        "lease_run_id": str(recovery_run_id),
        "lease_attempt": str(recovery_attempt),
        "leased_at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lease_seq": str(seq),
    }
    payload = {"status": "in_progress",
               "output": {"title": "IN_PROGRESS — content review recovery lease",
                          "summary": (reservation_block + "\n" +
                                      _format_block(LEASE_MARK, lease))[:65000]}}
    patched = patch_check_run(check["id"], qa_token, payload)
    if patched.get("status") != "in_progress":
        raise Hold("LEDGER_RECOVERY_LEASE_LOST")
    live = load_check(check["id"], read_token)
    if live.get("status") != "in_progress":
        raise Hold("LEDGER_RECOVERY_LEASE_LOST")
    observed = parse_lease(_summary_from(live))
    if (observed is None or observed["lease_run_id"] != str(recovery_run_id)
            or observed["lease_seq"] != str(seq)):
        raise Hold("LEDGER_RECOVERY_LEASE_HELD")
    return lease


def complete_reservation(check, evidence, *, decision, reason, qa_token,
                         recovery_run_id, recovery_attempt, lease, idempotent=False):
    if decision not in ALLOWED_RECOVERY_DECISIONS:
        raise Hold("LEDGER_RECOVERY_PASS_FORBIDDEN")
    conclusions = {"HOLD": "neutral", "REJECT": "failure"}
    receipt = {
        "decision": decision,
        "reason": reason,
        "check_run_id": str(check["id"]),
        "digest": evidence["digest"],
        "pr": evidence["pr"],
        "head": evidence["head"],
        "base": evidence["base"],
        "original_run_id": evidence["run_id"],
        "original_run_attempt": evidence["run_attempt"],
        "original_actor": evidence["actor"],
        "recovery_run_id": str(recovery_run_id),
        "recovery_attempt": str(recovery_attempt),
        "lease_run_id": lease["lease_run_id"],
        "semantic_reject_observed": "true" if decision == "REJECT" else "false",
        "idempotent": "true" if idempotent else "false",
        "can_execute": "false",
    }
    retained = _retain_original_summary(_summary_from(check))
    summary = (retained + "\n" + _format_block(RECEIPT_MARK, receipt))[:65000]
    payload = {"status": "completed", "conclusion": conclusions[decision],
               "output": {"title": decision + " — content review", "summary": summary}}
    result = patch_check_run(check["id"], qa_token, payload)
    if (result.get("status") != "completed"
            or result.get("conclusion") != conclusions[decision]
            or (result.get("output") or {}).get("title") != decision + " — content review"):
        raise Hold("LEDGER_RECOVERY_COMMIT_UNVERIFIED")
    return receipt, result


def recover_check(check_run_id, *, read_token, qa_token, recovery_run_id, recovery_attempt):
    _require_token_pair(read_token, qa_token)
    if type(recovery_run_id) is not int or recovery_run_id <= 0:
        raise Hold("LEDGER_RECOVERY_RUN_IDENTITY_INVALID")
    if type(recovery_attempt) is not int or recovery_attempt <= 0:
        raise Hold("LEDGER_RECOVERY_RUN_IDENTITY_INVALID")
    check = load_check(check_run_id, read_token)
    digest = verify_ledger_check(check)
    evidence = parse_reservation_evidence(_summary_from(check))
    if evidence["digest"] != digest or int(evidence["check_run_id"]) != check["id"]:
        raise Hold("LEDGER_RECOVERY_PROVENANCE_UNVERIFIED")
    status = check.get("status")
    title = ((check.get("output") or {}) if type(check.get("output")) is dict else {}).get("title")
    if status == "completed":
        if check.get("conclusion") == "failure" and title == "REJECT — content review":
            return {"decision": "REJECT", "reason": "RETAINED_SEMANTIC_REJECT",
                    "check_run_id": str(check["id"]), "digest": digest,
                    "idempotent": "true", "can_execute": "false"}
        if check.get("conclusion") == "success" or (type(title) is str and title.startswith("PASS")):
            raise Hold("LEDGER_RECOVERY_WILL_NOT_TOUCH_PASS")
        duplicate = _idempotent_receipt(_summary_from(check), check_run_id=check["id"],
                                        recovery_run_id=recovery_run_id)
        if duplicate is not None:
            duplicate = dict(duplicate)
            duplicate["reason"] = "DUPLICATE_RECOVERY_EVENT"
            duplicate["idempotent"] = "true"
            return duplicate
        if check.get("conclusion") == "neutral" and title == "HOLD — content review":
            return {"decision": "HOLD", "reason": "ALREADY_COMPLETED_HOLD",
                    "check_run_id": str(check["id"]), "digest": digest,
                    "idempotent": "true", "can_execute": "false"}
        raise Hold("LEDGER_RECOVERY_COMPLETED_STATE_UNTRUSTED")
    if status != "in_progress":
        raise Hold("LEDGER_RECOVERY_STATUS_UNTRUSTED")
    decision, reason = decision_from_evidence(check, evidence, read_token=read_token)
    if decision == "PASS":
        raise Hold("LEDGER_RECOVERY_PASS_FORBIDDEN")
    lease = acquire_lease(check, evidence, qa_token=qa_token, read_token=read_token,
                          recovery_run_id=recovery_run_id, recovery_attempt=recovery_attempt)
    live = load_check(check_run_id, read_token)
    if live.get("status") == "completed":
        if live.get("conclusion") == "failure":
            return {"decision": "REJECT", "reason": "RETAINED_SEMANTIC_REJECT",
                    "check_run_id": str(live["id"]), "digest": digest,
                    "idempotent": "true", "can_execute": "false"}
        if live.get("conclusion") == "neutral":
            return {"decision": "HOLD", "reason": "ALREADY_COMPLETED_HOLD",
                    "check_run_id": str(live["id"]), "digest": digest,
                    "idempotent": "true", "can_execute": "false"}
        raise Hold("LEDGER_RECOVERY_LEASE_LOST")
    live_lease = parse_lease(_summary_from(live))
    if live_lease is None or live_lease["lease_run_id"] != str(recovery_run_id):
        raise Hold("LEDGER_RECOVERY_LEASE_HELD")
    # Re-check adverse history after the lease so a racing REJECT cannot be dropped.
    if (evidence["semantic_decision"] == "REJECT"
            or prior_semantic_reject_on_sha(evidence["head"], read_token)
            or sibling_ledger_reject(digest, read_token, ignore_id=check["id"])):
        decision, reason = "REJECT", "CONFIRMED_SEMANTIC_REJECT"
    receipt, _result = complete_reservation(
        live, evidence, decision=decision, reason=reason, qa_token=qa_token,
        recovery_run_id=recovery_run_id, recovery_attempt=recovery_attempt, lease=lease)
    return receipt


def list_in_progress_reservations(read_token, *, check_run_id=None):
    if check_run_id is not None:
        check = load_check(check_run_id, read_token)
        if check.get("status") != "in_progress":
            return [check]
        return [check]
    path = f"/commits/{LEDGER_SENTINEL_SHA}/check-runs?filter=all&per_page=100"
    total = None
    matched = []
    for page in range(1, MAX_SCAN_PAGES + 1):
        result = gh(path + f"&page={page}", read_token)
        if (type(result) is not dict or type(result.get("total_count")) is not int
                or result["total_count"] < 0 or result["total_count"] > 1000
                or type(result.get("check_runs")) is not list
                or (total is not None and total != result["total_count"])):
            raise Hold("CONTENT_LEDGER_INVENTORY_UNTRUSTED")
        total = result["total_count"]
        for run in result["check_runs"]:
            if type(run) is not dict:
                raise Hold("CONTENT_LEDGER_CHECK_INVALID")
            app = run.get("app")
            name = run.get("name")
            if type(app) is not dict or app.get("id") != QA_APP_ID:
                continue
            if type(name) is not str or not name.startswith(LEDGER_NAME_PREFIX):
                continue
            if run.get("status") != "in_progress":
                continue
            verify_ledger_check(run)
            matched.append(run)
            if len(matched) > MAX_RECOVERIES:
                raise Hold("LEDGER_RECOVERY_UNBOUNDED")
        if page * 100 >= total:
            return matched
    raise Hold("CONTENT_LEDGER_UNBOUNDED")


def recover_all(*, read_token, qa_token, recovery_run_id, recovery_attempt, check_run_id=None):
    _require_token_pair(read_token, qa_token)
    found = list_in_progress_reservations(read_token, check_run_id=check_run_id)
    receipts = []
    for check in found:
        try:
            receipts.append(recover_check(
                check["id"], read_token=read_token, qa_token=qa_token,
                recovery_run_id=recovery_run_id, recovery_attempt=recovery_attempt))
        except Hold as exc:
            receipts.append({
                "decision": "HOLD",
                "reason": str(exc),
                "check_run_id": str(check.get("id")),
                "mutated": "false",
                "can_execute": "false",
            })
    return receipts


def main():
    if os.environ.get("GITHUB_REPOSITORY") != REPO or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Hold("PROTECTED_MAIN_REQUIRED")
    read_token = os.environ.get("GH_READ_TOKEN", "")
    qa_token = os.environ.get("QA_APP_TOKEN", "")
    recovery_run_id = int(os.environ["GITHUB_RUN_ID"])
    recovery_attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    raw_id = os.environ.get("WOW_QA_RECOVERY_CHECK_RUN_ID", "").strip()
    check_run_id = int(raw_id) if raw_id else None
    receipts = recover_all(
        read_token=read_token, qa_token=qa_token, recovery_run_id=recovery_run_id,
        recovery_attempt=recovery_attempt, check_run_id=check_run_id)
    report = {
        "schema_version": 1,
        "repository": REPO,
        "run_id": recovery_run_id,
        "run_attempt": recovery_attempt,
        "decision": "HOLD",
        "receipts": receipts,
        "can_execute": False,
        "no_live_trading": True,
        "custom_gpt_identity": "WOW_BETTING_ENGINE",
        "runtime_generation": "V17_ACTIVE",
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    Path("wow-qa-ledger-recovery.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n")
    mutated = [item for item in receipts if item.get("idempotent") != "true"
               and item.get("mutated") != "false"
               and item.get("reason") not in {
                   "RETAINED_SEMANTIC_REJECT", "ALREADY_COMPLETED_HOLD",
                   "DUPLICATE_RECOVERY_EVENT"}]
    print("LEDGER_RECOVERY_RECEIPTS=" + str(len(receipts)))
    hard = [item for item in receipts if item.get("reason") in {
        "CONTENT_LEDGER_QA_APP_TOKEN_MISSING", "LEDGER_RECOVERY_QA_APP_TOKEN_NOT_DISTINCT",
        "LEDGER_RECOVERY_READ_TOKEN_MISSING", "LEDGER_RECOVERY_UNBOUNDED",
        "CONTENT_LEDGER_UNBOUNDED", "CONTENT_LEDGER_INVENTORY_UNTRUSTED"}]
    if hard:
        print("INDEPENDENT_LEDGER_RECOVERY_HOLD: " + hard[0]["reason"], file=sys.stderr)
        raise SystemExit(1)
    print("INDEPENDENT_LEDGER_RECOVERY_COMPLETE: mutated=" + str(len(mutated)))


if __name__ == "__main__":
    try:
        main()
    except Hold as exc:
        print("INDEPENDENT_LEDGER_RECOVERY_HOLD: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
