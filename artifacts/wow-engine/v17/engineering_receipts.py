"""Append-only engineering attempt receipt writer (Charter §8). Evidence only.

Writes one row to public.wow_engineering_attempt_receipts through the
service-role PostgREST path, then reads it back by receipt_id to prove
persistence. Success is reported ONLY when the read-back matches what was
written. Rows are never updated: a correction is a new receipt carrying
supersedes_receipt_id.

Validation mirrors the database constraints so callers get a typed,
field-specific error before any network call. The database remains the
authority; its constraints and append-only triggers still apply.

Never a probability, QA, release or execution authority. can_execute=false.
Credentials, URLs and request bodies are never included in diagnostic output.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

TABLE = "wow_engineering_attempt_receipts"
CAN_EXECUTE = False
USER_AGENT = "wow-engineering-receipt-writer"

PROVIDERS = frozenset({"openai", "claude", "human_owner", "automation"})
ROLES = frozenset({
    "implementer", "adversarial_reviewer", "sirt_challenger", "release_preflight",
    "research_challenger", "independent_qa", "release_authority", "conductor",
})
PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
CHANGE_CLASSES = frozenset({"A", "B", "C"})
QA_DECISIONS = frozenset({"PASS", "REJECT", "HOLD"})
RELEASE_DECISIONS = frozenset({"READY", "HOLD", "REJECT", "DEPLOYED"})
DISPOSITIONS = frozenset({
    "IN_PROGRESS", "FIXED_AND_VERIFIED", "PR_CREATED", "EXPERIMENT_CREATED",
    "DUPLICATE", "NOT_REPRODUCIBLE", "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
})
TEXT_FIELDS = (
    "lease_id", "root_cause", "typed_blocker", "blocker_owner", "next_action", "revisit_trigger",
)
FIELDS = (
    "incident_id", "provider", "role", "worker_run_id", "lease_id", "lease_epoch",
    "priority", "change_class", "pr_number", "base_sha", "head_sha", "deployed_sha",
    "root_cause", "tests", "qa_decision", "release_decision", "disposition",
    "typed_blocker", "blocker_owner", "retry_count", "next_action", "revisit_trigger",
    "supersedes_receipt_id",
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


class ReceiptError(Exception):
    """Typed failure. ``code`` is safe to log; it never contains secrets."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _invalid(field: str) -> ReceiptError:
    return ReceiptError(f"RECEIPT_INVALID:{field}")


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def build_receipt(**fields: Any) -> dict[str, Any]:
    """Validate and normalise a receipt. Raises ReceiptError on any violation."""
    unknown = set(fields) - set(FIELDS)
    if "can_execute" in fields:
        raise _invalid("can_execute")
    if unknown:
        raise _invalid(sorted(unknown)[0])

    row: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}

    incident = row.get("incident_id")
    if not _nonempty_text(incident) or len(incident) > 200:
        raise _invalid("incident_id")
    if row.get("provider") not in PROVIDERS:
        raise _invalid("provider")
    if row.get("role") not in ROLES:
        raise _invalid("role")
    if not _nonempty_text(row.get("worker_run_id")):
        raise _invalid("worker_run_id")
    if row.get("disposition") not in DISPOSITIONS:
        raise _invalid("disposition")

    for name, allowed in (("priority", PRIORITIES), ("change_class", CHANGE_CLASSES),
                          ("qa_decision", QA_DECISIONS), ("release_decision", RELEASE_DECISIONS)):
        if name in row and row[name] not in allowed:
            raise _invalid(name)
    for name in ("base_sha", "head_sha", "deployed_sha"):
        if name in row and not (isinstance(row[name], str) and _SHA.match(row[name])):
            raise _invalid(name)
    for name in TEXT_FIELDS:
        if name in row and not _nonempty_text(row[name]):
            raise _invalid(name)
    # bool is an int subclass; reject it explicitly for integer fields.
    if "pr_number" in row and (isinstance(row["pr_number"], bool) or not isinstance(row["pr_number"], int) or row["pr_number"] <= 0):
        raise _invalid("pr_number")
    if "lease_epoch" in row and (isinstance(row["lease_epoch"], bool) or not isinstance(row["lease_epoch"], int) or row["lease_epoch"] < 0):
        raise _invalid("lease_epoch")
    retry = row.setdefault("retry_count", 0)
    if isinstance(retry, bool) or not isinstance(retry, int) or retry < 0:
        raise _invalid("retry_count")
    tests = row.setdefault("tests", [])
    if not isinstance(tests, list):
        raise _invalid("tests")
    if "supersedes_receipt_id" in row:
        try:
            row["supersedes_receipt_id"] = str(uuid.UUID(str(row["supersedes_receipt_id"])))
        except ValueError:
            raise _invalid("supersedes_receipt_id") from None

    disposition = row["disposition"]
    if disposition == "BLOCKED_WITH_EXACT_REASON" and not all(
        k in row for k in ("typed_blocker", "blocker_owner", "revisit_trigger")
    ):
        raise _invalid("blocked_requires_typed_blocker_owner_revisit_trigger")
    if disposition == "FIXED_AND_VERIFIED" and not ("deployed_sha" in row and row.get("qa_decision") == "PASS"):
        raise _invalid("fixed_and_verified_requires_deployed_sha_and_qa_pass")
    if disposition == "PR_CREATED" and not ("pr_number" in row and "head_sha" in row):
        raise _invalid("pr_created_requires_pr_number_and_head_sha")
    return row


def _endpoint(url: str) -> str:
    parsed = urlparse(url or "")
    secure = parsed.scheme == "https" and bool(parsed.hostname)
    local = parsed.scheme == "http" and parsed.hostname in _LOCAL_HOSTS
    if not (secure or local):
        raise ReceiptError("RECEIPT_CONFIGURATION_MISSING")
    return url.rstrip("/") + "/rest/v1/" + TABLE


def _request(endpoint: str, key: str, *, method: str, body: bytes | None = None) -> Request:
    headers = {
        "apikey": key, "Authorization": "Bearer " + key,
        "Accept": "application/json", "User-Agent": USER_AGENT,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"
    return Request(endpoint, data=body, headers=headers, method=method)


def _call(request: Request, timeout_seconds: int) -> Any:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.load(response)
    except HTTPError as exc:
        # 4xx: the governed boundary refused the row (constraint, grant, trigger).
        # Retrying the same payload cannot succeed, so it is not "unavailable".
        if 400 <= exc.code < 500:
            raise ReceiptError("RECEIPT_PERSISTENCE_REJECTED") from None
        raise ReceiptError("RECEIPT_PERSISTENCE_UNAVAILABLE") from None
    except (URLError, TimeoutError, OSError):
        raise ReceiptError("RECEIPT_PERSISTENCE_UNAVAILABLE") from None
    except ValueError:
        raise ReceiptError("RECEIPT_RESPONSE_MALFORMED") from None


def _matches(written: dict[str, Any], stored: dict[str, Any]) -> bool:
    if stored.get("can_execute") is not False:
        return False
    return all(stored.get(k) == v for k, v in written.items())


def record_receipt(url: str, key: str, receipt: dict[str, Any], *, timeout_seconds: int = 10) -> dict[str, Any]:
    """Insert one validated receipt and prove it persisted. Returns the stored row."""
    if not key:
        raise ReceiptError("RECEIPT_CONFIGURATION_MISSING")
    endpoint = _endpoint(url)
    row = build_receipt(**receipt)
    body = json.dumps(row, sort_keys=True).encode("utf-8")
    inserted = _call(_request(endpoint, key, method="POST", body=body), timeout_seconds)
    if not (isinstance(inserted, list) and len(inserted) == 1 and isinstance(inserted[0], dict)):
        raise ReceiptError("RECEIPT_RESPONSE_MALFORMED")
    receipt_id = inserted[0].get("receipt_id")
    try:
        receipt_id = str(uuid.UUID(str(receipt_id)))
    except ValueError:
        raise ReceiptError("RECEIPT_RESPONSE_MALFORMED") from None

    readback = _call(
        _request(endpoint + "?receipt_id=eq." + quote(receipt_id, safe="") + "&select=*", key, method="GET"),
        timeout_seconds,
    )
    if not (isinstance(readback, list) and len(readback) == 1 and isinstance(readback[0], dict)):
        raise ReceiptError("RECEIPT_READBACK_MISSING")
    if not _matches(row, readback[0]):
        raise ReceiptError("RECEIPT_READBACK_MISMATCH")
    return readback[0]


EXIT_CODES = {
    "RECEIPT_CONFIGURATION_MISSING": 2,
    "RECEIPT_PERSISTENCE_UNAVAILABLE": 3,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record one append-only WOW engineering attempt receipt")
    parser.add_argument("--receipt-json", required=True,
                        help="Path to a JSON object with receipt fields, or '-' for stdin")
    args = parser.parse_args(argv)
    try:
        try:
            raw = sys.stdin.read() if args.receipt_json == "-" else open(args.receipt_json, encoding="utf-8").read()
            receipt = json.loads(raw)
        except (OSError, ValueError):
            raise ReceiptError("RECEIPT_INVALID:payload") from None
        if not isinstance(receipt, dict):
            raise ReceiptError("RECEIPT_INVALID:payload")
        stored = record_receipt(
            os.getenv("SUPABASE_URL", ""),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", ""),
            receipt,
        )
    except ReceiptError as error:
        print(json.dumps({"status": "NON_SUCCESS", "reason": error.code, "can_execute": False}, sort_keys=True))
        return EXIT_CODES.get(error.code, 1)
    print(json.dumps({"status": "RECEIPT_PERSISTED_AND_VERIFIED",
                      "receipt_id": stored["receipt_id"], "can_execute": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
