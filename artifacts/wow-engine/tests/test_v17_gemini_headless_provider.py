"""Contract and negative tests for opt-in cloud Gemini AMRT integration."""
from __future__ import annotations

import importlib.util
import sys
import unittest
import tempfile
import subprocess
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "artifacts" / "wow-engine"))
SRC = ROOT / "artifacts" / "wow-engine" / "v17" / "engineering_gemini_headless.py"
spec = importlib.util.spec_from_file_location("engineering_gemini_headless", SRC)
gemini = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gemini)


def fixture():
    manifest = {
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "restoration": [{
            "issue_number": 1841, "severity": "P0", "priority_rank": 1,
            "execution_lane": "RAPID", "rapid_stream": "LLP_RESTORE",
            "lease_group": "P0_LLP_RESTORE", "conflict_keys": ["NFL_PUBLICATION"],
        }],
        "acceleration": [],
    }
    issues = [{"number": 1841, "title": "NFL stage-proof", "state": "OPEN"}]
    return manifest, issues


class GeminiHeadlessProviderTests(unittest.TestCase):
    def test_disabled_by_default_even_when_manually_forced(self):
        self.assertEqual(gemini.admit("", "true", "true", "manual")["status"], "HELD")
        self.assertEqual(gemini.admit("false", "true", "true", "auto")["status"], "HELD")

    def test_spend_and_auto_route_are_separate_gates(self):
        self.assertEqual(gemini.admit("true", "", "true", "manual")["code"],
                         "GEMINI_SPEND_CAP_UNCONFIRMED")
        self.assertEqual(gemini.admit("true", "true", "", "auto")["code"],
                         "GEMINI_AUTO_ROUTING_DISABLED")
        self.assertEqual(gemini.admit("true", "true", "", "manual")["status"], "ADMITTED")
        self.assertEqual(gemini.admit("true", "true", "true", "auto")["status"], "ADMITTED")

    def test_existing_priority_selected_for_research_not_writer_lease(self):
        manifest, issues = fixture()
        output = gemini.select_incident(manifest, issues)
        self.assertEqual(output["incident_id"], "1841")
        self.assertEqual(output["lease_group"], "P0_LLP_RESTORE")
        self.assertFalse(output["writer_lease_granted"])
        self.assertFalse(output["github_branch_write_allowed"])
        self.assertFalse(output["can_execute"])

    def test_target_identity_and_lease_fails_closed(self):
        manifest, issues = fixture()
        self.assertEqual(
            gemini.select_incident(manifest, issues, target_incident="1841",
                                   requested_lease="P0_LLP_RESTORE")["status"], "SELECTED")
        with self.assertRaisesRegex(ValueError, "LEASE_GROUP_MISMATCH"):
            gemini.select_incident(manifest, issues, target_incident="1841",
                                   requested_lease="GLOBAL")
        with self.assertRaisesRegex(ValueError, "INCIDENT_ID_INVALID"):
            gemini.select_incident(manifest, issues, target_incident="foo")
        self.assertEqual(gemini.select_incident(manifest, [dict(issues[0], state="CLOSED")])["status"],
                         "NO_ACTION")

    def test_provider_failures_are_typed_without_leaking_details(self):
        quota = gemini.classify_cli(1, "sensitive: RESOURCE_EXHAUSTED quota exceeded")
        self.assertEqual(quota["code"], "GEMINI_API_QUOTA_EXHAUSTED")
        self.assertNotIn("sensitive", str(quota))
        self.assertFalse(quota["retry_allowed"])
        self.assertEqual(gemini.classify_cli(124, "", timed_out=True)["code"],
                         "GEMINI_CLI_TIMEOUT")
        self.assertEqual(gemini.classify_cli(53, "limit")["code"], "GEMINI_CLI_TURN_LIMIT")

    def test_valid_result_requires_actual_response_and_no_error(self):
        ok = gemini.classify_cli(0, "", response={"response": "patch candidate", "stats": {}})
        self.assertEqual(ok["status"], "COMPLETED")
        for result in (None, {"response": ""}, {"response": "test", "error": {"code": 429}}):
            self.assertEqual(gemini.classify_cli(0, "", response=result)["status"], "HELD")

    def test_patch_never_claims_publishable_or_approves_trust_roots(self):
        self.assertEqual(gemini.inspect_changed_paths(["artifacts/wow-engine/v17/test.py"])["status"],
                         "CANDIDATE_ONLY")
        for path in (".github/workflows/wow-verify.yml", ".agents/agents/new/agent.md",
                     ".gemini/settings.json", "GEMINI.md", "scripts/other.py",
                     "../outside.py", "/tmp/token",
                     "artifacts/wow-engine/requirements.txt",
                     "artifacts/wow-engine/v17/new_config.yaml",
                     "artifacts/wow-engine/docs/notes.md",
                     "artifacts/wow-engine/tests/helper.py"):
            self.assertEqual(gemini.inspect_changed_paths([path])["status"], "HELD", path)
        self.assertEqual(gemini.inspect_changed_paths([])["status"], "HELD")

    def test_one_dispatcher_and_worker_no_release_permissions(self):
        dispatcher = (ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml").read_text()
        worker = (ROOT / ".github/workflows/wow-v17-gemini-engineering-worker.yml").read_text()
        self.assertIn("gemini_canary_healthy()", dispatcher)
        self.assertIn("GEMINI_AUTO_ROUTING_ENABLED", dispatcher)
        self.assertIn("GEMINI_SPEND_CAP_CONFIRMED", dispatcher)
        self.assertIn("force_provider", dispatcher)
        self.assertIn("wow-v17-gemini-engineering-worker.yml", dispatcher)
        self.assertIn("wow-v17-engineering-implementation-single-writer", worker)
        self.assertIn("ref: main", worker)
        self.assertIn("persist-credentials: false", worker)
        self.assertIn("contents: read", worker)
        self.assertNotIn("pull-requests: write", worker)
        self.assertNotIn("git push", worker)
        self.assertNotIn("gh pr create", worker)
        self.assertIn("GEMINI_API_KEY", worker)
        self.assertIn("timeout 900s", worker)
        self.assertIn("run_shell_command", worker)
        self.assertNotIn("schedule:", worker)
        self.assertIn("can_execute=false", worker)


    def test_auto_routing_has_daily_cost_cooldown_and_manual_canary(self):
        dispatcher = (ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml").read_text()
        start = dispatcher.index("gemini_canary_healthy()")
        end = dispatcher.index("if [ \"$EVENT_NAME\" =", start)
        healthy = dispatcher[start:end]
        self.assertIn("updatedAt", healthy)
        self.assertIn('conclusion" = "success"', healthy)
        self.assertIn('"$age_minutes" -ge 1440', healthy)
        self.assertIn('minutes >= 0 else -1', healthy)
        self.assertIn('elif [ "$force" = "gemini" ]', dispatcher)

    def test_candidate_requires_changed_real_regression_test(self):
        with tempfile.TemporaryDirectory() as root:
            worktree = Path(root)
            source = "artifacts/wow-engine/v17/other_module.py"
            (worktree / source).parent.mkdir(parents=True)
            (worktree / source).write_text("x=1\n")
            result = gemini.verify_candidate(worktree, [source])
            self.assertEqual(result["status"], "HELD")
            self.assertEqual(result["code"], "GEMINI_CHANGED_REGRESSION_TEST_REQUIRED")

    def test_candidate_tests_execute_in_isolated_checkout(self):
        with tempfile.TemporaryDirectory() as root:
            worktree = Path(root)
            target = "artifacts/wow-engine/tests/test_changed_candidate.py"
            (worktree / target).parent.mkdir(parents=True)
            (worktree / target).write_text("def test_ok(): assert True\n")
            source = "artifacts/wow-engine/v17/model_change.py"
            with mock.patch.object(gemini.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["python", "-m", "pytest"], 0, "1 passed", ""
            )) as runner:
                result = gemini.verify_candidate(worktree, [source, target])
            self.assertEqual(result["status"], "TESTED_CANDIDATE")
            self.assertEqual(result["test_files"], [target])
            self.assertEqual(runner.call_args.kwargs["cwd"], worktree)
            self.assertEqual(runner.call_args.kwargs["timeout"], 300)
            self.assertIn(target, runner.call_args.args[0])

    def test_failed_and_timeout_regressions_cannot_pass(self):
        with tempfile.TemporaryDirectory() as root:
            worktree = Path(root)
            target = "artifacts/wow-engine/tests/test_patch_failure.py"
            (worktree / target).parent.mkdir(parents=True)
            (worktree / target).write_text("def test_fail(): assert False\n")
            source = "artifacts/wow-engine/v17/model_change.py"
            with mock.patch.object(gemini.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["pytest"], 1, "FAILED", "error"
            )):
                result = gemini.verify_candidate(worktree, [source, target])
            self.assertEqual(result["code"], "GEMINI_REGRESSION_FAILED")
            self.assertEqual(result["status"], "HELD")
            with mock.patch.object(gemini.subprocess, "run", side_effect=subprocess.TimeoutExpired(["pytest"], 300)):
                result = gemini.verify_candidate(worktree, [source, target])
            self.assertEqual(result["code"], "GEMINI_REGRESSION_TIMEOUT")

    def test_test_only_patch_never_qualifies_as_source_repair(self):
        patch = gemini.inspect_changed_paths(["artifacts/wow-engine/tests/test_only.py"])
        self.assertEqual(patch["status"], "HELD")
        self.assertEqual(patch["code"], "GEMINI_PATCH_SOURCE_CHANGE_REQUIRED")

    def test_worker_parses_nul_delimited_git_and_requires_test_receipt(self):
        worker = (ROOT / ".github/workflows/wow-v17-gemini-engineering-worker.yml").read_text()
        self.assertIn('split(b"\\0")', worker)
        self.assertNotIn('split(b"\\\\0")', worker)
        self.assertIn("verify-candidate --files-json", worker)
        self.assertIn("regression-receipt.json", worker)
        self.assertIn("@google/gemini-cli@0.64.0", worker)
        self.assertIn("gemini --help", worker)
        self.assertIn('"maxSessionTurns": 12', worker)
        self.assertIn('"overageStrategy": "never"', worker)


if __name__ == "__main__":
    unittest.main()
