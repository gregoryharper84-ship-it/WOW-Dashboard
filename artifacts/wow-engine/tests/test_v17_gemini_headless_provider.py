"""Contract and negative tests for opt-in cloud Gemini AMRT integration."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
import tempfile
import subprocess
from contextlib import redirect_stdout
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
        self.assertEqual(gemini.admit("true", "true", "true", "other")["code"],
                         "GEMINI_ADMISSION_MODE_INVALID")

    def test_existing_priority_selected_for_research_not_writer_lease(self):
        manifest, issues = fixture()
        output = gemini.select_incident(manifest, issues)
        self.assertEqual(output["incident_id"], "1841")
        self.assertEqual(output["lease_group"], "P0_LLP_RESTORE")
        self.assertFalse(output["writer_lease_granted"])
        self.assertFalse(output["github_branch_write_allowed"])
        self.assertFalse(output["can_execute"])
        filtered = gemini.select_incident(manifest, issues, requested_lease="P0_LLP_RESTORE")
        self.assertEqual(filtered["incident_id"], "1841")

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
        with self.assertRaisesRegex(ValueError, "LEASE_GROUP_INVALID"):
            gemini.select_incident(manifest, issues, requested_lease="bad")
        self.assertEqual(gemini.select_incident(manifest, [dict(issues[0], state="CLOSED")])["status"],
                         "NO_ACTION")
        std_manifest = {
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
            "restoration": [
                manifest["restoration"][0],
                {
                    "issue_number": 1999, "severity": "P1", "priority_rank": 2,
                    "execution_lane": "STANDARD", "rapid_stream": "",
                    "lease_group": "GLOBAL", "conflict_keys": ["NFL_PUBLICATION"],
                },
            ],
            "acceleration": [],
        }
        std_issues = issues + [{"number": 1999, "title": "Standard conflict", "state": "OPEN"}]
        conflict = gemini.select_incident(std_manifest, std_issues, target_incident="1999",
                                          requested_lease="GLOBAL")
        self.assertEqual(conflict["status"], "NO_ACTION")
        self.assertEqual(conflict["code"], "GEMINI_STANDARD_CONFLICTS_WITH_P0")

    def test_provider_failures_are_typed_without_leaking_details(self):
        quota = gemini.classify_cli(1, "sensitive: RESOURCE_EXHAUSTED quota exceeded")
        self.assertEqual(quota["code"], "GEMINI_API_QUOTA_EXHAUSTED")
        self.assertNotIn("sensitive", str(quota))
        self.assertFalse(quota["retry_allowed"])
        self.assertEqual(gemini.classify_cli(124, "", timed_out=True)["code"],
                         "GEMINI_CLI_TIMEOUT")
        self.assertEqual(gemini.classify_cli(53, "limit")["code"], "GEMINI_CLI_TURN_LIMIT")
        self.assertEqual(gemini.classify_cli(42, "bad input")["code"], "GEMINI_CLI_INPUT_INVALID")
        self.assertEqual(gemini.classify_cli(1, "429 too many requests")["code"],
                         "GEMINI_API_RATE_LIMITED")
        self.assertEqual(gemini.classify_cli(1, "403 permission_denied")["code"],
                         "GEMINI_API_AUTH_FAILED")
        self.assertEqual(gemini.classify_cli(1, "503 unavailable")["code"],
                         "GEMINI_API_UPSTREAM_UNAVAILABLE")
        self.assertEqual(gemini.classify_cli(1, "other crash")["code"], "GEMINI_CLI_FAILED")

    def test_structured_cli_error_is_typed_without_persisting_secrets(self):
        failed = gemini.classify_cli(
            1, "", response={"error": {
                "code": 429, "status": "RESOURCE_EXHAUSTED",
                "message": "quota exceeded: private-token-DO-NOT-PRINT"}})
        self.assertEqual(failed["code"], "GEMINI_API_QUOTA_EXHAUSTED")
        self.assertEqual(failed["exit_code"], 1)
        self.assertTrue(failed["response_error_present"])
        self.assertFalse(failed["stderr_present"])
        self.assertNotIn("private-token", str(failed))
        self.assertFalse(failed["retry_allowed"])
        self.assertEqual(gemini.classify_cli(
            1, "", response={"error": {"message": "Billing required"}})["code"],
            "GEMINI_API_BILLING_REQUIRED")
        self.assertEqual(gemini.classify_cli(
            1, "", response={"error": {"message": "requested model unavailable"}})["code"],
            "GEMINI_MODEL_UNAVAILABLE")
        self.assertEqual(gemini.classify_cli(
            1, "unknown internal CLI exception")["code"], "GEMINI_CLI_FAILED")

    def test_cli_exit_codes_without_api_text(self):
        for exit_code, expected in (
            (41, "GEMINI_API_AUTH_FAILED"),
            (52, "GEMINI_CLI_CONFIG_INVALID"),
            (44, "GEMINI_CLI_SANDBOX_FAILED"),
            (42, "GEMINI_CLI_INPUT_INVALID"),
            (53, "GEMINI_CLI_TURN_LIMIT"),
        ):
            with self.subTest(exit_code=exit_code):
                got = gemini.classify_cli(exit_code, "")
                self.assertEqual(got["code"], expected)
                self.assertEqual(got["exit_code"], exit_code)
                self.assertEqual(got["status"], "HELD")
                self.assertFalse(got["retry_allowed"])

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
        self.assertIn(
            "steps.route.outputs.provider == 'gemini' && steps.remote_fence.outcome == 'success'",
            dispatcher,
        )
        self.assertIn("wow-v17-engineering-implementation-single-writer", worker)
        self.assertIn("engineering_remote_fallback.py guard", worker)
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

    def test_one_shot_recovery_requires_a_new_reviewed_runtime_revision(self):
        previous_sha, current_sha = "a" * 40, "b" * 40
        failed_run = {
            "headSha": previous_sha, "status": "completed", "conclusion": "failure",
        }
        comparison = {
            "status": "ahead",
            "base_commit": {"sha": previous_sha},
            "merge_base_commit": {"sha": previous_sha},
            "total_commits": 2,
            "files": [{
                "filename": "artifacts/wow-engine/v17/engineering_gemini_headless.py",
                "status": "modified",
            }],
        }

        admitted = gemini.admit_one_shot_recovery([failed_run], comparison, current_sha)
        self.assertEqual(admitted["status"], "RECOVERY_ADMITTED")
        self.assertEqual(admitted["code"], "GEMINI_ONE_SHOT_AFTER_REVIEWED_FIX")
        self.assertEqual(admitted["previous_head_sha"], previous_sha)
        self.assertEqual(admitted["current_head_sha"], current_sha)
        self.assertFalse(admitted["can_execute"])
        self.assertEqual(admitted["terminal_authority"], "V17_TERMINAL_REDUCER")

        already_attempted = gemini.admit_one_shot_recovery(
            [failed_run], comparison, previous_sha)
        self.assertEqual(already_attempted["code"], "GEMINI_RECOVERY_ALREADY_ATTEMPTED_SHA")
        unrelated = dict(comparison, files=[{
            "filename": "docs/README.md", "status": "modified",
        }])
        self.assertEqual(
            gemini.admit_one_shot_recovery([failed_run], unrelated, current_sha)["code"],
            "GEMINI_RECOVERY_NO_REVIEWED_RUNTIME_CHANGE",
        )

    def test_one_shot_recovery_fails_closed_on_unverified_run_or_compare(self):
        previous_sha, current_sha = "1" * 40, "2" * 40
        failed_run = {
            "headSha": previous_sha, "status": "completed", "conclusion": "failure",
        }
        comparison = {
            "status": "ahead",
            "base_commit": {"sha": previous_sha},
            "merge_base_commit": {"sha": previous_sha},
            "total_commits": 1,
            "files": [{
                "filename": ".github/workflows/wow-v17-gemini-engineering-worker.yml",
                "status": "modified",
            }],
        }

        def code(runs, compare=comparison, sha=current_sha):
            return gemini.admit_one_shot_recovery(runs, compare, sha)["code"]

        self.assertEqual(code([]), "GEMINI_RECOVERY_LATEST_RUN_UNVERIFIED")
        self.assertEqual(code([failed_run, failed_run]),
                         "GEMINI_RECOVERY_LATEST_RUN_UNVERIFIED")
        self.assertEqual(code([dict(failed_run, status="queued", conclusion=None)]),
                         "GEMINI_RECOVERY_PREVIOUS_RUN_NOT_FAILED")
        self.assertEqual(code([dict(failed_run, status="in_progress", conclusion=None)]),
                         "GEMINI_RECOVERY_PREVIOUS_RUN_NOT_FAILED")
        self.assertEqual(code([dict(failed_run, conclusion="success")]),
                         "GEMINI_RECOVERY_PREVIOUS_RUN_NOT_FAILED")
        self.assertEqual(code([dict(failed_run, headSha="invalid")]),
                         "GEMINI_RECOVERY_PREVIOUS_SHA_INVALID")
        self.assertEqual(code([failed_run], sha="invalid"),
                         "GEMINI_RECOVERY_CURRENT_SHA_INVALID")
        self.assertEqual(code([failed_run], compare=None),
                         "GEMINI_RECOVERY_COMPARE_UNAVAILABLE")
        self.assertEqual(code([failed_run], dict(comparison, status="diverged")),
                         "GEMINI_RECOVERY_SHA_LINEAGE_INVALID")
        self.assertEqual(code([failed_run], dict(comparison, merge_base_commit={"sha": "f" * 40})),
                         "GEMINI_RECOVERY_SHA_LINEAGE_INVALID")
        self.assertEqual(code([failed_run], dict(comparison, files=[{
            "filename": "artifacts/wow-engine/tests/test_gemini.py", "status": "modified",
        }])), "GEMINI_RECOVERY_NO_REVIEWED_RUNTIME_CHANGE")
        self.assertEqual(code([failed_run], dict(comparison, files=[{
            "filename": ".github/workflows/wow-v17-gemini-engineering-worker.yml",
            "status": "removed",
        }])), "GEMINI_RECOVERY_NO_REVIEWED_RUNTIME_CHANGE")
        self.assertEqual(code([failed_run], dict(comparison, total_commits=250)),
                         "GEMINI_RECOVERY_DIFF_TRUNCATED_OR_INVALID")
        self.assertEqual(code([failed_run], dict(comparison, total_commits=True)),
                         "GEMINI_RECOVERY_DIFF_TRUNCATED_OR_INVALID")
        self.assertEqual(code([failed_run], dict(comparison, files=[comparison["files"][0]] * 300)),
                         "GEMINI_RECOVERY_DIFF_TRUNCATED_OR_INVALID")

    def test_recovery_cli_emits_typed_admission_without_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            previous_sha, current_sha = "a" * 40, "b" * 40
            runs_file, comparison_file = root / "runs.json", root / "comparison.json"
            runs_file.write_text(json.dumps([{
                "headSha": previous_sha, "status": "completed", "conclusion": "failure",
            }]))
            comparison_file.write_text(json.dumps({
                "status": "ahead",
                "base_commit": {"sha": previous_sha},
                "merge_base_commit": {"sha": previous_sha},
                "total_commits": 1,
                "files": [{
                    "filename": ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
                    "status": "modified",
                }],
            }))

            output = io.StringIO()
            with redirect_stdout(output):
                code = gemini.main([
                    "recovery-admit", "--runs-file", str(runs_file),
                    "--comparison-file", str(comparison_file),
                    "--current-sha", current_sha,
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "RECOVERY_ADMITTED")

            output = io.StringIO()
            with redirect_stdout(output):
                code = gemini.main([
                    "recovery-admit", "--runs-file", str(runs_file),
                    "--comparison-file", str(comparison_file),
                    "--current-sha", previous_sha,
                ])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "HELD")

    def test_recovery_keeps_shared_dispatcher_safety_and_fallback_paths(self):
        dispatcher = (
            ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
        ).read_text()
        self.assertIn(
            "github.event.workflow_run.name != 'wow-v17-nightly-engineering-scan'",
            dispatcher,
        )
        self.assertIn("github.event_name == 'schedule'", dispatcher)
        self.assertIn(
            "github.event.workflow_run.name == 'wow-v17-nightly-engineering-scan'",
            dispatcher,
        )
        self.assertIn("inputs.force_provider == 'auto'", dispatcher)
        self.assertIn("inputs.force_provider == 'gemini'", dispatcher)
        for marker in (
            "GEMINI_ENGINEERING_ENABLED", "GEMINI_SPEND_CAP_CONFIRMED",
            "GEMINI_AUTO_ROUTING_ENABLED", "wow-v17-engineering-implementation-single-writer",
            "PROVIDER_SURVIVAL_RECEIPT_INVALID", "ROUTER_HEALTHY_PROVIDER_BLOCKED",
            "independent_fallback_allowed", "cursor_dispatch", "remote_fence",
            "GEMINI_ONE_SHOT_AFTER_REVIEWED_FIX",
        ):
            self.assertIn(marker, dispatcher)

    def test_candidate_requires_changed_real_regression_test(self):
        with tempfile.TemporaryDirectory() as root:
            worktree = Path(root)
            source = "artifacts/wow-engine/v17/other_module.py"
            (worktree / source).parent.mkdir(parents=True)
            (worktree / source).write_text("x=1\n")
            result = gemini.verify_candidate(worktree, [source])
            self.assertEqual(result["status"], "HELD")
            self.assertEqual(result["code"], "GEMINI_CHANGED_REGRESSION_TEST_REQUIRED")
            invalid = gemini.verify_candidate(worktree, [".github/workflows/wow-verify.yml"])
            self.assertEqual(invalid["status"], "HELD")
            self.assertEqual(invalid["code"], "GEMINI_PATCH_PROTECTED_OR_INVALID_PATH")
            missing = gemini.verify_candidate(
                worktree, [source, "artifacts/wow-engine/tests/test_missing.py"]
            )
            self.assertEqual(missing["status"], "HELD")
            self.assertEqual(missing["code"], "GEMINI_CHANGED_TEST_PATH_MISSING")

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
            with mock.patch.object(gemini.subprocess, "run", side_effect=OSError("no pytest")):
                result = gemini.verify_candidate(worktree, [source, target])
            self.assertEqual(result["code"], "GEMINI_REGRESSION_RUNNER_UNAVAILABLE")

    def test_test_only_patch_never_qualifies_as_source_repair(self):
        patch = gemini.inspect_changed_paths(["artifacts/wow-engine/tests/test_only.py"])
        self.assertEqual(patch["status"], "HELD")
        self.assertEqual(patch["code"], "GEMINI_PATCH_SOURCE_CHANGE_REQUIRED")

    def test_cli_entrypoints_and_fail_closed_error_handling(self):
        manifest, issues = fixture()
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            manifest_file = base / "manifest.json"
            issues_file = base / "issues.json"
            stderr_file = base / "stderr.txt"
            response_file = base / "response.json"
            files_file = base / "files.json"
            manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
            issues_file.write_text(json.dumps(issues), encoding="utf-8")
            stderr_file.write_text("", encoding="utf-8")
            response_file.write_text(json.dumps({"response": "ok"}), encoding="utf-8")
            src = "artifacts/wow-engine/v17/model_change.py"
            tst = "artifacts/wow-engine/tests/test_candidate.py"
            (base / src).parent.mkdir(parents=True)
            (base / tst).parent.mkdir(parents=True)
            (base / src).write_text("x = 1\n", encoding="utf-8")
            (base / tst).write_text("def test_x(): assert True\n", encoding="utf-8")
            files_file.write_text(json.dumps([src, tst]), encoding="utf-8")

            def call_cli(*argv: str) -> tuple[int, dict]:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = gemini.main(list(argv))
                return rc, json.loads(buf.getvalue())

            rc, out = call_cli(
                "admit", "--enabled", "true", "--spend-confirmed", "true",
                "--auto-enabled", "false", "--mode", "manual",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "ADMITTED")

            rc, out = call_cli(
                "select", "--manifest", str(manifest_file), "--issues", str(issues_file),
                "--target", "1841", "--lease-group", "P0_LLP_RESTORE",
            )
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "SELECTED")

            rc, out = call_cli(
                "classify", "--exit-code", "0", "--stderr-file", str(stderr_file),
                "--response-file", str(response_file),
            )
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "COMPLETED")

            bad_resp = base / "bad.json"
            bad_resp.write_text("{not-json", encoding="utf-8")
            rc, out = call_cli(
                "classify", "--exit-code", "0", "--stderr-file", str(stderr_file),
                "--response-file", str(bad_resp),
            )
            self.assertEqual(rc, 2)
            self.assertEqual(out["code"], "GEMINI_CLI_RESPONSE_MALFORMED")

            rc, out = call_cli("inspect-patch", "--files-json", str(files_file))
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "CANDIDATE_ONLY")

            with mock.patch.object(
                gemini.subprocess, "run",
                return_value=subprocess.CompletedProcess(["pytest"], 0, "1 passed", ""),
            ):
                rc, out = call_cli(
                    "verify-candidate", "--files-json", str(files_file),
                    "--worktree", str(base),
                )
            self.assertEqual(rc, 0)
            self.assertEqual(out["status"], "TESTED_CANDIDATE")

            rc, out = call_cli("inspect-patch", "--files-json", str(base / "nonexistent.json"))
            self.assertEqual(rc, 2)
            self.assertEqual(out["code"], "GEMINI_ADMISSION_OR_EVIDENCE_INVALID")

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
