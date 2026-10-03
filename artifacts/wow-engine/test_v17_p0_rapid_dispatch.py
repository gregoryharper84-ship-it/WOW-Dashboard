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
