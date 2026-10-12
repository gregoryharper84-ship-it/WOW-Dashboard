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
              eligible=True, conclusion="failure", title="lease=GLOBAL incident=AUTO"):
        text = WORKFLOW.read_text()
        section = text.split("      - name: Resolve provider action", 1)[1]
        script = section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        script = textwrap.dedent(script)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text("#!/bin/sh\nprintf 'fixture failed worker log\\n'\n")
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
                       CLAUDE_CODE_OAUTH_TOKEN="", OPENAI_API_KEY="")
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
