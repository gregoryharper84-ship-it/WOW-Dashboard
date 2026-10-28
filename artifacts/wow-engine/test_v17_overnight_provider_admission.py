"""Exercise the real overnight provider-routing shell with bounded offline fixtures.

No live providers, GitHub writes, deploys, or sporting model calls.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
DISPATCHER = ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
PRIMARY = ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml"


def _route(*, event="schedule", primary="failure", secondary="failure", anthro=True, openai=True):
    if not shutil.which("jq"):
        pytest.skip("Workflow shell fixture requires jq (present on GitHub Actions)")
    text = DISPATCHER.read_text()
    section = text.split("      - name: Resolve provider action", 1)[1]
    script = textwrap.dedent(section.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *wow-v17-chatgpt-engineering-worker.yml*) "
            "printf '[{\"databaseId\":900,\"conclusion\":\"%s\",\"updatedAt\":\"2026-10-23T03:00:00Z\"}]\\n' \"$TEST_PRIMARY\" ;;\n"
            "  *wow-v17-claude-engineering-worker.yml*) "
            "if [ \"$TEST_SECONDARY\" = none ]; then printf '[]\\n'; "
            "else printf '[{\"databaseId\":901,\"conclusion\":\"%s\"}]\\n' \"$TEST_SECONDARY\"; fi ;;\n"
            "  *) printf 'Quota exceeded\\n' ;;\n"
            "esac\n"
        )
        gh.chmod(0o755)
        python = bin_dir / "python"
        python.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = -c ]; then printf '5\\n'; exit 0; fi\n"
            "case \"$*\" in\n"
            "  *' classify '*) printf '{\"code\":\"OPENAI_API_QUOTA_EXCEEDED\",\"failover_eligible\":true}\\n' ;;\n"
            "  *' circuit-breaker '*) printf '{\"active\":true}\\n' ;;\n"
            "  *) exit 30 ;;\n"
            "esac\n"
        )
        python.chmod(0o755)
        output = root / "github-output"
        output.touch()
        env = dict(
            os.environ,
            PATH=str(bin_dir) + ":" + os.environ["PATH"],
            RUNNER_TEMP=str(root),
            GITHUB_OUTPUT=str(output),
            GITHUB_REPOSITORY="test/repo",
            GITHUB_RUN_ID="999",
            EVENT_NAME=event,
            SOURCE_WORKFLOW="wow-v17-nightly-engineering-scan" if event == "workflow_run" else "",
            SOURCE_RUN_ID="777" if event == "workflow_run" else "",
            SOURCE_CONCLUSION="failure" if event == "workflow_run" else "",
            SOURCE_DISPLAY_TITLE="",
            FORCE_PROVIDER="auto",
            MANUAL_REASON="",
            MANUAL_TARGET_INCIDENT="",
            MANUAL_LEASE_GROUP="",
            OPENAI_API_KEY="test-key" if openai else "",
            ANTHROPIC_API_KEY="test-key" if anthro else "",
            CLAUDE_CODE_OAUTH_TOKEN="",
            TEST_PRIMARY=primary,
            TEST_SECONDARY=secondary,
        )
        proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                              text=True, timeout=20)
        assert proc.returncode == 0, proc.stderr
        receipt = json.loads((root / "provider-route.json").read_text())
        assert receipt["can_execute"] is False
        assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
        return receipt


def test_scheduled_quota_without_secondary_uses_credit_free_survival():
    receipt = _route(anthro=False)
    assert (receipt["action"], receipt["provider"]) == ("SURVIVAL", "deterministic")
    assert "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED" in receipt["reason"]


def test_scheduled_quota_does_not_retry_failed_claude():
    receipt = _route(anthro=True, secondary="failure")
    assert (receipt["action"], receipt["provider"]) == ("SURVIVAL", "deterministic")
    assert receipt["reason"].endswith("SECONDARY_UNAVAILABLE_OR_FAILED")


def test_scheduled_quota_uses_successfully_recovered_secondary():
    receipt = _route(anthro=True, secondary="success")
    assert (receipt["action"], receipt["provider"]) == ("DISPATCH", "anthropic")


def test_first_secondary_attempt_allowed_when_no_history():
    receipt = _route(anthro=True, secondary="none")
    assert (receipt["action"], receipt["provider"]) == ("DISPATCH", "anthropic")


def test_successful_primary_run_reopens_normal_dispatch():
    receipt = _route(primary="success", secondary="failure")
    assert (receipt["action"], receipt["provider"]) == ("DISPATCH", "openai")


def test_nightly_scan_routes_as_fresh_intake_not_a_provider_failure():
    receipt = _route(event="workflow_run", anthro=False)
    assert (receipt["action"], receipt["provider"]) == ("SURVIVAL", "deterministic")
    assert "UNKNOWN_ENGINEERING_WORKFLOW_FAILURE" not in receipt["reason"]


def test_only_dispatcher_owns_automatic_primary_entrypoint():
    worker = PRIMARY.read_text()
    dispatch = DISPATCHER.read_text()
    worker_triggers = worker.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    dispatch_triggers = dispatch.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in worker_triggers
    assert "  schedule:" not in worker_triggers
    assert "  workflow_run:" not in worker_triggers
    assert 'cron: "17 * * * *"' in dispatch_triggers
    assert "wow-v17-nightly-engineering-scan" in dispatch_triggers
    assert "wow-v17-chatgpt-engineering-worker" in dispatch_triggers
    assert "wow-v17-claude-engineering-worker" in dispatch_triggers
    assert "wow-v17-gemini-engineering-worker" in dispatch_triggers
