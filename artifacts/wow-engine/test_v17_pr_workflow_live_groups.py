"""PR verification runs must not share a concurrency group with live runs.

A static group shared by pull_request and schedule/push/dispatch runs either
queues PR checks behind long live maintenance (cancel-in-progress: false) or
lets a PR cancel live maintenance (cancel-in-progress: true). Every workflow
that runs on pull_request must give PR runs their own group, and should cancel
superseded PR runs.
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
        "wow-v17-terminal-closure-controller", "wow-v17-wnba-prop-candidate",
        "wow-v17-first-six-transport-rescue",
    ):
        concurrency = yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text())["concurrency"]
        group = concurrency["group"]
        assert group.startswith(f"{name}-${{{{") and "|| 'live' }}" in group, name
        assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}", name
