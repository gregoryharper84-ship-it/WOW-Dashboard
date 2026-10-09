"""Contract for the status-only ANTHROPIC_API_KEY diagnostic workflow (#1021)."""
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-anthropic-key-diagnostic.yml"


def test_manual_only_and_no_token_permissions():
    doc = yaml.safe_load(WF.read_text())
    triggers = doc.get(True, doc.get("on"))
    assert set(triggers) == {"workflow_dispatch"}
    assert doc["permissions"] == {}


def test_never_prints_key_or_response_body():
    run = WF.read_text()
    assert 'echo "$K"' not in run and "echo $K" not in run
    assert "cat " not in run
    assert 'rm -f "$resp"' in run
    assert "max_tokens\\\":1" in run
