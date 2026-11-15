"""V28 Gemini App attestation regression checks (network-free)."""
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "wow_v28_reviewer_app_attest.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("wow_v28_reviewer_app_attest", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA, DIFF = "a" * 40, "b" * 64


class TestGeminiReviewerAttestation(unittest.TestCase):
    def setUp(self):
        self.receipt = {
            "schema_version": 2, "repository": m.REPO, "reviewer_identity": "gemini-generative-api",
            "decision": "PASS", "can_execute": False, "no_live_trading": True,
            "run_id": 125, "run_attempt": 1, "head_sha": SHA, "diff_sha256": DIFF,
            "pr_number": 2667, "verdict": {
                "source_review": "PASS", "security_review": "PASS",
                "probability_integrity_review": "PASS", "findings": [],
            },
        }

    def test_genuine_gemini_receipt_is_exact_head_bound(self):
        body = m.trusted_receipt(self.receipt, run_id=125, attempt=1)
        self.assertIn("SHA=" + SHA, body)
        self.assertIn("DIFF_SHA256=" + DIFF, body)

    def test_wrong_provider_or_run_cannot_attest(self):
        for patch in (
            {"reviewer_identity": "github-owner-security-review"},
            {"decision": "HOLD"}, {"head_sha": "bad"},
            {"run_id": 124}, {"no_live_trading": False},
            {"verdict": None},
        ):
            with self.subTest(patch=patch):
                with self.assertRaises(m.AttestationHold):
                    m.trusted_receipt({**self.receipt, **patch}, run_id=125, attempt=1)

    def test_reject_sticky_even_when_later_provider_passes(self):
        body = m.trusted_receipt(self.receipt, run_id=125, attempt=1)
        prior = [{"user": {"login": m.BOT_LOGIN}, "commit_id": SHA,
                  "state": "CHANGES_REQUESTED", "body": body}]
        with self.assertRaisesRegex(m.AttestationHold, "STICKY_REJECT"):
            m.review_event(self.receipt, body, prior)

    def test_duplicate_pass_no_second_review(self):
        body = m.trusted_receipt(self.receipt, run_id=125, attempt=1)
        prior = [{"user": {"login": m.BOT_LOGIN}, "commit_id": SHA,
                  "state": "APPROVED", "body": body}]
        self.assertIsNone(m.review_event(self.receipt, body, prior))
        self.assertEqual(m.review_event(self.receipt, body, []), "APPROVE")

    def test_reject_posts_request_changes_not_approval(self):
        rejected = {**self.receipt, "decision": "REJECT"}
        body = m.trusted_receipt(rejected, run_id=125, attempt=1)
        self.assertEqual(m.review_event(rejected, body, []), "REQUEST_CHANGES")

    def test_conflicting_same_sha_review_is_hold(self):
        body = m.trusted_receipt(self.receipt, run_id=125, attempt=1)
        prior = [{"user": {"login": m.BOT_LOGIN}, "commit_id": SHA,
                  "state": "APPROVED", "body": "other receipt"}]
        with self.assertRaisesRegex(m.AttestationHold, "CONFLICTING"):
            m.review_event(self.receipt, body, prior)


if __name__ == "__main__":
    unittest.main()
