"""Every workflow that runs on pull_request must declare top-level concurrency.

Without it, superseded PR runs keep consuming shared runners and delay every
other PR's checks. Non-PR runs of workflows that previously had no
concurrency keep a unique run_id group, so their behavior is unchanged.
"""
from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
CANCEL_SUPERSEDED_PR = "${{ github.event_name == 'pull_request' }}"


def _pr_workflows():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        on = data.get(True, data.get("on")) or {}
        if isinstance(on, list):
            on = {name: None for name in on}
        if isinstance(on, dict) and "pull_request" in on:
            yield path, data


def test_every_pr_workflow_declares_top_level_concurrency():
    missing = [path.name for path, data in _pr_workflows() if not isinstance(data.get("concurrency"), dict)]
    assert not missing, missing


def test_previously_unscoped_workflows_keep_non_pr_runs_unconstrained():
    for name in (
        "v17-1ip-bf-shadow-research", "v17-1ip-pr-verify", "v17-game-winner-shadow-evaluation",
        "v17-spread-margin-challenger", "wnba-prop-offline-fit", "wow-engineering-control-plane",
        "wow-odds-proxy-verify", "wow-odds-router-verify", "wow-v17-gpt-editor-sync-packet",
        "wow-v17-ncaaf-prop-challenger", "wow-v17-scout-research-sources-verify",
        "wow-v17-spread-forward-shadow", "wow-v17-team-state-transport-isolation",
    ):
        concurrency = yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text())["concurrency"]
        assert "github.run_id" in concurrency["group"], name
        assert "pr-{0}" in concurrency["group"], name
        assert concurrency["cancel-in-progress"] == CANCEL_SUPERSEDED_PR, name
