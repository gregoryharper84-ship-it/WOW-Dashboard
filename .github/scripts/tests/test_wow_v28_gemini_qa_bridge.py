"""Offline negative/positive tests for V28 Gemini QA bridge."""
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "wow_v28_gemini_qa_bridge.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("v28_gemini_qa_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
SHA = "a" * 40
FILES = [{"filename": "src/fix.py", "status": "modified", "previous_filename": None,
          "patch": "@@ -1 +1 @@\n-old\n+new", "additions": 1, "deletions": 1}]
NORMAL, DIGEST = bridge.source.canonical_diff(FILES, expected_count=1)
CAPTURED = {"pr": {"number": 2667, "head": {"sha": SHA, "repo": {"full_name": bridge.REPO}},
                   "base": {"ref": "main"}, "user": {"login": "repository-author"},
                   "state": "open", "draft": False, "changed_files": 1},
            "files": FILES, "diff_sha256": DIGEST}
GOOD = {"decision": "PASS", "summary": "reviewed all changed code and safeguards",
        "reviewed_head_sha": SHA, "reviewed_diff_sha256": DIGEST,
        "reviewed_files": ["src/fix.py"],
        "assessment": {"source_review": "PASS", "security_review": "PASS",
                       "probability_integrity_review": "PASS"}, "findings": []}


class TestGeminiQaBridge(unittest.TestCase):
    def test_exact_head_diff_and_file_inventory(self):
        number, head, author, files, digest = bridge.target(CAPTURED)
        self.assertEqual((number, head, digest), (2667, SHA, DIGEST))
        self.assertEqual(files[0]["filename"], "src/fix.py")

    def test_stale_and_incomplete_inventory_fail_closed(self):
        bad = {**CAPTURED, "diff_sha256": "b" * 64}
        with self.assertRaises(bridge.GeminiHold):
            bridge.target(bad)
        bad = {**CAPTURED, "pr": {**CAPTURED["pr"], "changed_files": 2}}
        with self.assertRaises(bridge.source.SourceReviewError):
            bridge.target(bad)

    def test_no_inference_without_free_tier_attestation(self):
        env = {"WOW_QA_REVIEW_GEMINI_API_KEY": "fake", "WOW_QA_GEMINI_MODEL": bridge.MODEL,
               "WOW_QA_GEMINI_FREE_TIER_CONFIRMED": "false"}
        with patch.dict(os.environ, env), patch.object(bridge.semantic, "request_json") as call:
            with self.assertRaisesRegex(bridge.GeminiHold, "FREE_TIER"):
                bridge.model_review(CAPTURED)
            call.assert_not_called()

    def test_structured_genuine_pass_and_malformed_result(self):
        import json
        output = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(GOOD)}]}}]}
        env = {"WOW_QA_REVIEW_GEMINI_API_KEY": "fake", "WOW_QA_GEMINI_MODEL": bridge.MODEL,
               "WOW_QA_GEMINI_FREE_TIER_CONFIRMED": "true"}
        with patch.dict(os.environ, env), \
             patch.object(bridge.semantic, "review_models", return_value=[bridge.MODEL]), \
             patch.object(bridge.semantic, "request_json", return_value=output):
            self.assertEqual(bridge.model_review(CAPTURED)["decision"], "PASS")
        bad = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "not JSON"}]}}]}
        with patch.dict(os.environ, env), \
             patch.object(bridge.semantic, "review_models", return_value=[bridge.MODEL]), \
             patch.object(bridge.semantic, "request_json", return_value=bad):
            with self.assertRaisesRegex(bridge.GeminiHold, "NOT_JSON"):
                bridge.model_review(CAPTURED)

    def test_wrong_head_in_review_result_rejected(self):
        import json
        bad = {**GOOD, "reviewed_head_sha": "b" * 40}
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(bad)}]}}]}
        env = {"WOW_QA_REVIEW_GEMINI_API_KEY": "fake", "WOW_QA_GEMINI_MODEL": bridge.MODEL,
               "WOW_QA_GEMINI_FREE_TIER_CONFIRMED": "true"}
        with patch.dict(os.environ, env), \
             patch.object(bridge.semantic, "review_models", return_value=[bridge.MODEL]), \
             patch.object(bridge.semantic, "request_json", return_value=response):
            with self.assertRaisesRegex(bridge.source.SourceReviewError, "HEAD_MISMATCH"):
                bridge.model_review(CAPTURED)

    def test_same_sha_reject_can_supersede_approval_but_not_reverse(self):
        prior = [{"state": "APPROVED", "body": "earlier approval"}]
        self.assertEqual(bridge.review_state(prior, "REJECT", "later rejection"), 0)
        with self.assertRaisesRegex(bridge.GeminiHold, "STICKY_REJECT"):
            bridge.review_state([{"state": "CHANGES_REQUESTED", "body": "earlier reject"}],
                                "PASS", "later purported pass")

    def test_app_review_cannot_post_after_head_changes_during_inventory(self):
        artifact = {
            "schema_version": 1,
            "target": {"pr_number": 2667, "head_sha": SHA,
                       "author": {"login": "repository-author"}},
            "diff": {"sha256": DIGEST, "file_paths": ["src/fix.py"]},
            "review": {"decision": "PASS"},
            "source": {"provider_identity": "gemini-generative-api",
                       "credential_scope": "WOW_QA_REVIEW_GEMINI_API_KEY",
                       "run_id": "123", "run_attempt": 1},
        }
        changed = {**CAPTURED, "pr": {**CAPTURED["pr"],
                                    "head": {"sha": "b" * 40, "repo": {"full_name": bridge.REPO}}}}
        with patch.dict(os.environ, {"WOW_REVIEWER_APP_ID": bridge.APP_ID,
                                      "GITHUB_TOKEN": "read", "REVIEWER_APP_TOKEN": "reviewer"}), \
             patch.object(bridge.source, "capture_pr_evidence",
                          side_effect=[CAPTURED, changed]), \
             patch.object(bridge, "read_reviews", return_value=[]), \
             patch.object(bridge.semantic, "request_json") as submission:
            with self.assertRaisesRegex(bridge.GeminiHold, "PR_IDENTITY_OR_DIFF_MOVED"):
                bridge.attest(CAPTURED, artifact, verify_only=False)
            submission.assert_not_called()

    def test_same_source_second_run_reuses_approved_app_review(self):
        initial = {
            "schema_version": 1,
            "target": {"pr_number": 2667, "head_sha": SHA},
            "diff": {"sha256": DIGEST},
            "review": {"decision": "PASS"},
            "source": {"provider_identity": "gemini-generative-api",
                       "credential_scope": "WOW_QA_REVIEW_GEMINI_API_KEY",
                       "run_id": "123", "run_attempt": 1},
        }
        next_run = {**initial, "source": {**initial["source"],
                                         "run_id": "456", "run_attempt": 2}}
        first = bridge.attestation_body(initial)
        second = bridge.attestation_body(next_run)
        self.assertEqual(first, second)
        self.assertEqual(bridge.review_state(
            [{"state": "APPROVED", "body": first}], "PASS", second), 1)
        reject = {**next_run, "review": {"decision": "REJECT"}}
        rejected_body = bridge.attestation_body(reject)
        self.assertEqual(bridge.review_state(
            [{"state": "APPROVED", "body": first}], "REJECT", rejected_body), 0)

    def test_wrong_provider_artifact_cannot_attest(self):
        artifact = {"schema_version": 1, "target": {"pr_number": 2667, "head_sha": SHA},
                    "diff": {"sha256": DIGEST}, "review": {"decision": "PASS"},
                    "source": {"provider_identity": "openai-codex-action",
                               "credential_scope": "WOW_QA_REVIEW_OPENAI_API_KEY"}}
        with self.assertRaisesRegex(bridge.GeminiHold, "PROVENANCE"):
            bridge.attestation_body(artifact)


if __name__ == "__main__":
    unittest.main()
