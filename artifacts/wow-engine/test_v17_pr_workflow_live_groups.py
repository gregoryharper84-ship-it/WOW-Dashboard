"""PR verification runs must not share a concurrency group with live runs.

Read-only PR verification should not queue behind long live maintenance.
Only verification PR runs may be cancelled when superseded. Write-capable
merged-PR terminal closure must retain one static non-cancelling group across
all triggers, including pull_request:closed, to prevent duplicate writes.
"""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"



def test_live_maintenance_groups_stay_single_flight_and_uncancelled():
    for name in (
        "wow-v17-basketball-model-maintenance", "wow-v17-ncaaf-model-maintenance",
        "wow-v17-nhl-model-maintenance", "wow-v17-nfl-forward-shadow",
        "wow-v17-llp-shadow-observer", "wow-v17-nightly-multiscout",
        "wow-v17-wnba-prop-candidate",
        "wow-v17-first-six-transport-rescue",
    ):
        concurrency = yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text())["concurrency"]
        group = concurrency["group"]
        assert group.startswith(f"{name}-${{{{") and "|| 'live' }}" in group, name
        assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}", name


def test_write_capable_terminal_closure_remains_single_writer():
    name = "wow-v17-terminal-closure-controller"
    source = (WORKFLOWS / f"{name}.yml").read_text()
    concurrency = yaml.safe_load(source)["concurrency"]
    assert concurrency == {"group": name, "cancel-in-progress": False}
    assert "types: [closed]" in source  # merged PRs are live write-capable runs
    assert "issues: write" in source and "pull-requests: write" in source
