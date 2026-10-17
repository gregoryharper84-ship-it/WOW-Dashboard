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
              source_run_id="123"):
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
                "  name=\"${SOURCE_WORKFLOW%% *}\"\n"
                "  case \"$*\" in\n"
                "    *\"${name}.yml\"*) printf '[{\"databaseId\":123,\"conclusion\":\"failure\"}]\\n';;\n"
                "    *) printf '[]\\n';;\n"
                "  esac\n"
                "else\n"
                "  printf 'fixture failed worker log\\n'\n"
                "fi\n"
            )
            gh.chmod(0o755)
            python = bin_dir / "python"
            classification = json.dumps({
                "code": code, "failover_eligible": eligible,
                "manual_recovery_required": code in {
                    "OPENAI_API_QUOTA_EXCEEDED", "OPENAI_API_AUTH_FAILED",
                    "OPENAI_API_KEY_MISSING",
                },
            })
            python.write_text("#!/bin/sh\nprintf '%s\\n' '" + classification + "'\n")
            python.chmod(0o755)
            output = root / "outputs"
            output.touch()
            env = dict(os.environ, PATH=str(bin_dir)+":"+os.environ["PATH"],
                       RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(output),
                       GITHUB_RUN_ID="900", GITHUB_REPOSITORY="fixture/repo",
                       EVENT_NAME="workflow_run", SOURCE_WORKFLOW=source,
                       SOURCE_RUN_ID=source_run_id, SOURCE_CONCLUSION=conclusion,
                       SOURCE_DISPLAY_TITLE=title, MANUAL_TARGET_INCIDENT="",
                       MANUAL_LEASE_GROUP="", ANTHROPIC_API_KEY="fixture" if auth else "",
                       CLAUDE_CODE_OAUTH_TOKEN="", OPENAI_API_KEY="")
            result = subprocess.run(["bash","-c",script], env=env,
                                    capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            receipt = json.loads((root/"provider-route.json").read_text())
            self.assertFalse(receipt["can_execute"])
            self.assertEqual(receipt["terminal_authority"],"V17_TERMINAL_REDUCER")
            self.assertEqual(receipt["parent_run_id"],source_run_id)
            return receipt

    def auto_route(self, *, latest_conclusion="failure",
                   breaker=True, code="OPENAI_API_QUOTA_EXCEEDED",
                   openai=True, anthropic=False,
                   claude_latest_conclusion=None, force_provider="auto"):
        """Execute the actual workflow's automatic dispatch path.

        Stub only provider/GitHub observations. The real route shell, jq, and
        all GitHub output serialization/branching remain under test.
        """
        text = WORKFLOW.read_text(encoding="utf-8")
        section = text.split("      - name: Resolve provider action", 1)[1]
        script = textwrap.dedent(
            section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = run ] && [ "$2" = list ]; then\n'
                '  case "$*" in\n'
                '    *wow-v17-claude-engineering-worker.yml*) printf \'%s\\n\' "$STUB_CLAUDE_RUN";;\n'
                '    *) printf \'%s\\n\' "$STUB_LAST_RUN";;\n'
                '  esac\n'
                'elif [ "$1" = run ] && [ "$2" = view ]; then\n'
                '  printf \'ERROR: quota exceeded\\n\'\n'
                'else\n'
                '  exit 12\n'
                'fi\n'
            )
            gh.chmod(0o755)
            py = bin_dir / "python"
            py.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = -c ]; then\n'
                '  echo 20\n'
                'elif [ "$2" = classify ]; then\n'
                '  printf \'%s\\n\' "$STUB_CLASSIFICATION"\n'
                'elif [ "$2" = circuit-breaker ]; then\n'
                '  printf \'%s\\n\' "$STUB_BREAKER"\n'
                'else\n'
                '  exit 15\n'
                'fi\n'
            )
            py.chmod(0o755)
            output = root / "outputs"
            output.touch()
            fixture = [{
                "databaseId": 321,
                "conclusion": latest_conclusion,
                "updatedAt": "2026-10-14T23:00:00Z",
            }]
            env = dict(
                os.environ,
                PATH=str(bin_dir)+":"+os.environ["PATH"],
                RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(output),
                GITHUB_RUN_ID="901", GITHUB_REPOSITORY="fixture/repo",
                EVENT_NAME="workflow_dispatch",
                FORCE_PROVIDER=force_provider, MANUAL_REASON="CI_PROBE",
                MANUAL_LEASE_GROUP="GLOBAL", MANUAL_TARGET_INCIDENT="",
                SOURCE_WORKFLOW="", SOURCE_RUN_ID="",
                SOURCE_CONCLUSION="", SOURCE_DISPLAY_TITLE="",
                OPENAI_API_KEY="configured" if openai else "",
                ANTHROPIC_API_KEY="configured" if anthropic else "",
                CLAUDE_CODE_OAUTH_TOKEN="",
                STUB_LAST_RUN=json.dumps(fixture),
                STUB_CLAUDE_RUN=json.dumps(
                    [] if claude_latest_conclusion is None else [{
                        "databaseId": 444,
                        "conclusion": claude_latest_conclusion,
                        "updatedAt": "2026-10-14T23:00:00Z",
                    }]
                ),
                STUB_CLASSIFICATION=json.dumps({
                    "code": code, "failover_eligible": True,
                    "manual_recovery_required": code in {
                        "OPENAI_API_QUOTA_EXCEEDED", "OPENAI_API_AUTH_FAILED",
                        "OPENAI_API_KEY_MISSING",
                    },
                }),
                STUB_BREAKER=json.dumps({"active":breaker}),
            )
            result = subprocess.run(
                ["bash", "-c", script], env=env, capture_output=True, text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads((root/"provider-route.json").read_text())
            self.assertFalse(receipt["can_execute"])
            self.assertEqual(receipt["terminal_authority"], "V17_TERMINAL_REDUCER")
            return receipt

    def test_forced_openai_dispatch_cannot_bypass_hard_quota_hold(self):
        route = self.auto_route(
            breaker=True, force_provider="openai", openai=True,
        )
        self.assertEqual(route["action"], "SURVIVAL")
        self.assertEqual(route["provider"], "deterministic")
        self.assertEqual(
            route["reason"],
            "FORCED_OPENAI_RECOVERY_NOT_VERIFIED:OPENAI_API_QUOTA_EXCEEDED",
        )

    def test_forced_openai_after_successful_provider_probe_is_allowed(self):
        route = self.auto_route(
            latest_conclusion="success", breaker=True, force_provider="openai",
        )
        self.assertEqual(route["action"], "DISPATCH")
        self.assertEqual(route["provider"], "openai")

    def test_forced_claude_requires_usable_secondary_provider(self):
        blocked = self.auto_route(
            force_provider="anthropic", anthropic=True,
            claude_latest_conclusion="failure",
        )
        self.assertEqual(blocked["action"], "SURVIVAL")
        self.assertEqual(
            blocked["reason"], "ANTHROPIC_AUTH_OR_RECOVERY_EVIDENCE_MISSING"
        )
        recovered = self.auto_route(
            force_provider="anthropic", anthropic=True,
            claude_latest_conclusion="success",
        )
        self.assertEqual(recovered["action"], "DISPATCH")
        self.assertEqual(recovered["provider"], "anthropic")

    def test_configured_but_quota_exhausted_openai_key_is_not_retried(self):
        route = self.auto_route(breaker=True, openai=True, anthropic=False)
        self.assertEqual(route["action"], "SURVIVAL")
        self.assertEqual(route["provider"], "deterministic")
        self.assertEqual(
            route["reason"],
            "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED:NO_APPROVED_ALTERNATE",
        )

    def test_quota_breaker_uses_only_authorized_secondary_provider(self):
        route = self.auto_route(breaker=True, openai=True, anthropic=True)
        self.assertEqual(route["action"], "DISPATCH")
        self.assertEqual(route["provider"], "anthropic")
        self.assertEqual(route["reason"], "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED")

    def test_exhausted_secondary_credits_do_not_spawn_repeat_claude_runs(self):
        route = self.auto_route(
            breaker=True, openai=True, anthropic=True,
            claude_latest_conclusion="failure",
        )
        self.assertEqual(route["action"], "SURVIVAL")
        self.assertEqual(route["provider"], "deterministic")
        self.assertIn("NO_APPROVED_ALTERNATE", route["reason"])

    def test_expired_transient_rate_limiter_allows_openai(self):
        route = self.auto_route(breaker=False, code="OPENAI_API_RATE_LIMITED")
        self.assertEqual(route["action"], "DISPATCH")
        self.assertEqual(route["provider"], "openai")

    def test_non_provider_failure_does_not_repeat_a_broken_agent(self):
        route = self.auto_route(
            breaker=False, code="NON_PROVIDER_FAILURE", openai=True
        )
        self.assertEqual(route["action"], "SURVIVAL")
        self.assertEqual(
            route["reason"],
            "WORKER_FAILURE_REQUIRES_REPAIR:NON_PROVIDER_FAILURE",
        )

    def test_no_recent_failed_openai_run_can_use_openai(self):
        route = self.auto_route(latest_conclusion="success", breaker=True)
        self.assertEqual(route["action"], "DISPATCH")
        self.assertEqual(route["provider"], "openai")

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
        self.assertEqual(receipt["reason"],"OPENAI_API_QUOTA_EXCEEDED:ANTHROPIC_AUTH_MISSING")

    def test_non_provider_failure_never_dispatches_fallback(self):
        receipt=self.route("wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
                           code="NON_PROVIDER_FAILURE",eligible=False)
        self.assertEqual(receipt["action"],"SURVIVAL")

    def test_claude_failure_does_not_loop_back_to_openai(self):
        receipt=self.route("wow-v17-claude-engineering-worker lease=GLOBAL incident=AUTO",
                           code="ANTHROPIC_API_AUTH_FAILED",eligible=False)
        self.assertEqual(receipt["action"],"SURVIVAL")
        self.assertEqual(receipt["reason"],"ANTHROPIC_API_AUTH_FAILED")

    def test_stale_failed_source_event_cannot_override_newer_result(self):
        receipt = self.route(
            "wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
            source_run_id="111",
        )
        self.assertEqual(receipt["action"], "NO_ACTION")
        self.assertEqual(receipt["reason"], "STALE_WORKER_FAILURE_SUPERSEDED")
        self.assertEqual(receipt["provider"], "none")

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


if __name__ == "__main__":
    unittest.main()
