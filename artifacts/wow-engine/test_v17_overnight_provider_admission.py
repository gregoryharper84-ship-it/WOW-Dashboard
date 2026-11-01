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


def _route(*, event="schedule", primary="failure", secondary="failure",
           anthro=True, openai=True, target="", claude_failure="delivery",
           last_incident="1150", receipt_fetch="ok", receipt_invalid="",
           artifact="", run_identity="", run_id=""):
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
        gh.write_text("#!/usr/bin/env python3\nimport json\nimport os\nimport sys\nfrom pathlib import Path\n\nargs = sys.argv[1:]\nenv = os.environ\nrepo = \"test/repo\"\nrun_id = 901\nsha = \"a\" * 40\nname = \"wow-v17-dual-stream-receipt-901-1\"\ndef emit(value):\n    print(json.dumps(value))\n\nif args[:2] == [\"run\", \"list\"]:\n    workflow = args[args.index(\"--workflow\") + 1]\n    if \"chatgpt\" in workflow:\n        emit([{\"databaseId\": 900, \"conclusion\": env[\"TEST_PRIMARY\"], \"updatedAt\": \"2026-10-23T03:00:00Z\"}])\n    elif \"claude\" in workflow:\n        if \"--branch\" not in args or args[args.index(\"--branch\") + 1] != \"main\":\n            sys.exit(2)\n        if env[\"TEST_SECONDARY\"] == \"none\":\n            emit([])\n        else:\n            rid = \"../escape\" if env.get(\"TEST_RUN_ID\") == \"malicious\" else 901.5 if env.get(\"TEST_RUN_ID\") == \"float\" else run_id\n            emit([{\"databaseId\": rid, \"conclusion\": env[\"TEST_SECONDARY\"]}])\n    else:\n        sys.exit(3)\nelif args[:1] == [\"api\"]:\n    path = args[1]\n    if path.endswith(\"/actions/runs/901/artifacts?per_page=100\"):\n        if env.get(\"TEST_RECEIPT_FETCH\") == \"api-fail\":\n            sys.exit(4)\n        artifact = {\n            \"name\": name, \"size_in_bytes\": 962, \"expired\": False,\n            \"workflow_run\": {\"id\": run_id, \"head_branch\": \"main\", \"head_sha\": sha}\n        }\n        items = [] if env.get(\"TEST_ARTIFACT\") == \"missing\" else [artifact, artifact] if env.get(\"TEST_ARTIFACT\") == \"duplicate\" else [artifact]\n        emit({\"total_count\": len(items), \"artifacts\": items})\n    elif path.endswith(\"/actions/runs/901\"):\n        emit({\n            \"id\": run_id, \"repository\": {\"full_name\": repo},\n            \"path\": \".github/workflows/other.yml\" if env.get(\"TEST_RUN_IDENTITY\") == \"bad\" else \".github/workflows/wow-v17-claude-engineering-worker.yml\",\n            \"head_branch\": \"main\", \"event\": \"workflow_dispatch\",\n            \"status\": \"completed\", \"conclusion\": \"failure\",\n            \"head_sha\": sha, \"run_attempt\": 1\n        })\n    else:\n        sys.exit(5)\nelif args[:2] == [\"run\", \"download\"]:\n    if env.get(\"TEST_RECEIPT_FETCH\") == \"download-fail\":\n        sys.exit(6)\n    if args[2] != \"901\" or args[args.index(\"--name\") + 1] != name:\n        sys.exit(7)\n    directory = Path(args[args.index(\"--dir\") + 1])\n    directory.mkdir(parents=True, exist_ok=True)\n    receipt = {\n        \"workflow_run_id\": \"901\", \"provider\": \"anthropic\",\n        \"worker_id\": \"github-actions:901:1:anthropic\",\n        \"current_restoration_target\": env[\"TEST_LAST_INCIDENT\"],\n        \"restoration_action\": \"REPAIR\",\n        \"restoration_delivery\": \"FAILED\",\n        \"restoration_delivery_code\": \"ACTIONABLE_REPAIR_TRIAGE_NOT_REPAIRABLE\" if env[\"TEST_CLAUDE_FAILURE\"] == \"delivery\" else \"ANTHROPIC_API_RATE_LIMITED\",\n        \"restoration_disposition\": \"UNRESOLVED_TYPED_FAILURE\",\n        \"job_status\": \"failure\", \"worker_mode\": \"SAFE_HOLD\",\n        \"lease_state\": \"RELEASED_ON_FAILURE\",\n        \"can_execute\": False, \"terminal_authority\": \"V17_TERMINAL_REDUCER\"\n    }\n    if env.get(\"TEST_RECEIPT_INVALID\") == \"worker\":\n        receipt[\"worker_id\"] = \"github-actions:other:1:anthropic\"\n    elif env.get(\"TEST_RECEIPT_INVALID\") == \"lease\":\n        receipt[\"lease_state\"] = \"HELD\"\n    elif env.get(\"TEST_RECEIPT_INVALID\") == \"incident\":\n        receipt[\"current_restoration_target\"] = \"invalid\"\n    (directory / \"wow-dual-stream-receipt.json\").write_text(json.dumps(receipt))\nelse:\n    sys.exit(8)\n")
        gh.chmod(0o755)
        python = bin_dir / "python"
        python.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = -c ]; then printf '5\\n'; exit 0; fi\n"
            "case \"$*\" in\n"
            "  *' classify '*'--provider anthropic'*)\n"
            "    if [ \"$TEST_CLAUDE_FAILURE\" = delivery ]; then\n"
            "      printf '{\"provider\":\"anthropic\",\"code\":\"ACTIONABLE_REPAIR_TRIAGE_NOT_REPAIRABLE\",\"provider_signal\":null,\"disposition\":\"UNRESOLVED_TYPED_FAILURE\",\"failover_eligible\":false}\\n'\n"
            "    else\n"
            "      printf '{\"provider\":\"anthropic\",\"code\":\"ACTIONABLE_REPAIR_TRIAGE_NOT_REPAIRABLE\",\"provider_signal\":\"ANTHROPIC_API_RATE_LIMITED\",\"disposition\":\"UNRESOLVED_TYPED_FAILURE\",\"failover_eligible\":true}\\n'\n"
            "    fi ;;\n"
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
            MANUAL_TARGET_INCIDENT=target,
            MANUAL_LEASE_GROUP="",
            OPENAI_API_KEY="test-key" if openai else "",
            ANTHROPIC_API_KEY="test-key" if anthro else "",
            CLAUDE_CODE_OAUTH_TOKEN="",
            TEST_PRIMARY=primary,
            TEST_SECONDARY=secondary,
            TEST_CLAUDE_FAILURE=claude_failure,
            TEST_LAST_INCIDENT=last_incident,
            TEST_RECEIPT_FETCH=receipt_fetch,
            TEST_RECEIPT_INVALID=receipt_invalid,
            TEST_ARTIFACT=artifact,
            TEST_RUN_IDENTITY=run_identity,
            TEST_RUN_ID=run_id,
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
    secondary = dispatch.split("secondary_usable()", 1)[1].split("gemini_child_held()", 1)[0]
    assert "--branch main --status completed" in secondary


def test_delivery_only_failed_claude_allows_a_different_exact_target():
    receipt = _route(secondary="failure", target="1021")
    assert (receipt["action"], receipt["provider"]) == ("DISPATCH", "anthropic")


def test_delivery_failure_never_retries_same_failed_target():
    receipt = _route(secondary="failure", target="1150")
    assert (receipt["action"], receipt["provider"]) == ("SURVIVAL", "deterministic")


def test_provider_failure_still_blocks_new_target():
    receipt = _route(secondary="failure", target="1021", claude_failure="provider")
    assert (receipt["action"], receipt["provider"]) == ("SURVIVAL", "deterministic")


def test_unverifiable_receipt_still_blocks_new_target():
    receipt = _route(secondary="failure", target="1021", receipt_fetch="api-fail")
    assert (receipt["action"], receipt["provider"]) == ("SURVIVAL", "deterministic")


def test_cancelled_claude_is_never_treated_as_recovered():
    receipt = _route(secondary="cancelled", target="1021")
    assert (receipt["action"], receipt["provider"]) == ("SURVIVAL", "deterministic")

def test_receipt_download_failure_blocks():
    assert _route(secondary="failure", target="1021", receipt_fetch="download-fail")["provider"] == "deterministic"


def test_missing_or_duplicate_run_artifact_blocks():
    for artifact in ("missing", "duplicate"):
        assert _route(secondary="failure", target="1021", artifact=artifact)["provider"] == "deterministic"


def test_spoofed_receipt_worker_or_held_lease_blocks():
    for invalid in ("worker", "lease", "incident"):
        assert _route(secondary="failure", target="1021", receipt_invalid=invalid)["provider"] == "deterministic"


def test_wrong_workflow_run_identity_blocks():
    assert _route(secondary="failure", target="1021", run_identity="bad")["provider"] == "deterministic"


def test_run_ids_must_be_positive_integers():
    for invalid in ("malicious", "float"):
        assert _route(secondary="failure", target="1021", run_id=invalid)["provider"] == "deterministic"


def test_missing_explicit_target_remains_held_after_task_only_failure():
    assert _route(secondary="failure", target="")["provider"] == "deterministic"
