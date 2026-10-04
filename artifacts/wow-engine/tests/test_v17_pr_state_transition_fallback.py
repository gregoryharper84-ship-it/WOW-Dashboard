from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-pr-state-transition-fallback.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_ready_fallback_is_label_only_and_least_privilege():
    text = WORKFLOW.read_text(encoding="utf-8")
    data = _workflow()
    trigger = data.get("on") or data.get(True) or {}

    assert trigger["pull_request_target"]["types"] == ["labeled"]
    assert data["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    assert "FORCE_READY_FOR_REVIEW" in text
    assert "github.event.pull_request.draft == true" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    assert "markPullRequestReadyForReview" in text
    assert "actions/checkout" not in text


def test_ready_fallback_cannot_merge_or_write_branch_content():
    text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = (
        "mergePullRequest",
        "pulls.merge",
        "createRef",
        "updateRef",
        "createCommit",
        "createBlob",
        "contents: write",
        "actions: write",
    )
    assert all(token not in text for token in forbidden)
    assert "Merge performed: `false`" in text
    assert "Branch protection modified: `false`" in text
    assert "can_execute: `false`" in text
