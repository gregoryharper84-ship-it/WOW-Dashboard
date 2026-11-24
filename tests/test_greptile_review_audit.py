"""Offline failure-mode regressions for read-only Greptile review inventory."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "greptile_review_audit.py"
spec = importlib.util.spec_from_file_location("greptile_review_audit", PATH)
audit_mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(audit_mod)

HEAD = "a" * 40
BASE_PR = {"number": 123, "head_sha": HEAD}


def thread(resolved, outdated=False, author="greptile-apps"):
    return {"id": "PRRT_example", "isResolved": resolved, "isOutdated": outdated,
            "comments": {"nodes": [{"author": {"login": author},
                                    "body": "P1: source validation fails closed", "url": "https://github.com/example/repo/pull/123#discussion_r1"}]}}


def review(commit=HEAD, author="greptile-apps"):
    return {"author": {"login": author}, "commit": {"oid": commit}, "state": "COMMENTED"}


class AuditClassificationTests(unittest.TestCase):
    def test_unresolved_greptile_is_open_even_when_outdated(self):
        row = audit_mod.classify_pr(BASE_PR, [thread(False, True)], [review()], [])
        self.assertEqual(row["status"], "UNRESOLVED_GREPTILE_FINDING")
        self.assertEqual(len(row["unresolved_threads"]), 1)
        self.assertTrue(row["unresolved_threads"][0]["outdated"])

    def test_resolved_thread_not_presented_as_open(self):
        row = audit_mod.classify_pr(BASE_PR, [thread(True)], [review()], [])
        self.assertEqual(row["status"], "GREPTILE_REVIEW_AT_HEAD_NO_OPEN_THREADS")
        self.assertEqual(row["resolved_greptile_threads"], 1)

    def test_other_bot_thread_does_not_count(self):
        row = audit_mod.classify_pr(BASE_PR, [thread(False, author="coderabbitai")], [], [])
        self.assertEqual(row["status"], "NO_EXACT_HEAD_GREPTILE_REVIEW_EVIDENCE")
        self.assertFalse(row["unresolved_threads"])

    def test_human_mention_does_not_create_credit_pause(self):
        self.assertFalse(audit_mod.credit_pause({"user": {"login": "reviewer"},
            "body": "Greptile ran out of review credits"}))

    def test_real_credit_pause_is_blocked_not_clean(self):
        credit = {"user": {"login": "greptile-apps"}, "body": "Your organization has run out of review credits."}
        row = audit_mod.classify_pr(BASE_PR, [], [], [credit])
        self.assertEqual(row["status"], "GREPTILE_CREDITS_EXHAUSTED")

    def test_prior_commit_review_is_stale(self):
        row = audit_mod.classify_pr(BASE_PR, [thread(True)], [review("b" * 40)], [])
        self.assertEqual(row["status"], "GREPTILE_REVIEW_STALE_HEAD")
        self.assertEqual(row["exact_head_greptile_reviews"], 0)

    def test_unknown_thread_resolution_does_not_succeed(self):
        with self.assertRaises(audit_mod.AuditUnavailable):
            audit_mod.classify_pr(BASE_PR, [{"isOutdated": False, "comments": {"nodes": []}}], [], [])

    def test_invalid_head_does_not_succeed(self):
        with self.assertRaises(audit_mod.AuditUnavailable):
            audit_mod.classify_pr({"number": 123, "head_sha": "short"}, [], [], [])

    def test_unresolved_remains_blocker_even_if_credit_pause(self):
        credit = {"user": {"login": "greptile-apps"}, "body": "out of review credits"}
        row = audit_mod.classify_pr(BASE_PR, [thread(False)], [], [credit])
        self.assertEqual(row["status"], "UNRESOLVED_GREPTILE_FINDING")
        self.assertTrue(row["credit_pause_observed"])


class AuditResultTests(unittest.TestCase):
    class Client:
        def __init__(self, prs, threads=(), reviews=(), comments=(), fail=False):
            self.prs, self.threads, self.reviews, self.comments = prs, threads, reviews, comments
            self.fail = fail
        def pulls(self, repository):
            return self.prs
        def threads_and_reviews(self, repository, number):
            if self.fail:
                raise audit_mod.AuditUnavailable("GITHUB_HTTP_403")
            return list(self.threads), list(self.reviews)
        def issue_comments(self, repository, number):
            return list(self.comments)

    def test_missing_reviews_in_full_scan_not_clean(self):
        report = audit_mod.audit(self.Client([BASE_PR]), "owner/repo")
        self.assertEqual(report["outcome"], "GREPTILE_REVIEW_COVERAGE_UNPROVEN")
        self.assertFalse(report["can_execute"])

    def test_partial_scan_never_marked_complete(self):
        report = audit_mod.audit(self.Client([BASE_PR, {"number": 124, "head_sha": "b" * 40}],
            reviews=[review()]), "owner/repo", max_prs=1)
        self.assertEqual(report["outcome"], "AUDIT_INCOMPLETE")
        self.assertTrue(report["partial"])

    def test_permission_failure_blocks_with_no_retry_storm(self):
        report = audit_mod.audit(self.Client([BASE_PR], fail=True), "owner/repo")
        self.assertEqual(report["outcome"], "AUDIT_INCOMPLETE")
        self.assertEqual(len(report["audit_errors"]), 1)

    def test_inventory_drift_fails_closed(self):
        class DriftingClient(self.Client):
            def __init__(self):
                super().__init__([BASE_PR], reviews=[review()])
                self.calls = 0
            def pulls(self, repository):
                self.calls += 1
                return [BASE_PR] if self.calls == 1 else [{"number": 123, "head_sha": "b" * 40}]
        report = audit_mod.audit(DriftingClient(), "owner/repo")
        self.assertEqual(report["outcome"], "AUDIT_INCOMPLETE")
        self.assertEqual(report["audit_errors"][0]["error"], "GITHUB_OPEN_PR_INVENTORY_DRIFT")

    def test_exact_head_review_is_not_release_approval(self):
        report = audit_mod.audit(self.Client([BASE_PR], reviews=[review()]), "owner/repo")
        self.assertEqual(report["outcome"], "NO_OPEN_GREPTILE_FINDINGS_IN_EXACT_HEAD_REVIEWS")
        self.assertEqual(report["release_approval"], "NOT_ASSESSED_BY_THIS_AUDIT")


if __name__ == "__main__":
    unittest.main()
