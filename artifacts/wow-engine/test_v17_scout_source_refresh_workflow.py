from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-scout-source-refresh.yml"


def test_source_refresh_workflow_always_emits_durable_receipt() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Write blocked source refresh receipt" in text
    assert '"status": "BLOCKED_SOURCE_CONFIGURATION"' in text
    assert '"database_configured":' in text
    assert '"sportsdataio_configured":' in text
    assert '"prediction_authority": False' in text
    assert '"can_execute": False' in text
    assert "Upload source refresh receipt" in text
    assert "if-no-files-found: error" in text

    upload_block = text.split("- name: Upload source refresh receipt", 1)[1]
    assert "if: steps.credentials.outputs.configured == 'true'" not in upload_block


def test_source_refresh_workflow_distinguishes_blocked_from_real_refresh() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "steps.credentials.outputs.configured != 'true'" in text
    assert "steps.credentials.outputs.configured == 'true'" in text
    assert "::warning::Scout structured-source refresh is blocked by missing runtime configuration." in text
    assert "python v17/scout_source_refresh.py" in text


def test_source_refresh_workflow_remains_valid_yaml_and_non_executable() -> None:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert payload["name"] == "wow-v17-scout-source-refresh"
    assert payload["jobs"]["refresh"]["env"]["WOW_CAN_EXECUTE"] == "false"
