import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "wow_simple_security_review.py"
spec = importlib.util.spec_from_file_location("simple_security", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SimpleSecurityReviewTests(unittest.TestCase):
    def test_exact_event_target(self):
        self.assertEqual(m.event_target({"pull_request": {"number": 2247, "head": {"sha": "a" * 40}}},
                                        "pull_request_target"), (2247, "a" * 40))
        self.assertEqual(m.event_target({"inputs": {"pr_number": "2247", "head_sha": "b" * 40}},
                                        "workflow_dispatch"), (2247, "b" * 40))
        with self.assertRaises(m.Hold):
            m.event_target({"inputs": {"pr_number": "invalid"}}, "workflow_dispatch")

    def test_pass_requires_every_dimension_and_no_findings(self):
        good = {"source_review": "PASS", "security_review": "PASS",
                "probability_integrity_review": "PASS", "findings": []}
        self.assertEqual(m.parse_verdict(good)[0], "PASS")
        self.assertEqual(m.parse_verdict({**good, "security_review": "FAIL"})[0], "REJECT")
        self.assertEqual(m.parse_verdict({**good, "findings": ["Issue found"]})[0], "REJECT")
        for bad in ({}, {**good, "source_review": "UNKNOWN"},
                    {**good, "source_review": []}, {**good, "findings": [0]}, {**good, "extra": True}):
            with self.assertRaises(m.Hold):
                m.parse_verdict(bad)

    def test_failover_only_on_provider_unavailability(self):
        target = {"pr": 2247, "head_sha": "a" * 40, "diff_sha256": "b" * 64, "diff": "sample"}
        creds = {p: "scoped-key" for p in m.PROVIDERS}
        called = []
        def rejected(provider, key, prompt):
            called.append(provider)
            return ("REJECT", {"findings": ["vulnerability"]})
        self.assertEqual(m.semantic_review(target, creds, rejected)[0], "REJECT")
        self.assertEqual(called, ["anthropic-claude-api"])
        called.clear()
        def transient(provider, key, prompt):
            called.append(provider)
            if provider == "anthropic-claude-api":
                raise m.ProviderUnavailable("PROVIDER_HTTP_429")
            return ("PASS", {"findings": []})
        self.assertEqual(m.semantic_review(target, creds, transient)[0], "PASS")
        self.assertEqual(called, list(m.PROVIDERS))
        called.clear()
        def malformed(provider, key, prompt):
            called.append(provider)
            raise m.Hold("SEMANTIC_SCHEMA_INVALID")
        with self.assertRaises(m.Hold):
            m.semantic_review(target, creds, malformed)
        self.assertEqual(called, ["anthropic-claude-api"])
        with self.assertRaises(m.Hold):
            m.semantic_review(target, {})

    def test_complete_diff_and_stale_sha_required(self):
        pr = {"head": {"sha": "a" * 40, "repo": {"full_name": m.REPO}},
              "base": {"ref": "main", "repo": {"full_name": m.REPO}},
              "state": "open", "changed_files": 1}
        file = {"filename": "a.py", "status": "modified", "sha": "b" * 40,
                "patch": "@@ -1 +1 @@\\n-old\\n+new"}
        def api(path, token):
            return [file] if "/files?" in path else pr
        with patch.object(m, "gh", api):
            self.assertEqual(m.capture(2247, "a" * 40, "read")["head_sha"], "a" * 40)
            with self.assertRaises(m.Hold):
                m.capture(2247, "c" * 40, "read")
            file["patch"] = None
            with self.assertRaises(m.Hold):
                m.capture(2247, "a" * 40, "read")

    def test_untrusted_reviewer_and_missing_result_never_pass(self):
        target = {"pr": 2247, "head_sha": "a" * 40, "diff_sha256": "c" * 64, "diff": "safe"}
        with self.assertRaises(m.Hold):
            m.make_receipt(target, decision="PASS", reviewer="engineering",
                           verdict={}, attempts=[], run_id=1, attempt=1)
        with patch.object(m, "request_json", return_value={"id": 1}) as post:
            m.publish_check("a" * 40, {"decision": "HOLD", "pr_number": 2247}, "qa-app")
            payload = post.call_args.kwargs["data"]
            self.assertEqual(payload["name"], m.CHECK_NAME)
            self.assertEqual(payload["head_sha"], "a" * 40)
            self.assertEqual(payload["conclusion"], "failure")


if __name__ == "__main__":
    unittest.main()
