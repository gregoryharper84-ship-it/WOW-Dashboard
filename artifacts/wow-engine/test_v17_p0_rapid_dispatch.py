from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-p0-rapid-dispatch.yml"


def test_p0_parallel_rapid_dispatch_contract() -> None:
    text = WORKFLOW.read_text()
    assert 'cron: "*/15 * * * *"' in text
    assert "p0_parallel_dispatch.py" in text
    assert "max_parallel_writers: 3" in text
    assert "implementation_lease_scope: domain-scoped" in text
    assert "for status in queued in_progress waiting requested pending" in text
    assert '--status "$status"' in text
    assert "--limit 1000" in text
    assert "ACTIVE_WRITER_INVENTORY_TRUNCATED" in text
    assert '--active-runs "$RUNNER_TEMP/wow-active-writer-runs.json"' in text
    assert '--run-number "$GITHUB_RUN_NUMBER"' in text
    assert "stream_A: Scout/Data Plane (#1237 -> #1250)" in text
    assert "stream_B: State/Scoring (#1189 -> #960)" in text
    assert "stream_C: Runtime/Ingest (#502 -> #1127)" in text
    assert "status,displayTitle" in text
    assert 'contains(\\\"lease=${lease_group}\\\")' in text
    assert "wow-v17-engineering-provider-dispatcher.yml" in text
    assert "-f force_provider=auto" in text
    assert '-f target_incident="$incident"' in text
    assert '-f lease_group="$lease_group"' in text
    assert 'reason="P0_PARALLEL_RAPID:${stream}:${incident}"' in text
    assert "can_execute: false" in text
    assert "terminal_authority: V17_TERMINAL_REDUCER" in text


def test_p0_parallel_rapid_dispatch_yaml_parses() -> None:
    data = yaml.safe_load(WORKFLOW.read_text())
    assert data["name"] == "wow-v17-p0-rapid-dispatch"
    assert data["permissions"]["actions"] == "write"
    assert data["permissions"]["contents"] == "read"
    assert data["permissions"]["issues"] == "read"


def test_p0_rapid_provider_preflight_is_wired_before_dispatch() -> None:
    text = WORKFLOW.read_text()
    assert text.index("Preflight P0 engineering provider availability") < text.index(
        "Dispatch idle domain-scoped writers"
    )
    assert "if: steps.provider_state.outputs.admit == 'true'" in text
    assert "PRIMARY_ACCOUNT_BLOCKED_SECONDARY_DELIVERY_FAILED" in text
    assert "PRIMARY_WORKER_INVENTORY_UNVERIFIED" in text
    assert "provider_admission:" in text


import os
import subprocess
import tempfile
import textwrap

import pytest


@pytest.mark.parametrize(
    "primary,secondary,code,fail_inventory,expected_admit,expected_reason,independent",
    [
        ("failure", "failure", "OPENAI_API_QUOTA_EXCEEDED", False, "false", "PRIMARY_ACCOUNT_BLOCKED_SECONDARY_DELIVERY_FAILED", False),
        ("failure", "cancelled", "OPENAI_API_AUTH_FAILED", False, "false", "PRIMARY_ACCOUNT_BLOCKED_SECONDARY_DELIVERY_FAILED", False),
        ("failure", "success", "OPENAI_API_QUOTA_EXCEEDED", False, "true", "PROVIDER_ADMISSION_CLEAR", False),
        ("failure", "failure", "OPENAI_API_RATE_LIMITED", False, "true", "PROVIDER_ADMISSION_CLEAR", False),
        ("success", "failure", "OPENAI_API_QUOTA_EXCEEDED", False, "true", "PROVIDER_ADMISSION_CLEAR", False),
        ("failure", "failure", "OPENAI_API_QUOTA_EXCEEDED", True, "false", "PRIMARY_WORKER_INVENTORY_UNVERIFIED", False),
        ("failure", "failure", "OPENAI_API_QUOTA_EXCEEDED", False, "true", "PRIMARY_ACCOUNT_BLOCKED_INDEPENDENT_ADMISSION_ONLY", True),
        ("failure", "failure", "OPENAI_API_AUTH_FAILED", False, "true", "PRIMARY_ACCOUNT_BLOCKED_INDEPENDENT_ADMISSION_ONLY", True),
    ],
)
def test_real_p0_provider_preflight_with_bounded_cli_fixtures(
    primary, secondary, code, fail_inventory, expected_admit, expected_reason, independent,
) -> None:
    text = WORKFLOW.read_text()
    part = text.split("      - name: Preflight P0 engineering provider availability", 1)[1]
    script = textwrap.dedent(part.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path as _Path
        root = _Path(tmpdir)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/bin/bash\n"
            "if [ \"$1\" = run ] && [ \"$2\" = list ]; then\n"
            "  if [[ \"$*\" == *wow-v17-chatgpt-engineering-worker.yml* ]]; then\n"
            "    if [ \"$FAKE_INVENTORY_FAILURE\" = true ]; then exit 1; fi\n"
            "    printf '[{\"databaseId\":101,\"conclusion\":\"%s\"}]\\n' \"$FAKE_PRIMARY\"\n"
            "  else\n"
            "    printf '[{\"databaseId\":102,\"conclusion\":\"%s\"}]\\n' \"$FAKE_SECONDARY\"\n"
            "  fi\n"
            "elif [ \"$1\" = run ] && [ \"$2\" = view ]; then\n"
            "  echo 'ERROR: Quota exceeded. Check your plan and billing details.'\n"
            "else exit 70; fi\n"
        )
        gh.chmod(0o755)
        python = bin_dir / "python"
        python.write_text(
            "#!/bin/bash\n"
            "printf '{\"code\":\"%s\"}\\n' \"$FAKE_CODE\"\n"
        )
        python.chmod(0o755)
        output = root / "github-output"
        summary = root / "summary"
        output.touch()
        summary.touch()
        env = dict(
            os.environ,
            PATH=str(bin_dir) + ":" + os.environ["PATH"],
            GITHUB_OUTPUT=str(output),
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_REPOSITORY="fixture/repo",
            RUNNER_TEMP=str(root),
            FAKE_PRIMARY=primary,
            FAKE_SECONDARY=secondary,
            FAKE_CODE=code,
            FAKE_INVENTORY_FAILURE="true" if fail_inventory else "false",
            INDEPENDENT_ENABLED="true" if independent else "",
            INDEPENDENT_BUDGET_APPROVED="true" if independent else "",
            INDEPENDENT_REPOSITORY="fixture/repo",
            INDEPENDENT_MAX_ASSIGNMENTS="1",
        )
        result = subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        values = dict(
            line.split("=", 1) for line in output.read_text().splitlines()
        )
        assert values["admit"] == expected_admit
        assert values["reason"] == expected_reason
        assert expected_reason in summary.read_text()
