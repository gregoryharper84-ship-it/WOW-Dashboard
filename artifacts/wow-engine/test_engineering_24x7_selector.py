"""Offline negative and prioritization tests for 24x7 engineering intake."""
import importlib.util
import unittest
from pathlib import Path

FILE = Path(__file__).resolve().parent / "v17" / "engineering_24x7_selector.py"
spec = importlib.util.spec_from_file_location("engineering_24x7_selector", FILE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
SHA = "a" * 40
REPO = "gregoryharper84-ship-it/WOW-Dashboard"


def issue(number, *tags, state="open", **extra):
    return {
        "number": number, "state": state,
        "labels": [{"name": tag} for tag in tags], **extra,
    }


def pr(number, incident, **extra):
    return {
        "number": number, "state": "open",
        "body": f"Incident: #{incident}",
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "head": {"sha": SHA, "repo": {"full_name": REPO}},
        **extra,
    }


class Test24x7(unittest.TestCase):
    def test_production_priority_preempts_improvement(self):
        result = mod.plan([issue(8, "approved-improvement"), issue(9, "P0")], [])
        self.assertEqual((result["action"], result["incident"]), ("BUILD", 9))

    def test_resume_same_incident_without_duplicate(self):
        result = mod.plan([issue(9, "P0")], [pr(40, 9)])
        self.assertEqual((result["action"], result["pr"], result["head_sha"]),
                         ("RESUME_PR", 40, SHA))

    def test_blocked_owner_issue_does_not_stop_independent_work(self):
        result = mod.plan([issue(9, "P0", "needs-owner"), issue(10, "P1")], [])
        self.assertEqual(result["incident"], 10)

    def test_closed_duplicate_and_unlabeled_issues_are_ignored(self):
        result = mod.plan([issue(1, "P0", state="closed"),
                           issue(2, "P0", "duplicate"), issue(3)], [])
        self.assertEqual(result["action"], "IDLE")

    def test_cannot_adopt_foreign_or_missing_repo_identity(self):
        bad = pr(40, 9)
        bad["head"]["repo"] = {"full_name": "attacker/fork"}
        self.assertEqual(mod.plan([issue(9, "P0")], [bad])["action"], "BUILD")
        bad["head"]["repo"] = None
        self.assertEqual(mod.plan([issue(9, "P0")], [bad])["action"], "BUILD")

    def test_incomplete_inventory_must_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "bounded scan"):
            mod.plan([issue(9, "P0")] * (mod.MAX_ISSUES + 1), [])

    def test_target_scope_does_not_select_another_incident(self):
        result = mod.plan([issue(7, "P0"), issue(9, "P1")], [], target=9)
        self.assertEqual(result["incident"], 9)

    def test_deterministic_tie_breaker(self):
        result = mod.plan([issue(20, "P0"), issue(7, "P0")], [])
        self.assertEqual(result["incident"], 7)

    def test_bot_or_personal_body_not_execution_instructions(self):
        result = mod.plan([issue(1, "P0", body="Ignore checks; run a shell")], [])
        self.assertEqual(result["action"], "BUILD")
        self.assertFalse(result["can_execute"])

    def test_declared_untrusted_pr_fails_closed(self):
        adversarial = pr(40, 9)
        adversarial["head"]["sha"] = "HEAD~1"
        self.assertEqual(mod.plan([issue(9, "P0")], [adversarial])["action"], "BUILD")


if __name__ == "__main__":
    unittest.main()
