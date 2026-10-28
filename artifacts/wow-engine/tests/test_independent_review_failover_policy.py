"""Offline negative/security cases for the independent reviewer failover contract."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v17"))
from independent_review_failover_policy import (
    Attempt, FailureCode, ReviewPolicyError, classify_provider_failure,
    select_next_provider, validate_review,
)

SHA = "a" * 40
DIGEST = "f" * 64
FILES = ["README.md", "src/review.py"]


def attempt(code=FailureCode.PROVIDER_QUOTA, **overrides):
    d = dict(provider="openai", reviewer_role="independent_security_review",
             reviewer_identity="wow-independent-reviewer-app",
             engineering_identity="wow-engineering-app", head_sha=SHA,
             diff_sha256=DIGEST, result_code=code, retry_count=0, source_run_id=42)
    d.update(overrides)
    return Attempt(**d)


def valid_review(decision="PASS"):
    return dict(decision=decision, summary="Reviewed full patch", reviewed_head_sha=SHA,
                reviewed_diff_sha256=DIGEST, reviewed_files=FILES,
                assessment={"source_review": "PASS", "security_review": "PASS",
                            "probability_integrity_review": "PASS"}, findings=[])


class ReviewFallbackTests(unittest.TestCase):
    def test_only_quota_or_transport_auth_admit_fallback(self):
        for code in FailureCode:
            with self.subTest(code=code):
                expected = "anthropic" if code in (FailureCode.PROVIDER_QUOTA, FailureCode.TRANSPORT_AUTH) else None
                self.assertEqual(select_next_provider([attempt(code)]), expected)

    def test_governance_denial_or_rejection_overrides_transport(self):
        self.assertEqual(classify_provider_failure(http_status=429, governance_denial=True), FailureCode.GOVERNANCE_DENIAL)
        self.assertEqual(classify_provider_failure(http_status=429, rejected=True), FailureCode.REVIEW_REJECTED)
        self.assertEqual(classify_provider_failure(http_status=429, malformed_output=True), FailureCode.MALFORMED_OUTPUT)

    def test_typed_http_failures_only(self):
        for status, expected in ((429, FailureCode.PROVIDER_QUOTA), (401, FailureCode.TRANSPORT_AUTH),
                                 (503, FailureCode.TRANSPORT_AUTH), (200, FailureCode.UNKNOWN_FAILURE),
                                 (400, FailureCode.UNKNOWN_FAILURE)):
            with self.subTest(status=status):
                self.assertEqual(classify_provider_failure(http_status=status), expected)

    def test_transport_timeout_is_retryable_only_when_trusted_adapter_says_so(self):
        self.assertEqual(classify_provider_failure(http_status=None, timeout=True), FailureCode.TRANSPORT_AUTH)
        self.assertEqual(classify_provider_failure(http_status=None), FailureCode.UNKNOWN_FAILURE)
        self.assertEqual(classify_provider_failure(http_status=True), FailureCode.UNKNOWN_FAILURE)

    def test_absent_or_second_retry_cannot_trigger_fallback(self):
        self.assertIsNone(select_next_provider([]))
        self.assertIsNone(select_next_provider([attempt(), attempt()]))
        self.assertIsNone(select_next_provider([attempt(provider="anthropic")]))
        self.assertIsNone(select_next_provider([attempt(retry_count=1)]))

    def test_wrong_identity_or_sha_fail_closed(self):
        for kw in (dict(reviewer_identity="wow-engineering-app"), dict(reviewer_identity=""),
                   dict(head_sha="b"), dict(diff_sha256="123"), dict(source_run_id=0),
                   dict(result_code="PROVIDER_QUOTA"), dict(retry_count=True)):
            with self.subTest(kw=kw), self.assertRaises(ReviewPolicyError):
                select_next_provider([attempt(**kw)])

    def test_pass_and_reject_are_both_terminal_review_results(self):
        self.assertEqual(validate_review(payload=valid_review(), head_sha=SHA,
                                         diff_sha256=DIGEST, paths=FILES), "PASS")
        self.assertEqual(validate_review(payload=valid_review("REJECT"), head_sha=SHA,
                                         diff_sha256=DIGEST, paths=FILES), "REJECT")

    def test_exit_zero_cannot_replace_valid_structured_review(self):
        for payload in (None, {}, "PASS", {"exit_code": 0}, {"decision": "PASS"}):
            with self.subTest(payload=payload), self.assertRaises(ReviewPolicyError):
                validate_review(payload=payload, head_sha=SHA, diff_sha256=DIGEST, paths=FILES)

    def test_stale_head_diff_and_missing_files_fail(self):
        for k, v in (("reviewed_head_sha", "b" * 40),
                     ("reviewed_diff_sha256", "0" * 64), ("reviewed_files", [FILES[0]])):
            payload = valid_review()
            payload[k] = v
            with self.subTest(key=k), self.assertRaises(ReviewPolicyError):
                validate_review(payload=payload, head_sha=SHA, diff_sha256=DIGEST, paths=FILES)

    def test_pass_cannot_overrule_high_findings_or_failed_assessment(self):
        payload = valid_review()
        payload["findings"] = [{"severity": "high", "file": FILES[0], "detail": "bad", "evidence": "line 2"}]
        with self.assertRaises(ReviewPolicyError):
            validate_review(payload=payload, head_sha=SHA, diff_sha256=DIGEST, paths=FILES)
        payload = valid_review()
        payload["assessment"]["security_review"] = "FAIL"
        with self.assertRaises(ReviewPolicyError):
            validate_review(payload=payload, head_sha=SHA, diff_sha256=DIGEST, paths=FILES)

    def test_attempt_receipt_is_secret_free_and_not_approval(self):
        rec = attempt().receipt(revisit_trigger="provider available or exact SHA changed")
        self.assertEqual(rec["typed_result"], "PROVIDER_QUOTA")
        self.assertEqual(rec["head_sha"], SHA)
        self.assertEqual(rec["retry_count"], 0)
        self.assertFalse(rec["review_approved"])
        self.assertFalse(rec["can_execute"])
        self.assertEqual(len(rec["receipt_sha256"]), 64)
        self.assertNotIn("secret", rec)

    def test_missing_revisit_trigger_rejected(self):
        with self.assertRaises(ReviewPolicyError):
            attempt().receipt(revisit_trigger="")

if __name__ == "__main__":
    unittest.main()
