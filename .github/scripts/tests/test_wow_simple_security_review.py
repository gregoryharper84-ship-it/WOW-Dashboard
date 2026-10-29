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
              "base": {"ref": "main", "sha": "b" * 40, "additions": 1, "deletions": 1, "repo": {"full_name": m.REPO}},
              "state": "open", "changed_files": 1}
        file = {"filename": "a.py", "status": "modified", "sha": "b" * 40, "additions": 1, "deletions": 1,
                "patch": "@@ -1 +1 @@\n-old\n+new"}
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


    def test_security_client_refuses_all_redirects_with_credentials(self):
        from urllib.request import Request
        from urllib.error import HTTPError
        req = Request("https://api.anthropic.com/v1/messages", data=b"{}",
                      headers={"x-api-key": "private-review-key"})
        redirected = m.NoCredentialRedirect().redirect_request(
            req, None, 302, "Found", {"Location": "https://untrusted.example/steal"},
            "https://untrusted.example/steal")
        self.assertIsNone(redirected)
        with self.assertRaises(m.Hold):
            m.request_json("https://untrusted.example/steal", data={})
        with patch.object(m.HTTP_CLIENT, "open",
                          side_effect=HTTPError(req.full_url, 302, "redirect", {}, None)):
            with self.assertRaises(m.Hold) as cm:
                m.request_json("https://api.anthropic.com/v1/messages", data={},
                               headers={"x-api-key": "private-review-key"})
        self.assertNotIsInstance(cm.exception, m.ProviderUnavailable)

    def test_unknown_provider_models_are_denied_before_network(self):
        with patch.dict(m.os.environ, {"WOW_QA_CLAUDE_MODEL": "unapproved-model"}):
            with self.assertRaises(m.Hold) as cm:
                m.call_provider("anthropic-claude-api", "key", "review")
            self.assertEqual(str(cm.exception), "CLAUDE_MODEL_UNAPPROVED")
        with patch.dict(m.os.environ, {"WOW_QA_GEMINI_MODEL": "gemini-unapproved"}):
            with self.assertRaises(m.Hold) as cm:
                m.call_provider("gemini-generative-api", "key", "review")
            self.assertEqual(str(cm.exception), "GEMINI_MODEL_INVALID")



    def test_base_sha_must_be_present_and_valid_in_review_capture(self):
        pr = {"head": {"sha": "a" * 40, "repo": {"full_name": m.REPO}},
              "base": {"ref": "main", "sha": "b" * 40, "additions": 1, "deletions": 1, "repo": {"full_name": m.REPO}},
              "state": "open", "changed_files": 1}
        file = {"filename": "test.py", "status": "modified", "sha": "d" * 40, "additions": 1, "deletions": 1,
                "patch": "@@ -1 +1 @@\n-old\n+new"}
        def api(path, token):
            return [file] if "/files?" in path else pr
        with patch.object(m, "gh", api):
            result = m.capture(2251, "a" * 40, "read")
            self.assertEqual(result["base_sha"], "b" * 40)
            pr["base"]["sha"] = "INVALID"
            with self.assertRaises(m.Hold):
                m.capture(2251, "a" * 40, "read")

    def test_truncated_patch_denied_even_when_nonempty(self):
        pr = {"head": {"sha": "a" * 40, "repo": {"full_name": m.REPO}},
              "base": {"ref": "main", "sha": "b" * 40, "repo": {"full_name": m.REPO}},
              "state": "open", "changed_files": 1}
        file = {"filename": "test.py", "status": "modified", "sha": "d" * 40,
                "additions": 2, "deletions": 1, "patch": "@@ -1 +1 @@\n-old\n+new"}
        def api(path, token):
            return [file] if "/files?" in path else pr
        with patch.object(m, "gh", api):
            with self.assertRaises(m.Hold) as ctx:
                m.capture(2251, "a" * 40, "read")
            self.assertEqual(str(ctx.exception), "DIFF_PATCH_TRUNCATED_OR_INCOMPLETE")

    def test_live_base_or_head_move_denies_old_review(self):
        target = {"head_sha": "a" * 40, "base_sha": "b" * 40}
        current = {"head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}}
        m.require_live_identity(current, target)
        current["base"]["sha"] = "c" * 40
        with self.assertRaises(m.Hold):
            m.require_live_identity(current, target)
        current["base"]["sha"] = "b" * 40
        current["head"]["sha"] = "d" * 40
        with self.assertRaises(m.Hold):
            m.require_live_identity(current, target)



    def test_both_providers_unavailable_requires_owner_review(self):
        target = {"pr": 2254, "head_sha": "a" * 40, "base_sha": "b" * 40,
                  "diff_sha256": "c" * 64, "diff": "safe"}
        creds = {provider: "key" for provider in m.PROVIDERS}
        def unavailable(provider, key, prompt):
            raise m.ProviderUnavailable("PROVIDER_HTTP_429")
        with self.assertRaises(m.OwnerFallbackRequired) as cm:
            m.semantic_review(target, creds, unavailable)
        self.assertEqual([item["provider"] for item in cm.exception.attempts], list(m.PROVIDERS))
        self.assertEqual(str(cm.exception), "OWNER_REVIEW_REQUIRED")
        with self.assertRaises(m.OwnerFallbackRequired) as missing:
            m.semantic_review(target, {}, unavailable)
        self.assertTrue(all(x["result"] == "CREDENTIAL_UNAVAILABLE" for x in missing.exception.attempts))

    def _owner_fixture(self, source_author="independent-engineer"):
        target = {"pr": 2254, "author": "wow-engineering[bot]",
                  "head_sha": "a" * 40, "base_sha": "b" * 40,
                  "diff_sha256": "c" * 64, "file_blobs": {"src.py": "d" * 40}}
        pr = {"head": {"sha": target["head_sha"]}, "base": {"sha": target["base_sha"]},
              "body": f"Original author/source: PR #2248, head `{'e' * 40}`.\n"}
        src = {"head": {"sha": "e" * 40, "repo": {"full_name": m.REPO}},
               "changed_files": 1, "user": {"login": source_author}}
        reviews = [{"id": 123, "user": {"login": m.OWNER_LOGIN}, "state": "APPROVED",
                    "commit_id": "a" * 40, "submitted_at": "2026-10-29T13:20:00Z",
                    "body": m.owner_review_instruction(target)}]
        def gh(path, token):
            if path == "/pulls/2254":
                return pr
            if path == "/pulls/2248":
                return src
            if path.startswith("/pulls/2248/files?"):
                return [{"filename": "src.py", "sha": "d" * 40}]
            if path.startswith("/pulls/2254/reviews?"):
                return reviews
            raise AssertionError(path)
        return target, pr, src, reviews, gh

    def test_authenticated_owner_third_review_can_pass_distinct_source_only(self):
        target, pr, src, reviews, fake = self._owner_fixture()
        with patch.object(m, "gh", fake):
            v = m.owner_fallback_review(target, "read")
        self.assertEqual(v["review_id"], 123)
        self.assertEqual(v["source_author"], "independent-engineer")
        receipt = m.make_receipt(target, decision="PASS", reviewer=m.OWNER_REVIEWER,
                                 verdict={"security_review": "PASS"}, attempts=[],
                                 run_id=5, attempt=1)
        self.assertEqual(receipt["reviewer_identity"], m.OWNER_REVIEWER)

    def test_owner_cannot_certify_own_original_source_even_on_app_copy(self):
        target, pr, src, reviews, fake = self._owner_fixture(m.OWNER_LOGIN)
        with patch.object(m, "gh", fake):
            with self.assertRaises(m.Hold) as cm:
                m.owner_fallback_review(target, "read")
        self.assertEqual(str(cm.exception), "OWNER_IS_SOURCE_AUTHOR_OR_IDENTITY_UNVERIFIED")
        target["author"] = m.OWNER_LOGIN
        with patch.object(m, "gh", fake):
            with self.assertRaises(m.Hold):
                m.owner_fallback_review(target, "read")

    def test_owner_fallback_rejects_bad_review_or_stale_source(self):
        target, pr, src, reviews, fake = self._owner_fixture()
        reviews[0]["body"] = "LGTM"
        with patch.object(m, "gh", fake):
            with self.assertRaises(m.Hold) as cm:
                m.owner_fallback_review(target, "read")
        self.assertEqual(str(cm.exception), "OWNER_REVIEW_NOT_EXACT_OR_NOT_AUTHENTICATED")
        reviews[0]["body"] = m.owner_review_instruction(target)
        reviews[0]["state"] = "DISMISSED"
        with patch.object(m, "gh", fake):
            with self.assertRaises(m.Hold):
                m.owner_fallback_review(target, "read")
        reviews[0]["state"] = "APPROVED"
        src["head"]["sha"] = "f" * 40
        with patch.object(m, "gh", fake):
            with self.assertRaises(m.Hold) as cm:
                m.owner_fallback_review(target, "read")
        self.assertEqual(str(cm.exception), "OWNER_SOURCE_SHA_OR_FILES_UNVERIFIED")

    def test_owner_action_required_check_is_never_a_success(self):
        with patch.object(m, "request_json", return_value={"id": 1}) as api:
            m.publish_check("a" * 40, {"decision": "HOLD",
                           "reason": "OWNER_REVIEW_REQUIRED", "pr_number": 2254}, "qa-app")
            self.assertEqual(api.call_args.kwargs["data"]["conclusion"], "action_required")
            m.publish_check("a" * 40, {"decision": "HOLD",
                           "reason": "OWNER_IS_SOURCE_AUTHOR_OR_IDENTITY_UNVERIFIED"}, "qa-app")
            self.assertEqual(api.call_args.kwargs["data"]["conclusion"], "failure")


if __name__ == "__main__":
    unittest.main()
