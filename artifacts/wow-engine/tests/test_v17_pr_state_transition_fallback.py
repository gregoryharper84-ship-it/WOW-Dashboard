import base64
import json
from pathlib import Path
import shutil
import subprocess

import yaml

ROOT = Path(__file__).parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "workflow-pr-state-transition-fallback.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _script() -> str:
    data = _workflow()
    return data["jobs"]["ready-for-review"]["steps"][0]["with"]["script"]


def _run_case(*, label="FORCE_READY_FOR_REVIEW", draft=True, head_repo="o/r", permission="write"):
    node = shutil.which("node")
    assert node, "node is required to execute the actual github-script body"
    payload = {
        "script_b64": base64.b64encode(_script().encode()).decode(),
        "label": label,
        "draft": draft,
        "head_repo": head_repo,
        "permission": permission,
    }
    harness = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const script = Buffer.from(input.script_b64, 'base64').toString('utf8');
const calls = {failed: null, graphql: false, comment: null, removed: false, permissionLookups: 0};
const context = {
  payload: i{
    pull_request: {draft: input.draft, node_id: 'PR_NODE', number: 7, head: {repo: {full_name: input.head_repo}}},
    label: {name: input.label},
  },
  repo: {owner: 'o', repo: 'r'},
  actor: 'authorized-actor',
  runId: 12345,
};
const core = {setFailed: (message) => { calls.failed = message; }};
const github = {
  rest: {
    repos: {
      getCollaboratorPermissionLevel: async () => {
        calls.permissionLookups += 1;
        return {data: {permission: input.permission, role_name: input.permission}};
      },
    },
    issues: {
      createComment: async (args) => { calls.comment = args.body; },
      removeLabel: async () => { calls.removed = true; },
    },
  },
  graphql: async () => {
    calls.graphql = true;
    return {markPullRequestReadyForReview: {pullRequest: {number: 7, isDraft: false}}};
  },
};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
(async () => {
  await new AsyncFunction('context', 'github', 'core', script)(context, github, core);
  process.stdout.write(JSON.stringify(calls));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    completed = subprocess.run(
        [node, "-e", harness],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_ready_fallback_is_label_only_and_least_privilege():
    text = WORKFLOW.read_text(encoding="utf-8")
    data = _workflow()
    trigger = data.get("on") or data.get(True) or {}

    assert trigger["pull_request_target"]["types"] == ["labeled"]
    assert data["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "FORCE_READY_FOR_REVIEW" in text
    assert "github.event.pull_request.draft == true" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    assert "getCollaboratorPermissionLevel" in text
    assert "markPullRequestReadyForReview" in text
    assert "actions/checkout" not in text
    forbidden = (
        "mergePullRequest", "pulls.merge", "createRef", "updateRef", "createCommit",
        "createBlob", "contents: write", "actions: write", "issues: write",
    )
    assert all(token not in text for token in forbidden)


def test_ready_fallback_positive_path_executes_transition_and_readable_receipt():
    result = _run_case()
    assert result["failed"] is None
    assert result["permissionLookups"] == 1
    assert result["graphql"] is True
    assert result["removed"] is True
    assert "\n- Trigger:" in result["comment"]
    assert "\\n- Trigger:" not in result["comment"]
    assert "can_execute: `false`" in result["comment"]


def test_ready_fallback_wrong_label_fails_closed():
    result = _run_case(label="other")
    assert result["failed"] == "READY_FALLBACK_LABEL_MISMATCH"
    assert result["graphql"] is False


def test_ready_fallback_non_draft_fails_closed():
    result = _run_case(draft=False)
    assert result["failed"] == "READY_FALLBACK_PR_NOT_DRAFT"
    assert result["graphql"] is False


def test_ready_fallback_cross_repo_fails_closed():
    result = _run_case(head_repo="fork/repo")
    assert result["failed"] == "READY_FALLBACK_CROSS_REPOSITORY_HEAD_REJECTED"
    assert result["graphql"] is False


def test_ready_fallback_unauthorized_actor_fails_closed():
    result = _run_case(permission="read")
    assert result["failed"] == "READY_FALLBACK_ACTOR_NOT_AUTHORIZED"
    assert result["permissionLookups"] == 1
    assert result["graphql"] is False
