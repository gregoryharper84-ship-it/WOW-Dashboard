#!/usr/bin/env python3
"""Governed recovery for orphaned QA content-ledger reservations (P0 #2136).

A reviewer run that is cancelled, killed, times out or loses network access
after `reserve_content_review` (PR #2275) leaves a QA App check run in
`in_progress` on the ledger sentinel commit. The reviewer's history scan then
raises CONTENT_LEDGER_IN_PROGRESS forever: identical content can never be
reviewed again and no amount of retrying dissolves the deadlock.

This tool is the separately authenticated recovery path for that state. It:

* never publishes PASS (recovery outcomes are HOLD or REJECT only),
* never deletes or rewrites a check run; it completes the original
  reservation in place, retaining its check-run ID and content digest,
* completes as REJECT whenever a semantic REJECT is corroborated,
* fails closed when provenance, staleness or the provider decision cannot
  be established, and
* serializes competing recovery workers with a QA App claim check run: the
  lowest claim ID wins, every other worker aborts without touching the
  reservation.

Run ONLY from protected main in the wow-qa environment, with the QA App
(checks:write) token as the mutation credential and a distinct read token.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
CHECK_NAME = "WOW Independent Security Review"
QA_APP_ID = 5245832
# Must stay byte-identical to wow_simple_security_review.py (PR #2275 ledger).
LEDGER_SENTINEL_SHA = "79fa0c275a58cd9d397c5a1bc81d641b6dad7f75"
LEDGER_NAME_PREFIX = "WOW Semantic Content Ledger "
RECOVERY_CLAIM_PREFIX = "WOW Semantic Content Ledger Recovery "
OWNER_LOGIN = "gregoryharper84-ship-it"
PROVIDER_IDENTITIES = ("anthropic-claude-api", "gemini-generative-api")
PROVIDER_DECISIONS = ("NONE_RECEIVED", "PROVIDER_UNAVAILABLE", "REJECT_CONFIRMED")
DEFAULT_STALE_AFTER_SECONDS = 1800  # > 12-minute reviewer timeout + queue margin
MIN_STALE_AFTER_SECONDS = 900
MAX_STALE_AFTER_SECONDS = 86400
MAX_PATCH_ATTEMPTS = 3
TRANSIENT_PATCH_CODES = ("HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504",
                         "HTTP_429", "GITHUB_TRANSPORT")


class Hold(Exception):
    """Recovery cannot be established; the reservation stays untouched."""


class NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


HTTP_CLIENT = build_opener(NoCredentialRedirect)
_SHA40 = re.compile(r"[a-f0-9]{40}")
_DIGEST64 = re.compile(r"[a-f0-9]{64}")


def request_json(url, *, token=None, data=None, method=None):
    if not url.startswith("https://api.github.com/repos/" + REPO + "/"):
        raise Hold("RECOVERY_ENDPOINT_UNTRUSTED")
    if method is not None and method not in ("GET", "POST", "PATCH"):
        raise Hold("RECOVERY_METHOD_UNTRUSTED")
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "wow-qa-ledger-recovery-v1"}
    if token:
        h["Authorization"] = "Bearer " + token
    body = None if data is None else json.dumps(data, separators=(",", ":")).encode()
    if body is not None:
        h["Content-Type"] = "application/json"
    try:
        with HTTP_CLIENT.open(Request(url, data=body, headers=h, method=method),
                              timeout=60) as response:
            return json.loads(response.read(250000))
    except HTTPError as exc:
        raise Hold("HTTP_" + str(exc.code)) from None
    except (URLError, TimeoutError):
        raise Hold("GITHUB_TRANSPORT") from None
    except (ValueError, UnicodeError):
        raise Hold("RESPONSE_JSON_INVALID") from None


class GitHubRecoveryClient:
    """Thin, token-separated GitHub client. Reads use the read token only;
    every mutation uses the QA App token only."""

    def __init__(self, read_token, qa_token):
        self.read_token = read_token
        self.qa_token = qa_token

    def _checks_page(self, sha, check_name, page):
        name = check_name.replace(" ", "%20")
        return request_json(
            "https://api.github.com/repos/" + REPO +
            f"/commits/{sha}/check-runs?check_name={name}&filter=all"
            f"&per_page=100&page={page}", token=self.read_token)

    def _list_named_checks(self, sha, check_name):
        runs, total = [], None
        for page in range(1, 11):
            data = self._checks_page(sha, check_name, page)
            if (type(data) is not dict or type(data.get("check_runs")) is not list
                    or type(data.get("total_count")) is not int
                    or data["total_count"] < 0 or data["total_count"] > 1000
                    or (total is not None and total != data["total_count"])):
                raise Hold("LEDGER_INVENTORY_UNTRUSTED")
            total = data["total_count"]
            runs.extend(data["check_runs"])
            if page * 100 >= total:
                return runs
        raise Hold("LEDGER_INVENTORY_UNBOUNDED")

    def list_ledger(self, digest):
        return self._list_named_checks(LEDGER_SENTINEL_SHA,
                                       LEDGER_NAME_PREFIX + digest)

    def list_claims(self, reservation_id):
        return self._list_named_checks(LEDGER_SENTINEL_SHA,
                                       RECOVERY_CLAIM_PREFIX + str(reservation_id))

    def list_head_checks(self, head_sha):
        return self._list_named_checks(head_sha, CHECK_NAME)

    def get_pr(self, pr_number):
        return request_json("https://api.github.com/repos/" + REPO +
                            f"/pulls/{pr_number}", token=self.read_token)

    def create_check(self, payload):
        return request_json("https://api.github.com/repos/" + REPO + "/check-runs",
                            token=self.qa_token, data=payload, method="POST")

    def patch_check(self, check_run_id, payload):
        return request_json("https://api.github.com/repos/" + REPO +
                            f"/check-runs/{check_run_id}",
                            token=self.qa_token, data=payload, method="PATCH")


def _epoch_utc(value):
    """Parse an ISO-8601 Z timestamp to epoch seconds (UTC, tz-independent)."""
    import calendar
    import datetime
    if type(value) is not str:
        raise Hold("RESERVATION_TIMESTAMP_INVALID")
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise Hold("RESERVATION_TIMESTAMP_INVALID") from None
    return calendar.timegm(parsed.timetuple())


def validate_evidence(evidence):
    if type(evidence) is not dict:
        raise Hold("RECOVERY_EVIDENCE_INVALID")
    required = {"pr_number", "head_sha", "base_sha", "run_id", "run_attempt",
                "provider_decision", "evidence_ref"}
    if not required.issubset(evidence):
        raise Hold("RECOVERY_EVIDENCE_INCOMPLETE")
    if type(evidence["pr_number"]) is not int or evidence["pr_number"] <= 0:
        raise Hold("RECOVERY_EVIDENCE_PR_INVALID")
    for key in ("head_sha", "base_sha"):
        value = evidence[key]
        if type(value) is not str or not _SHA40.fullmatch(value):
            raise Hold("RECOVERY_EVIDENCE_SHA_INVALID")
    if type(evidence["run_id"]) is not int or evidence["run_id"] <= 0:
        raise Hold("RECOVERY_EVIDENCE_RUN_INVALID")
    if type(evidence["run_attempt"]) is not int or evidence["run_attempt"] <= 0:
        raise Hold("RECOVERY_EVIDENCE_RUN_INVALID")
    if evidence["provider_decision"] not in PROVIDER_DECISIONS:
        raise Hold("RECOVERY_PROVIDER_DECISION_UNESTABLISHED")
    reviewer = evidence.get("reviewer_identity")
    if evidence["provider_decision"] == "REJECT_CONFIRMED" \
            and reviewer not in PROVIDER_IDENTITIES:
        raise Hold("RECOVERY_REJECT_REVIEWER_UNVERIFIED")
    ref = evidence["evidence_ref"]
    if type(ref) is not str or not ref or len(ref) > 500:
        raise Hold("RECOVERY_EVIDENCE_REF_INVALID")
    return evidence


def _valid_ledger_record(run, digest):
    if type(run) is not dict:
        raise Hold("LEDGER_CHECK_INVALID")
    app = run.get("app")
    if type(app) is not dict or app.get("id") != QA_APP_ID:
        return None  # foreign check runs are not ledger evidence
    if run.get("name") != LEDGER_NAME_PREFIX + digest:
        return None
    if run.get("head_sha") != LEDGER_SENTINEL_SHA or run.get("external_id") != digest:
        raise Hold("LEDGER_PROVENANCE_INVALID")
    if type(run.get("id")) is not int or run["id"] <= 0:
        raise Hold("LEDGER_CHECK_INVALID")
    return run


def ledger_records(runs, digest):
    records = []
    for run in runs:
        record = _valid_ledger_record(run, digest)
        if record is not None:
            records.append(record)
    return records


def completed_decision(record):
    """Map a completed ledger record to PASS/REJECT/HOLD, else fail closed."""
    title = (record.get("output") or {}).get("title")
    conclusion = record.get("conclusion")
    for decision, want_title, want_conclusion in (
            ("PASS", "PASS — content review", "success"),
            ("REJECT", "REJECT — content review", "failure"),
            ("HOLD", "HOLD — content review", "neutral")):
        if title == want_title and conclusion == want_conclusion:
            return decision
    raise Hold("LEDGER_VERDICT_UNTRUSTED")


def idempotency_key(reservation_id, digest, outcome):
    material = f"{reservation_id}:{digest}:{outcome}"
    return hashlib.sha256(material.encode()).hexdigest()


def assess_recovery(records, reservation_id, evidence, *, now_epoch,
                    head_reject_found=False, stale_after_seconds=
                    DEFAULT_STALE_AFTER_SECONDS):
    """Pure fail-closed decision: HOLD or REJECT, never PASS."""
    if not (MIN_STALE_AFTER_SECONDS <= stale_after_seconds
            <= MAX_STALE_AFTER_SECONDS):
        raise Hold("RECOVERY_STALENESS_BOUND_INVALID")
    validate_evidence(evidence)
    target = None
    prior_completed_reject = False
    for record in records:
        if record.get("status") == "completed":
            if completed_decision(record) == "REJECT":
                prior_completed_reject = True
        elif record.get("status") != "in_progress":
            raise Hold("LEDGER_STATE_UNTRUSTED")
        if record["id"] == reservation_id:
            target = record
    if target is None:
        raise Hold("RESERVATION_NOT_FOUND")
    if target.get("status") == "completed":
        return {"already_completed": True,
                "existing_decision": completed_decision(target),
                "reservation_id": reservation_id}
    started = _epoch_utc(target.get("started_at"))
    if now_epoch - started < stale_after_seconds:
        raise Hold("RESERVATION_NOT_STALE")
    if prior_completed_reject or head_reject_found \
            or evidence["provider_decision"] == "REJECT_CONFIRMED":
        outcome, reason = "REJECT", "CONFIRMED_SEMANTIC_REJECT_RETAINED"
    elif evidence["provider_decision"] in ("NONE_RECEIVED", "PROVIDER_UNAVAILABLE"):
        outcome, reason = "HOLD", "ORPHANED_RESERVATION_NO_ADVERSE_VERDICT"
    else:  # pragma: no cover - validate_evidence already gates this
        raise Hold("RECOVERY_PROVIDER_DECISION_UNESTABLISHED")
    return {"already_completed": False, "outcome": outcome, "reason": reason,
            "reservation_id": reservation_id,
            "idempotency_key": idempotency_key(reservation_id,
                                               target["external_id"], outcome)}


def head_has_semantic_reject(head_checks):
    for run in head_checks:
        if type(run) is not dict:
            raise Hold("HEAD_CHECK_INVALID")
        app = run.get("app") or {}
        if (app.get("id") == QA_APP_ID and run.get("name") == CHECK_NAME
                and run.get("conclusion") == "failure"
                and str((run.get("output") or {}).get("title", "")).startswith("REJECT")):
            return True
    return False


def _patch_with_bounded_retries(client, check_run_id, payload):
    last = None
    for _ in range(MAX_PATCH_ATTEMPTS):
        try:
            return client.patch_check(check_run_id, payload)
        except Hold as exc:
            last = exc
            if not any(str(exc).startswith(code) for code in TRANSIENT_PATCH_CODES):
                raise
    raise last


def execute_recovery(client, *, digest, reservation_id, evidence, actor,
                     authorized_actors, now_epoch, stale_after_seconds=
                     DEFAULT_STALE_AFTER_SECONDS, dry_run=True):
    """Recover one stale reservation. Returns a traceable recovery receipt."""
    if type(digest) is not str or not _DIGEST64.fullmatch(digest):
        raise Hold("RECOVERY_DIGEST_INVALID")
    if actor not in authorized_actors:
        raise Hold("RECOVERY_ACTOR_UNAUTHORIZED")
    validate_evidence(evidence)
    if not getattr(client, "qa_token", None):
        raise Hold("QA_APP_TOKEN_MISSING")
    if client.qa_token == getattr(client, "read_token", None):
        raise Hold("QA_APP_TOKEN_NOT_DISTINCT_FROM_READ_TOKEN")
    pr = client.get_pr(evidence["pr_number"]) if type(evidence) is dict \
        and type(evidence.get("pr_number")) is int else None
    if type(pr) is not dict \
            or ((pr.get("head") or {}).get("repo") or {}).get("full_name") != REPO:
        raise Hold("RECOVERY_EVIDENCE_PR_UNVERIFIED")
    records = ledger_records(client.list_ledger(digest), digest)
    head_reject = head_has_semantic_reject(
        client.list_head_checks(evidence["head_sha"]))
    plan = assess_recovery(records, reservation_id, evidence,
                           now_epoch=now_epoch, head_reject_found=head_reject,
                           stale_after_seconds=stale_after_seconds)
    target = next(r for r in records if r["id"] == reservation_id)
    receipt = {
        "schema_version": 1, "repository": REPO, "qa_app_id": QA_APP_ID,
        "reservation": {"check_run_id": reservation_id, "digest": digest,
                        "sentinel_sha": LEDGER_SENTINEL_SHA,
                        "name": target.get("name"),
                        "started_at": target.get("started_at")},
        "original": {"pr_number": evidence["pr_number"],
                     "head_sha": evidence["head_sha"],
                     "base_sha": evidence["base_sha"],
                     "run_id": evidence["run_id"],
                     "run_attempt": evidence["run_attempt"],
                     "provider_decision": evidence["provider_decision"],
                     "reviewer_identity": evidence.get("reviewer_identity"),
                     "evidence_ref": evidence["evidence_ref"],
                     "pr_head_moved_since_reservation":
                         (pr.get("head") or {}).get("sha") != evidence["head_sha"]},
        "recovery_actor": actor, "dry_run": dry_run,
        "can_execute": False, "no_live_trading": True,
    }
    if plan["already_completed"]:
        receipt.update({"status": "ALREADY_COMPLETED",
                        "outcome": plan["existing_decision"]})
        return receipt
    receipt.update({"status": "PLANNED" if dry_run else "RECOVERED",
                    "outcome": plan["outcome"], "reason": plan["reason"],
                    "idempotency_key": plan["idempotency_key"]})
    if dry_run:
        return receipt
    # Single-winner claim: the lowest QA App claim ID owns this reservation.
    claim = client.create_check({
        "name": RECOVERY_CLAIM_PREFIX + str(reservation_id),
        "head_sha": LEDGER_SENTINEL_SHA,
        "external_id": plan["idempotency_key"], "status": "in_progress",
        "output": {"title": "RECOVERY_CLAIM — orphaned ledger reservation",
                   "summary": f"reservation={reservation_id} digest={digest} "
                              f"actor={actor} outcome={plan['outcome']}"}})
    if type(claim) is not dict or type(claim.get("id")) is not int:
        raise Hold("RECOVERY_CLAIM_UNVERIFIED")
    receipt["claim_check_run_id"] = claim["id"]
    rivals = [c for c in client.list_claims(reservation_id)
              if type(c) is dict and (c.get("app") or {}).get("id") == QA_APP_ID
              and type(c.get("id")) is int and c["id"] < claim["id"]
              and c.get("status") in ("in_progress", "completed")]
    if rivals:
        _patch_with_bounded_retries(client, claim["id"], {
            "status": "completed", "conclusion": "neutral",
            "output": {"title": "RECOVERY_SUPERSEDED — claim lost",
                       "summary": f"reservation={reservation_id} "
                                  f"winning_claim={rivals[0]['id']}"}})
        raise Hold("RECOVERY_CLAIM_LOST")
    conclusions = {"HOLD": "neutral", "REJECT": "failure"}
    response = _patch_with_bounded_retries(client, reservation_id, {
        "status": "completed", "conclusion": conclusions[plan["outcome"]],
        "output": {"title": plan["outcome"] + " — content review",
                   "summary": (f"QA ledger recovery. reservation={reservation_id} "
                               f"digest={digest} PR={evidence['pr_number']} "
                               f"SHA={evidence['head_sha']} "
                               f"original_run={evidence['run_id']}/"
                               f"{evidence['run_attempt']} actor={actor} "
                               f"reason={plan['reason']} "
                               f"idempotency={plan['idempotency_key']}; "
                               "recovered, never PASS; no wagering authority.")}})
    if (type(response) is not dict or response.get("id") != reservation_id
            or response.get("conclusion") != conclusions[plan["outcome"]]
            or response.get("external_id") != digest):
        raise Hold("RECOVERY_COMMIT_UNVERIFIED")
    claim_done = _patch_with_bounded_retries(client, claim["id"], {
        "status": "completed", "conclusion": conclusions[plan["outcome"]],
        "output": {"title": "RECOVERY_RECEIPT — orphaned ledger reservation",
                   "summary": json.dumps(receipt, sort_keys=True)[:60000]}})
    if type(claim_done) is not dict or claim_done.get("id") != claim["id"]:
        receipt["claim_receipt_pending"] = True
    return receipt


_SUMMARY_PR = re.compile(r"(?:^|\s)PR=([0-9]+)(?:\s|$)")
_SUMMARY_DECISION = re.compile(r"(?:^|\s)decision=([A-Z]+)(?:;|\s|$)")


def assess_attempt_budget(records, pr_number, *, budget=3, enabled=False):
    """EXPERIMENTAL, NOT ENFORCED: distinct rejected contents attempted by a PR.

    One-byte edits mint a new content digest, so identical-content REJECT
    retention alone cannot bound re-rolls. This read-only assessment counts
    distinct digests a PR has had completed as REJECT. It changes no gate;
    activating any budget requires separate governance approval.
    """
    if enabled:
        raise Hold("ATTEMPT_BUDGET_NOT_AUTHORIZED_FOR_ACTIVATION")
    if type(budget) is not int or not 1 <= budget <= 10:
        raise Hold("ATTEMPT_BUDGET_INVALID")
    digests = set()
    for record in records:
        if record.get("status") != "completed":
            continue
        summary = (record.get("output") or {}).get("summary") or ""
        match = _SUMMARY_PR.search(summary)
        decision = completed_decision(record)
        if match and int(match.group(1)) == pr_number and decision == "REJECT":
            digests.add(record.get("external_id"))
    return {"pr_number": pr_number, "distinct_rejected_digests": len(digests),
            "budget": budget, "exceeded": len(digests) > budget,
            "enforced": False,
            "recommendation": ("HOLD_FOR_INDEPENDENT_REVIEW"
                               if len(digests) > budget else "WITHIN_BUDGET")}


def authorized_actors():
    raw = os.environ.get("WOW_QA_RECOVERY_ACTORS", OWNER_LOGIN)
    return {a.strip() for a in raw.split(",") if a.strip()}


def main():
    if os.environ.get("GITHUB_REPOSITORY") != REPO \
            or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise Hold("PROTECTED_MAIN_REQUIRED")
    evidence = {
        "pr_number": int(os.environ["RECOVERY_PR_NUMBER"]),
        "head_sha": os.environ["RECOVERY_HEAD_SHA"],
        "base_sha": os.environ["RECOVERY_BASE_SHA"],
        "run_id": int(os.environ["RECOVERY_ORIGINAL_RUN_ID"]),
        "run_attempt": int(os.environ.get("RECOVERY_ORIGINAL_RUN_ATTEMPT", "1")),
        "provider_decision": os.environ["RECOVERY_PROVIDER_DECISION"],
        "reviewer_identity": os.environ.get("RECOVERY_REVIEWER_IDENTITY") or None,
        "evidence_ref": os.environ["RECOVERY_EVIDENCE_REF"],
    }
    client = GitHubRecoveryClient(os.environ.get("GH_READ_TOKEN", ""),
                                  os.environ.get("QA_APP_TOKEN", ""))
    receipt = execute_recovery(
        client, digest=os.environ["RECOVERY_DIGEST"],
        reservation_id=int(os.environ["RECOVERY_RESERVATION_ID"]),
        evidence=evidence, actor=os.environ.get("GITHUB_ACTOR", ""),
        authorized_actors=authorized_actors(), now_epoch=int(time.time()),
        dry_run=os.environ.get("RECOVERY_DRY_RUN", "true").lower() != "false")
    Path("wow-qa-ledger-recovery-receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print("QA_LEDGER_RECOVERY_" + receipt["status"] + ": " +
          str(receipt["reservation"]["check_run_id"]))


if __name__ == "__main__":
    try:
        main()
    except (Hold, KeyError, ValueError) as exc:
        print("QA_LEDGER_RECOVERY_HOLD: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
