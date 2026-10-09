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
    assert "stream_A: canonical acquisition / official event identity (#823 -> #1407)" in text
    assert "stream_B: MLB scorer numeric input integrity (#1496 / #1507)" in text
    assert "stream_C: production memory and interactive latency (#1388 -> #1501 -> #502)" in text
    assert "status,displayTitle" in text
    assert '--open-prs "$RUNNER_TEMP/wow-open-prs.json"' in text
    assert '--active-runs "$RUNNER_TEMP/wow-active-runs.json"' in text
    assert "ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE" in text
    assert "--json number,title,body" in text
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
