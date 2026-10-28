"""Fail-closed decision contract for independent semantic-review provider failover.

This module is intentionally pure and has NO provider API, check publishing, token,
reviewer App, or merge authority. Its outputs are proposals to the protected
reviewer workflow, not review evidence or release approval. Integration requires
separate, independently authenticated source-review evidence for the new provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
REVIEWER_ROLE = "independent_security_review"
PRIMARY = "openai"
FALLBACK = "anthropic"
MAX_ATTEMPTS = 2


class FailureCode(str, Enum):
    PROVIDER_QUOTA = "PROVIDER_QUOTA"
    TRANSPORT_AUTH = "TRANSPORT_AUTH"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    GOVERNANCE_DENIAL = "GOVERNANCE_DENIAL"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


class ReviewPolicyError(ValueError):
    pass


ELIGIBLE = frozenset({FailureCode.PROVIDER_QUOTA, FailureCode.TRANSPORT_AUTH})
# Only codes delivered by the trusted provider adapter, never raw agent text.
TRANSPORT_HTTP_CODES = frozenset({401, 403, 407, 408, 500, 502, 503, 504})


def classify_provider_failure(*, http_status: int | None,
                              timeout: bool = False,
                              malformed_output: bool = False,
                              rejected: bool = False,
                              governance_denial: bool = False) -> FailureCode:
    """Classify *authenticated adapter observations*, not LLM-generated prose.

    A 200 response with malformed structured evidence is terminal; status 429 is
    quota. Review REJECT and governance denial always take precedence.
    """
    if governance_denial:
        return FailureCode.GOVERNANCE_DENIAL
    if rejected:
        return FailureCode.REVIEW_REJECTED
    if malformed_output:
        return FailureCode.MALFORMED_OUTPUT
    if timeout:
        return FailureCode.TRANSPORT_AUTH
    if type(http_status) is not int:
        return FailureCode.UNKNOWN_FAILURE
    if http_status == 429:
        return FailureCode.PROVIDER_QUOTA
    if http_status in TRANSPORT_HTTP_CODES:
        return FailureCode.TRANSPORT_AUTH
    return FailureCode.UNKNOWN_FAILURE


def _hex(value: Any, regex: re.Pattern[str]) -> bool:
    return isinstance(value, str) and bool(regex.fullmatch(value))


def validate_review(*, payload: object, head_sha: str, diff_sha256: str,
                    paths: list[str]) -> str:
    """Validate structured reviewer output; never equate exit=0 with PASS.

    Returns PASS/REJECT (both are terminal decisions) or raises. REJECT is not
    eligible for fallback. The production receipt's authentication is separate.
    """
    if not _hex(head_sha, SHA) or not _hex(diff_sha256, DIGEST):
        raise ReviewPolicyError("TARGET_IDENTITY_INVALID")
    if (not isinstance(paths, list) or not paths or len(set(paths)) != len(paths)
            or any(not isinstance(p, str) or not p for p in paths)):
        raise ReviewPolicyError("TARGET_INVENTORY_INVALID")
    if not isinstance(payload, Mapping) or set(payload) != {
        "decision", "summary", "reviewed_head_sha", "reviewed_diff_sha256",
        "reviewed_files", "assessment", "findings",
    }:
        raise ReviewPolicyError("MALFORMED_OUTPUT")
    if payload["reviewed_head_sha"] != head_sha or payload["reviewed_diff_sha256"] != diff_sha256:
        raise ReviewPolicyError("STALE_OR_MISMATCHED_HEAD")
    if payload["reviewed_files"] != sorted(paths):
        raise ReviewPolicyError("INCOMPLETE_REVIEW_COVERAGE")
    if (not isinstance(payload["summary"], str) or not payload["summary"].strip()
            or len(payload["summary"]) > 4000):
        raise ReviewPolicyError("MALFORMED_OUTPUT")
    if payload["decision"] not in ("PASS", "REJECT"):
        raise ReviewPolicyError("MALFORMED_OUTPUT")
    assessment = payload["assessment"]
    if (not isinstance(assessment, Mapping) or set(assessment) != {
        "source_review", "security_review", "probability_integrity_review",
    } or any(x not in ("PASS", "FAIL") for x in assessment.values())):
        raise ReviewPolicyError("MALFORMED_OUTPUT")
    findings = payload["findings"]
    if not isinstance(findings, list) or len(findings) > 50:
        raise ReviewPolicyError("MALFORMED_OUTPUT")
    for f in findings:
        if (not isinstance(f, Mapping) or set(f) != {"severity", "file", "detail", "evidence"}
                or f["severity"] not in ("critical", "high", "medium", "low")
                or f["file"] not in paths or any(not isinstance(f[x], str) or not f[x].strip()
                                                 for x in ("detail", "evidence"))):
            raise ReviewPolicyError("MALFORMED_OUTPUT")
    if payload["decision"] == "PASS" and (
        any(x != "PASS" for x in assessment.values())
        or any(f["severity"] in ("critical", "high") for f in findings)
    ):
        raise ReviewPolicyError("PASS_CONTRADICTS_FINDINGS")
    return payload["decision"]


@dataclass(frozen=True)
class Attempt:
    provider: str
    reviewer_role: str
    reviewer_identity: str
    engineering_identity: str
    head_sha: str
    diff_sha256: str
    result_code: FailureCode
    retry_count: int
    source_run_id: int

    def validate(self) -> None:
        if self.provider not in (PRIMARY, FALLBACK):
            raise ReviewPolicyError("PROVIDER_UNKNOWN")
        if self.reviewer_role != REVIEWER_ROLE or not self.reviewer_identity:
            raise ReviewPolicyError("REVIEWER_ROLE_UNTRUSTED")
        if (not self.engineering_identity or
                self.reviewer_identity == self.engineering_identity):
            raise ReviewPolicyError("REVIEWER_NOT_INDEPENDENT")
        if not _hex(self.head_sha, SHA) or not _hex(self.diff_sha256, DIGEST):
            raise ReviewPolicyError("TARGET_IDENTITY_INVALID")
        if not isinstance(self.result_code, FailureCode):
            raise ReviewPolicyError("UNTYPED_FAILURE")
        if type(self.retry_count) is not int or self.retry_count < 0 or self.retry_count >= MAX_ATTEMPTS:
            raise ReviewPolicyError("RETRY_COUNT_INVALID")
        if type(self.source_run_id) is not int or self.source_run_id <= 0:
            raise ReviewPolicyError("RUN_PROVENANCE_INVALID")

    def receipt(self, *, revisit_trigger: str) -> dict[str, object]:
        self.validate()
        if not isinstance(revisit_trigger, str) or not revisit_trigger.strip():
            raise ReviewPolicyError("REVISIT_TRIGGER_MISSING")
        row: dict[str, object] = {
            "provider": self.provider,
            "role": self.reviewer_role,
            "reviewer_identity": self.reviewer_identity,
            "head_sha": self.head_sha,
            "diff_sha256": self.diff_sha256,
            "typed_result": self.result_code.value,
            "retry_count": self.retry_count,
            "source_run_id": self.source_run_id,
            "revisit_trigger": revisit_trigger,
            "can_execute": False,
            "review_approved": False,
        }
        row["receipt_sha256"] = hashlib.sha256(json.dumps(row, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8")).hexdigest()
        return row


def select_next_provider(attempts: list[Attempt]) -> str | None:
    """Only primary typed availability/auth failures permit one independent retry.

    No retry on QA rejection, malformed evidence, identity failure, or missing
    provenance. Neither result is itself a release authorization.
    """
    if not isinstance(attempts, list) or len(attempts) != 1:
        return None
    first = attempts[0]
    first.validate()
    if first.provider != PRIMARY or first.retry_count != 0:
        return None
    if first.result_code in ELIGIBLE:
        return FALLBACK
    return None
