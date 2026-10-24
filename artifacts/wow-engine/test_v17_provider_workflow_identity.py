"""Execute the real provider-routing shell with bounded provider/CLI fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"


class ProviderWorkflowIdentityTests(unittest.TestCase):
    def route(self, source, *, auth=True, code="OPENAI_API_QUOTA_EXCEEDED",
              eligible=True, conclusion="failure", title="lease=GLOBAL incident=AUTO",
              secondary="success"):
        text = WORKFLOW.read_text()
        section = text.split("      - name: Resolve provider action", 1)[1]
        script = section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        script = textwrap.dedent(script)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = run ] && [ \"$2\" = list ]; then\n"
                "  printf '[{\"databaseId\":901,\"conclusion\":\"%s\"}]\\n' \"$FAKE_SECONDARY\"\n"
                "else printf 'fixture failed worker log\\n'; fi\n"
            )
            gh.chmod(0o755)
            python = bin_dir / "python"
            classification = json.dumps({"code":code, "failover_eligible":eligible})
            python.write_text("#!/bin/sh\nprintf '%s\\n' '" + classification + "'\n")
            python.chmod(0o755)
            output = root / "outputs"
            output.touch()
            env = dict(os.environ, PATH=str(bin_dir)+":"+os.environ["PATH"],
                       RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(output),
                       GITHUB_RUN_ID="900", GITHUB_REPOSITORY="fixture/repo",
                       EVENT_NAME="workflow_run", SOURCE_WORKFLOW=source,
                       SOURCE_RUN_ID="123", SOURCE_CONCLUSION=conclusion,
                       SOURCE_DISPLAY_TITLE=title, MANUAL_TARGET_INCIDENT="",
                       MANUAL_LEASE_GROUP="", ANTHROPIC_API_KEY="fixture" if auth else "",
                       CLAUDE_CODE_OAUTH_TOKEN="", OPENAI_API_KEY="",
                       FAKE_SECONDARY=secondary)
            result = subprocess.run(["bash","-c",script], env=env,
                                    capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            receipt = json.loads((root/"provider-route.json").read_text())
            self.assertFalse(receipt["can_execute"])
            self.assertEqual(receipt["terminal_authority"],"V17_TERMINAL_REDUCER")
            self.assertEqual(receipt["parent_run_id"],"123")
            return receipt

    def test_static_and_observed_openai_identity_dispatch_claude(self):
        for source in ("wow-v17-chatgpt-engineering-worker",
                       "wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO"):
            with self.subTest(source=source):
                receipt=self.route(source)
                self.assertEqual(receipt["action"],"DISPATCH")
                self.assertEqual(receipt["provider"],"anthropic")
                self.assertEqual(receipt["reason"],"OPENAI_API_QUOTA_EXCEEDED")

    def test_missing_fallback_auth_is_fail_closed(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
                           auth=False)
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertEqual(receipt["reason"],"OPENAI_API_QUOTA_EXCEEDED:ANTHROPIC_UNAVAILABLE_OR_FAILED")

    def test_latest_failed_secondary_is_not_retried(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker",
                           secondary="failure")
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertEqual(receipt["provider"],"deterministic")
        self.assertEqual(receipt["reason"],
                         "OPENAI_API_QUOTA_EXCEEDED:ANTHROPIC_UNAVAILABLE_OR_FAILED")

    def test_non_provider_failure_never_dispatches_fallback(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
                           code="NON_PROVIDER_FAILURE",eligible=False)
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertFalse(receipt["independent_fallback_allowed"])

    def test_governance_boundary_never_dispatches_independent_fallback(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker",
                           code="ACTIONABLE_REPAIR_POLICY_BOUNDARY",eligible=False)
        self.assertFalse(receipt["independent_fallback_allowed"])

    def test_exhausted_primary_and_secondary_admit_only_independent_fallback(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker", secondary="failure")
        self.assertTrue(receipt["independent_fallback_allowed"])
        self.assertEqual(receipt["cursor_status"], "CURSOR_NOT_CONFIGURED")

    def test_claude_failure_does_not_loop_back_to_openai(self):
        receipt=self.route("wow-v17-claude-engineering-worker lease=GLOBAL incident=AUTO",
                           code="ANTHROPIC_API_AUTH_FAILED",eligible=False)
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertEqual(receipt["reason"],"ANTHROPIC_API_AUTH_FAILED")

    def test_unknown_and_lookalike_names_remain_unknown(self):
        for source in ("unrelated-workflow lease=GLOBAL incident=AUTO",
                       "wow-v17-chatgpt-engineering-worker-lookalike"):
            with self.subTest(source=source):
                receipt=self.route(source)
                self.assertEqual(receipt["action"],"SURVIVAL")
                self.assertEqual(receipt["reason"],"UNKNOWN_ENGINEERING_WORKFLOW_FAILURE")

    def test_exact_target_and_domain_lease_propagate(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=RUNTIME incident=1021",
                           title="lease=RUNTIME incident=1021")
        self.assertEqual(receipt["target_incident"],"1021")
        self.assertEqual(receipt["lease_group"],"RUNTIME")

    def test_success_does_not_start_another_worker(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
                           conclusion="success")
        self.assertEqual(receipt["action"],"NO_ACTION")

    def test_quota_and_failed_secondary_produce_typed_blocked_route_receipt(self):
        receipt = self.route(
            "wow-v17-chatgpt-engineering-worker",
            secondary="failure",
            title="lease=GLOBAL incident=2034",
        )
        self.assertEqual(receipt["action"], "SURVIVAL")
        self.assertEqual(receipt["status"], "BLOCKED_WITH_EXACT_REASON")
        self.assertEqual(
            receipt["reason"],
            "OPENAI_API_QUOTA_EXCEEDED:ANTHROPIC_UNAVAILABLE_OR_FAILED",
        )
        self.assertEqual(receipt["issue_id"], "2034")
        self.assertFalse(receipt["provider_generation_allowed"])
        self.assertFalse(receipt["can_execute"])


class ProviderControlPlaneHandoffTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def step_script(self, name):
        block = self.text.split(f"      - name: {name}\n", 1)[1]
        if name == "Surface degraded provider state":
            block = block.split("\n  independent-provider:", 1)[0]
        else:
            block = block.split("\n      - name:", 1)[0]
        return textwrap.dedent(block.split("        run: |\n", 1)[1])

    def run_step(self, name, env):
        return subprocess.run(
            ["bash", "-e", "-c", self.step_script(name)],
            env=dict(os.environ, **env),
            capture_output=True,
            text=True,
            timeout=10,
        )

    def route_receipt(self):
        return {
            "action": "SURVIVAL",
            "status": "BLOCKED_WITH_EXACT_REASON",
            "provider": "deterministic",
            "reason": "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED:SECONDARY_UNAVAILABLE_OR_FAILED",
            "parent_run_id": "901",
            "target_incident": "2034",
            "issue_id": "2034",
            "lease_group": "P0_LLP_RESTORE",
            "independent_fallback_allowed": True,
            "provider_generation_allowed": False,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }

    def validate(self, directory, *, route=None, survival=None, **overrides):
        route_path = Path(directory) / "provider-route.json"
        survival_path = Path(directory) / "provider-survival-receipt.json"
        route_path.write_text(json.dumps(self.route_receipt() if route is None else route))
        survival_path.write_text(json.dumps(survival or {
            "status": "DEGRADED_DETERMINISTIC_SURVIVAL",
            "reason": self.route_receipt()["reason"],
            "parent_run_id": "901",
            "priority": {"incident_id": "PM-TEST-001"},
            "provider_generation_allowed": False,
            "deterministic_checks_allowed": True,
            "probability_authority": False,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }))
        env = {
            "ROUTE_RECEIPT": str(route_path),
            "SURVIVAL_RECEIPT": str(survival_path),
            "ACTION": "SURVIVAL",
            "PROVIDER": "deterministic",
            "REASON": self.route_receipt()["reason"],
            "PARENT_RUN_ID": "901",
            "TARGET_INCIDENT": "2034",
            "LEASE_GROUP": "P0_LLP_RESTORE",
            "ROUTE_OUTCOME": "success",
            "REMOTE_FENCE_OUTCOME": "success",
            "SURVIVAL_OUTCOME": "success",
            "OPENAI_DISPATCH_OUTCOME": "skipped",
            "ANTHROPIC_DISPATCH_OUTCOME": "skipped",
        }
        env.update(overrides)
        return self.run_step("Validate provider-control receipts", env)

    def surface(self, **overrides):
        env = {
            "ACTION": "SURVIVAL",
            "REASON": self.route_receipt()["reason"],
            "ROUTE_OUTCOME": "success",
            "REMOTE_FENCE_OUTCOME": "success",
            "SURVIVAL_OUTCOME": "success",
            "OPENAI_DISPATCH_OUTCOME": "skipped",
            "ANTHROPIC_DISPATCH_OUTCOME": "skipped",
            "RECEIPT_VALIDATION_OUTCOME": "success",
            "RECEIPT_UPLOAD_OUTCOME": "success",
        }
        env.update(overrides)
        return self.run_step("Surface degraded provider state", env)

    def test_fully_recorded_quota_hold_is_router_healthy_but_not_repair_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            validated = self.validate(directory)
            self.assertEqual(validated.returncode, 0, validated.stderr)

        surfaced = self.surface()
        self.assertEqual(surfaced.returncode, 0, surfaced.stderr)
        self.assertIn("::warning::ROUTER_HEALTHY_PROVIDER_BLOCKED:", surfaced.stdout)
        self.assertNotIn("::error::", surfaced.stdout)

        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            recorded = self.run_step(
                "Record provider-control summary",
                {
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "ACTION": "SURVIVAL",
                    "PROVIDER": "deterministic",
                    "REASON": self.route_receipt()["reason"],
                    "PARENT_RUN_ID": "901",
                    "TARGET_INCIDENT": "2034",
                    "ROUTE_OUTCOME": "success",
                    "REMOTE_FENCE_OUTCOME": "success",
                    "SURVIVAL_OUTCOME": "success",
                    "OPENAI_DISPATCH_OUTCOME": "skipped",
                    "ANTHROPIC_DISPATCH_OUTCOME": "skipped",
                    "RECEIPT_VALIDATION_OUTCOME": "success",
                    "RECEIPT_UPLOAD_OUTCOME": "success",
                },
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            text = summary.read_text()
            self.assertIn("ROUTER_HEALTHY_PROVIDER_BLOCKED", text)
            self.assertIn("OPEN / BLOCKED_WITH_EXACT_REASON", text)
            self.assertIn("repair_verified: false", text)
            self.assertIn("provider_generation_allowed: false", text)
            self.assertNotIn("FIXED_AND_VERIFIED", text)
            self.assertNotIn("PR_CREATED", text)

    def test_malformed_receipt_fence_survival_or_artifact_failure_stays_red(self):
        with tempfile.TemporaryDirectory() as directory:
            route = self.route_receipt()
            route["reason"] = "OTHER"
            result = self.validate(directory, route=route)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PROVIDER_ROUTE_RECEIPT_INVALID", result.stdout)

        with tempfile.TemporaryDirectory() as directory:
            result = self.validate(directory, REMOTE_FENCE_OUTCOME="failure")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REMOTE_WRITER_RECONCILIATION_REQUIRED", result.stdout)

        with tempfile.TemporaryDirectory() as directory:
            result = self.validate(directory, SURVIVAL_OUTCOME="failure")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("DETERMINISTIC_SURVIVAL_CHECKS_FAILED", result.stdout)

        for outcomes in (
            {"REMOTE_FENCE_OUTCOME": "failure"},
            {"SURVIVAL_OUTCOME": "failure"},
            {"ACTION": "DISPATCH", "OPENAI_DISPATCH_OUTCOME": "failure"},
            {"RECEIPT_VALIDATION_OUTCOME": "failure"},
            {"RECEIPT_UPLOAD_OUTCOME": "failure"},
        ):
            with self.subTest(outcomes=outcomes):
                result = self.surface(**outcomes)
                self.assertEqual(result.returncode, 1)
                self.assertIn("::error::BLOCKED_WITH_EXACT_REASON:", result.stdout)

    def test_fallback_requires_enabled_eligible_route_and_is_separately_auditable(self):
        outputs = self.text.split("    outputs:\n", 1)[1].split("\n\n    steps:", 1)[0]
        self.assertIn("independent_fallback_allowed == 'true'", outputs)
        self.assertIn("WOW_COPILOT_FALLBACK_ENABLED == 'true'", outputs)
        self.assertIn("steps.remote_fence.outcome == 'success'", outputs)
        self.assertIn("steps.survival.outcome == 'success'", outputs)
        self.assertIn("steps.receipt_validation.outcome == 'success'", outputs)
        self.assertIn("steps.provider_receipt.outcome == 'success'", outputs)
        self.assertNotIn("|| steps.remote_fence.outcome == 'failure'", outputs)
        job = self.text.split("  independent-provider:\n", 1)[1]
        self.assertIn("if: always() && needs.provider-control.outputs.fallback_required == 'true'", job)
        self.assertIn("group: wow-v17-engineering-implementation-single-writer", job)
        self.assertIn("engineering_remote_fallback.py fallback", job)
        self.assertIn("independent-provider-receipt.json", job)
        self.assertIn("if-no-files-found: error", job)
        self.assertIn("engineering_remote_fallback.py guard", self.text)


if __name__ == "__main__":
    unittest.main()
