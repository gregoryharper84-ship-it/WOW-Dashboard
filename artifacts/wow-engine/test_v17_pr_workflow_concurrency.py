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

# Contract-pinned exceptions; each is tracked by its own test/PR.
KNOWN_STATIC_GROUPS = {
    "wow-v17-team-event-recoverable-refresh.yml",  # pinned by test_v17_team_event_recoverable_refresh
    "wow-v17-first-six-model-maintenance.yml",  # split into PR/live groups by PR #1590
}


def _pr_workflows():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        on = data.get(True, data.get("on")) or {}
        if isinstance(on, list):
            on = {name: None for name in on}
        if isinstance(on, dict) and "pull_request" in on:
            yield path, data


def test_pr_workflows_never_share_a_static_concurrency_group_with_live_runs():
    offenders = []
    for path, data in _pr_workflows():
        if path.name in KNOWN_STATIC_GROUPS:
            continue
        concurrency = data.get("concurrency")
        if not isinstance(concurrency, dict):
            offenders.append(f"{path.name}: no top-level concurrency")
            continue
        if "${{" not in str(concurrency.get("group", "")):
            offenders.append(f"{path.name}: static group {concurrency.get('group')!r}")
    assert not offenders, offenders


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
