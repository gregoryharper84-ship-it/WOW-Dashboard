"""Fail-closed tests for advisory reviewer-evidence auditing."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "v17_approval_readiness.py"
spec = importlib.util.spec_from_file_location("v17_approval_readiness", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

HEAD = "a" * 40


def pr(draft=False, author="implementer"):
    return {
        "number": 1538,
        "head": {"sha": HEAD},
        "user": {"login": author},
        "draft": draft,
        "requested_reviewers": [],
    }


def review(user, state="APPROVED", commit=HEAD, timestamp="2026-10-08T14:00:00Z"):
    return {
        "user": {"login": user},
        "state": state,
        "commit_id": commit,
        "submitted_at": timestamp,
    }


class ApprovalReadinessTests(unittest.TestCase):
    def test_empty_paths_fail_closed(self):
        v = module.evaluate(pr(), [], [], ["qa"], ["release"])
        self.assertIn("CHANGED_FILE_LIST_UNAVAILABLE", v["blockers"])
        self.assertEqual(v["risk_class"], "R3_PROTECTED_UNKNOWN")

    def test_docs_low_risk(self):
        self.assertEqual(module.risk_class(["docs/example.md"]), "R1_DOCUMENTATION")

    def test_specialist_code_is_class_c(self):
        self.assertEqual(
            module.risk_class(["artifacts/wow-engine/v17/nfl_specialist.py"]),
            "CLASS_C_REVIEW",
        )

    def test_editor_instruction_file_is_protected(self):
        self.assertEqual(
            module.risk_class(["artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"]),
            "R3_PROTECTED",
        )

    def test_workflow_protected_even_when_yaml(self):
        self.assertEqual(module.risk_class([".github/workflows/new.yml"]), "R3_PROTECTED")

    def test_generic_runtime_requires_release_reviewer(self):
        v = module.evaluate(pr(), ["api/handler.py"], [review("qa")], ["qa"], ["rel"])
        self.assertIn("RELEASE_APPROVAL_MISSING", v["blockers"])

    def test_missing_reviewers_never_admits_ready(self):
        v = module.evaluate(pr(), ["docs/readme.md"], [review("qa")], [], [])
        self.assertIn("INDEPENDENT_QA_REVIEWER_UNCONFIGURED", v["blockers"])

    def test_author_cannot_approve_self(self):
        v = module.evaluate(pr(author="qa"), ["docs/readme.md"], [review("qa")], ["qa"], [])
        self.assertIn("INDEPENDENT_QA_REVIEWER_UNCONFIGURED", v["blockers"])

    def test_bot_cannot_approve(self):
        v = module.evaluate(pr(), ["docs/readme.md"], [review("qa[bot]")], ["qa"], [])
        self.assertIn("INDEPENDENT_QA_APPROVAL_MISSING", v["blockers"])

    def test_unlisted_reviewer_does_not_count(self):
        v = module.evaluate(pr(), ["docs/readme.md"], [review("stranger")], ["qa"], [])
        self.assertIn("INDEPENDENT_QA_APPROVAL_MISSING", v["blockers"])

    def test_stale_head_does_not_count(self):
        v = module.evaluate(pr(), ["docs/readme.md"], [review("qa", commit="b" * 40)], ["qa"], [])
        self.assertIn("STALE_HEAD_APPROVAL", v["blockers"])

    def test_latest_changes_requested_overrides_approval(self):
        v = module.evaluate(
            pr(), ["docs/readme.md"],
            [review("qa"), review("qa", state="CHANGES_REQUESTED", timestamp="2026-10-08T15:00:00Z")],
            ["qa"], [])
        self.assertIn("INDEPENDENT_QA_APPROVAL_MISSING", v["blockers"])

    def test_draft_cannot_advance(self):
        v = module.evaluate(pr(draft=True), ["docs/readme.md"], [review("qa")], ["qa"], [])
        self.assertIn("DRAFT_PR", v["blockers"])
        self.assertEqual(v["qa_request_candidates"], [])

    def test_separate_qa_release_signers(self):
        v = module.evaluate(pr(), ["api/run.py"],
                            [review("qa")], ["qa"], ["qa"])
        self.assertIn("INDEPENDENT_RELEASE_APPROVER_UNCONFIGURED", v["blockers"])

    def test_two_distinct_approvals_are_still_not_merge_authority(self):
        v = module.evaluate(pr(), ["api/run.py"],
                            [review("qa"), review("rel")], ["qa"], ["rel"])
        self.assertEqual(v["status"], "REVIEW_RECEIPTS_PRESENT_NOT_MERGE_AUTHORIZED")
        self.assertFalse(v["merge_authorized"])
        self.assertFalse(v["production_deploy_authorized"])
        self.assertFalse(v["can_execute"])

    def test_author_and_automated_bots_are_invalid_config(self):
        for name in ("qa[bot]", "github-actions", "INVALID/USER"):
            with self.assertRaises(ValueError):
                module.usernames(name)

    def test_request_candidates_do_not_include_existing_request_or_author(self):
        p = pr()
        p["requested_reviewers"] = [{"login": "qa1"}]
        v = module.evaluate(p, ["docs/readme.md"], [], ["qa1", "qa2"], ["rel"])
        self.assertEqual(v["qa_request_candidates"], ["qa2"])

    def test_negative_input_outputs_held_without_throw(self):
        with tempfile.TemporaryDirectory() as temp:
            d = Path(temp)
            (d / "pr.json").write_text("{}")
            (d / "files.json").write_text('"not an array"')
            (d / "reviews.json").write_text("[]")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--pr-json", str(d / "pr.json"),
                 "--files-json", str(d / "files.json"),
                 "--reviews-json", str(d / "reviews.json")],
                text=True, capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout)["status"], "HELD")
            self.assertFalse(json.loads(result.stdout)["merge_authorized"])


if __name__ == "__main__":
    unittest.main()
