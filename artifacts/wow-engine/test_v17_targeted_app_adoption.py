"""P0 #2136: exact-target bootstrap must reuse existing App writer and guards."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from v17 import engineering_authorized_adoption as adoption
from v17 import engineering_targeted_adoption_guard as target


ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows/wow-v17-engineering-auto-repair.yml"
REPO = "gregoryharper84-ship-it/WOW-Dashboard"
SHA = "a" * 40


def owner_source(n: int) -> dict:
    return {"number": n, "user": {"login": "gregoryharper84-ship-it"},
            "head": {"ref": f"owner/{n}"}}


def app_source(n: int) -> dict:
    return {"number": n, "user": {"login": adoption.APP_AUTHOR},
            "head": {"ref": f"wow-engineering/exact-replay-{n}-{SHA[:12]}"}}


def mocked_inventory(monkeypatch, *, open_pulls=None, existing=None):
    calls = []
    rows = [] if open_pulls is None else open_pulls
    old = [] if existing is None else existing

    def api(endpoint: str):
        calls.append(endpoint)
        if endpoint.startswith("pulls?state=open&base=main&per_page=100&page="):
            return rows
        if endpoint.startswith("pulls?state=all&head="):
            return old
        raise AssertionError(f"unexpected mutation or inventory route: {endpoint}")

    monkeypatch.setattr(adoption, "api", api)
    return calls


def test_exact_target_admission_is_readonly_and_bounded(monkeypatch):
    calls = mocked_inventory(monkeypatch, open_pulls=[app_source(1), owner_source(2158)])
    result = target.guard(2158, SHA, REPO)
    assert result == {
        "status": "TARGET_ADMISSION_PASS", "pr_number": 2158,
        "source_sha": SHA, "can_execute": False, "review_wip": 1,
    }
    assert len(calls) == 2
    assert all(c.startswith("pulls?") for c in calls)


@pytest.mark.parametrize("number,sha,repo", [
    (0, SHA, REPO), (-1, SHA, REPO), (True, SHA, REPO),
    (2158, "a" * 39, REPO), (2158, "A" * 40, REPO),
    (2158, SHA, "attacker/fork"),
])
def test_invalid_target_is_rejected_before_any_github_access(monkeypatch, number, sha, repo):
    monkeypatch.setattr(adoption, "api", lambda x: pytest.fail(f"unsafe github read: {x}"))
    with pytest.raises(adoption.AdoptionHold, match="TARGET_ADOPTION_REQUEST_INVALID_HOLD"):
        target.guard(number, sha, repo)


def test_at_capacity_fails_before_pr_replay_or_writer(monkeypatch):
    calls = mocked_inventory(monkeypatch, open_pulls=[
        app_source(1), app_source(2), app_source(3)
    ])
    with pytest.raises(adoption.AdoptionHold, match="TARGET_APP_WIP_CAP_HOLD"):
        target.guard(2158, SHA, REPO)
    assert len(calls) == 1


def test_existing_exact_replay_cannot_dispatch_second_writer(monkeypatch):
    calls = mocked_inventory(monkeypatch, existing=[{"number": 2222}])
    with pytest.raises(adoption.AdoptionHold, match="TARGET_REPLAY_ALREADY_EXISTS_HOLD"):
        target.guard(2158, SHA, REPO)
    assert len(calls) == 2


def test_malformed_or_truncated_inventory_fails_closed(monkeypatch):
    monkeypatch.setattr(adoption, "api", lambda _: {"bad": "inventory"})
    with pytest.raises(adoption.AdoptionHold, match="TARGET_APP_WIP_INVENTORY_INVALID_HOLD"):
        target.guard(2158, SHA, REPO)

    def capped(_):
        return [owner_source(n) for n in range(100)]
    monkeypatch.setattr(adoption, "api", capped)
    with pytest.raises(adoption.AdoptionHold, match="TARGET_APP_WIP_INVENTORY_PAGE_CAP_HOLD"):
        target.guard(2158, SHA, REPO)


def test_replay_inventory_unknown_is_never_assumed_empty(monkeypatch):
    def api(path):
        if path.startswith("pulls?state=open&base=main"):
            return []
        return {"unexpected": True}
    monkeypatch.setattr(adoption, "api", api)
    with pytest.raises(adoption.AdoptionHold, match="TARGET_REPLAY_INVENTORY_INVALID_HOLD"):
        target.guard(2158, SHA, REPO)


def test_targeted_dispatch_reuses_existing_authenticated_worker_and_lock():
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    triggers = wf.get("on", wf.get(True))
    dispatch_inputs = triggers["workflow_dispatch"]["inputs"]
    assert set(dispatch_inputs) == {"source_pr", "source_sha"}
    assert all(not spec["required"] for spec in dispatch_inputs.values())
    assert wf["on"] if "on" in wf else wf[True]
    jobs = wf["jobs"]
    observer = jobs["adoption-readonly-preflight"]
    writer = jobs["adopt-reviewed-owner-source"]
    assert observer["steps"][0]["with"]["ref"] == "main"
    assert observer["steps"][0]["with"]["fetch-depth"] == 0
    assert "concurrency" not in observer
    assert writer["needs"] == "adoption-readonly-preflight"
    assert "wow-v17-engineering-implementation-single-writer" in str(writer["concurrency"])
    assert writer["environment"] == "wow-engineering"
    source = next(s for s in observer["steps"] if s.get("id") == "source")
    script = source["run"]
    assert 'if [ -n "$TARGET_SOURCE_PR" ] || [ -n "$TARGET_SOURCE_SHA" ]; then' in script
    assert '"$GITHUB_ACTOR" = "gregoryharper84-ship-it"' in script
    assert '"$GITHUB_REF" = "refs/heads/main"' in script
    assert "TARGET_ADOPTION_UNAUTHORIZED_DISPATCH" in script
    assert "TARGET_ADOPTION_REQUEST_INVALID_HOLD" in script
    assert "engineering_authorized_adoption.py preflight" in script
    assert "engineering_authorized_adoption.py discover" in script
    assert "--source-pr" in script and "--source-sha" in script
    assert "TARGET_ADMISSION_PASS" in script
    assert "can_execute == false" in script
    assert subprocess.run(["bash", "-n"], input=script, text=True,
                          capture_output=True).returncode == 0
    write = next(s for s in writer["steps"]
                 if s.get("name") == "Re-propose immutable source through authenticated Engineering App")
    assert "engineering_targeted_adoption_guard.py" in write["run"]
    assert write["run"].index("engineering_targeted_adoption_guard.py") < write["run"].index(
        "engineering_authorized_adoption.py integrate")
    assert write["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert write["env"]["APP_TOKEN"] == "${{ steps.engineering_app.outputs.token }}"
    assert write["env"]["WOW_CAN_EXECUTE"] == "false"
    assert write["env"]["WOW_DRY_RUN_ONLY"] == "true"
    assert subprocess.run(["bash", "-n"], input=write["run"], text=True,
                          capture_output=True).returncode == 0


def test_manual_recovery_does_not_create_other_controller_or_approval():
    workflow = WF.read_text(encoding="utf-8")
    assert workflow.count("  workflow_dispatch:") == 1
    assert workflow.count("  adopt-reviewed-owner-source:") == 1
    assert workflow.count("  adoption-readonly-preflight:") == 1
    # Review capacity is a backpressure boundary, not a permanently stuck queue.
    # A genuine App replay PR closing wakes the already-existing protected-main
    # observer, and the 3-WIP cap is still checked again inside the writer.
    assert "pull_request_target:\n    types: [closed]" in workflow
    assert "github.event_name != 'pull_request_target'" in workflow
    assert "github.event.pull_request.user.login == 'wow-engineering[bot]'" in workflow
    assert "github.event.pull_request.base.ref == 'main'" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "startsWith(github.event.pull_request.head.ref, 'wow-engineering/exact-replay-')" in workflow
    assert "queue: max" in workflow
    assert "gh pr merge" not in workflow
    assert "gh pr review" not in workflow
    # The worker only proposes an App PR. Existing authentic checks still own merge.
    assert 'independent_source_review_complete == false' in workflow
    assert "REQUIRED_PENDING" in workflow
