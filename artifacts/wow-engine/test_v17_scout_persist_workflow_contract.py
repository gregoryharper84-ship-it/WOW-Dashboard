from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-scout-brain-persist.yml"


def test_scout_persistence_workflow_run_is_main_only() -> None:
    data = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    trigger = data["on"]["workflow_run"]

    assert trigger["workflows"] == ["wow-v17-nightly-multiscout"]
    assert trigger["types"] == ["completed"]
    assert trigger["branches"] == ["main"]
    assert "workflow_dispatch" in data["on"]


def test_scout_persistence_remains_nonexecuting() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "WOW_CAN_EXECUTE" not in text or 'WOW_CAN_EXECUTE: "true"' not in text
    assert "wow-v17-scout-brain-persist" in text
