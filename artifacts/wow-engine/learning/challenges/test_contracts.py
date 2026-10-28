"""Exercises MUST be explicitly launched with run_challenge_tests.py.

Intentional starter failures are not part of the normal production test suite.
"""
import importlib
import os
import unittest

impl = importlib.import_module(os.environ.get("WOW_CHALLENGE_IMPL", "reference"))
SHA = "a" * 40


class QueueContract(unittest.TestCase):
    def test_conflicted_old_does_not_starve_next(self):
        self.assertEqual(impl.select_adoption_source([{"number": 1573, "mergeable": False}, {"number": 2156, "mergeable": True}], 1), 2156)

    def test_wip_at_capacity_blocks(self):
        self.assertIsNone(impl.select_adoption_source([{"number": 2, "mergeable": True}], 3))

    def test_unknown_mergeability_not_admitted(self):
        self.assertIsNone(impl.select_adoption_source([{"number": 3, "mergeable": None}], 0))

    def test_fair_rotation(self):
        self.assertEqual(impl.select_adoption_source([{"number": 2, "mergeable": True}, {"number": 3, "mergeable": True}], 0, cursor=1), 3)

    def test_all_conflicted_none(self):
        self.assertIsNone(impl.select_adoption_source([{"number": 2, "mergeable": False}], 0))


class QaContract(unittest.TestCase):
    def setUp(self):
        self.pr = {"head_sha": SHA, "author": "wow-engineering[bot]", "author_is_owner": False, "native_ci_green": True}
        self.check = {"app_id": 5245832, "head_sha": SHA, "conclusion": "failure", "completed_at": 100}
        self.review = {"state": "APPROVED", "head_sha": SHA, "authenticated": True, "submitted_at": 200, "actor": "owner"}
        self.res = {"signed": True, "previous_count": 1, "previous_terminal_failed": True, "ambiguous_ack": False, "competing_writer": False}

    def test_authenticated_newer_review_allows_one_reentry(self):
        self.assertTrue(impl.authenticated_qa_reentry(self.pr, self.check, self.review, self.res))

    def test_owner_authored_never_self_heals(self):
        self.pr["author_is_owner"] = True
        self.assertFalse(impl.authenticated_qa_reentry(self.pr, self.check, self.review, self.res))

    def test_old_review_is_not_new_prerequisite(self):
        self.review["submitted_at"] = 90
        self.assertFalse(impl.authenticated_qa_reentry(self.pr, self.check, self.review, self.res))

    def test_ambiguous_ack_fail_closed(self):
        self.res["ambiguous_ack"] = True
        self.assertFalse(impl.authenticated_qa_reentry(self.pr, self.check, self.review, self.res))

    def test_wrong_app_denied(self):
        self.check["app_id"] = 999
        self.assertFalse(impl.authenticated_qa_reentry(self.pr, self.check, self.review, self.res))

    def test_competing_writer_denied(self):
        self.res["competing_writer"] = True
        self.assertFalse(impl.authenticated_qa_reentry(self.pr, self.check, self.review, self.res))


class LeaseContract(unittest.TestCase):
    def setUp(self):
        self.res = {"comment_id": 42, "agent_id": "cursor-a", "run_id": "run-a", "source_sha": SHA, "digest": "sha256:abc", "hold": True, "released": False}
        self.terminal = {"comment_id": 42, "agent_id": "cursor-a", "run_id": "run-a", "source_sha": SHA, "original_digest": "sha256:abc", "authenticated": True, "status": "FINISHED", "idle": True}
        self.active = [self.res]

    def test_authenticated_exact_finished_release(self):
        self.assertTrue(impl.safe_release(self.res, self.terminal, self.active))

    def test_active_run_no_release(self):
        self.terminal["status"] = "RUNNING"
        self.assertFalse(impl.safe_release(self.res, self.terminal, self.active))

    def test_forged_issuer_denied(self):
        self.terminal["authenticated"] = False
        self.assertFalse(impl.safe_release(self.res, self.terminal, self.active))

    def test_wrong_comment_denied(self):
        self.terminal["comment_id"] = 41
        self.assertFalse(impl.safe_release(self.res, self.terminal, self.active))

    def test_competing_held_writer_denied(self):
        self.active.append({"comment_id": 43, "hold": True})
        self.assertFalse(impl.safe_release(self.res, self.terminal, self.active))

    def test_source_sha_changed_denied(self):
        self.terminal["source_sha"] = "b" * 40
        self.assertFalse(impl.safe_release(self.res, self.terminal, self.active))

    def test_duplicate_held_ledger_denied(self):
        self.active.append(self.res.copy())
        self.assertFalse(impl.safe_release(self.res, self.terminal, self.active))

    def test_mismatched_agent_denied(self):
        self.terminal["agent_id"] = "cursor-b"
        self.assertFalse(impl.safe_release(self.res, self.terminal, self.active))


if __name__ == "__main__":
    unittest.main()
