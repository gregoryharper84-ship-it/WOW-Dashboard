from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "verify_existing_pr_governance_receipt.py"
SOURCE_REVIEW_SCRIPT = Path(__file__).resolve().parents[1] / "independent_source_review_receipt.py"
WORKFLOW = Path(__file__).resolve().parents[2] / "workflows" / "wow-v17-existing-pr-governance-review.yml"
REPO_ROOT = Path(__file__).resolve().parents[3]
TRUSTED_GATE = REPO_ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"
MORNING_GREEN_WORKFLOW = REPO_ROOT / ".github/workflows/wow-v17-morning-green-continuation.yml"
RELEASE_CHECKS_SCRIPT = REPO_ROOT / "artifacts/wow-engine/v17/independent_release_checks.py"
spec = importlib.util.spec_from_file_location("verify_existing_pr_governance_receipt", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = verifier
spec.loader.exec_module(verifier)
source_review_spec = importlib.util.spec_from_file_location(
    "independent_source_review_receipt_for_governance", SOURCE_REVIEW_SCRIPT
)
source_review = importlib.util.module_from_spec(source_review_spec)
assert source_review_spec and source_review_spec.loader
sys.modules[source_review_spec.name] = source_review
source_review_spec.loader.exec_module(source_review)
release_spec = importlib.util.spec_from_file_location("independent_release_checks_for_existing_pr", RELEASE_CHECKS_SCRIPT)
release_checks = importlib.util.module_from_spec(release_spec)
assert release_spec and release_spec.loader
sys.modules[release_spec.name] = release_checks
release_spec.loader.exec_module(release_checks)

NOW = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
REPOSITORY = verifier.CANONICAL_REPOSITORY
REVIEWER = {"id": 9001, "login": "independent-reviewer"}
ALLOWLIST = [REVIEWER]


def test_certifier_workflow_consumes_the_protected_qa_review_without_write_permissions():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "uses: ./.github/actions/wow-claude-agent" not in workflow
    assert "ANTHROPIC_API_KEY" not in workflow
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in workflow
    assert "TRUSTED_REVIEWERS_JSON: ${{ vars.WOW_V17_INDEPENDENT_REVIEWERS }}" in workflow
    assert "workflows: [wow-v17-independent-qa-check]" in workflow
    assert "WOW_QA_INDEPENDENT_REVIEW_ACTIVE" in workflow
    assert "wow-v17-independent-source-review" in workflow
    assert "WOW_QA_REVIEW_OPENAI_API_KEY" not in workflow
    assert "SOURCE_REVIEW_ARTIFACT_MISSING_OR_DUPLICATE" in workflow
    assert "--source-review-json" in workflow
    assert "TRUST_ROOT_BOOTSTRAP" in workflow
    assert "checks: read" in workflow
    assert "actions: write" not in workflow
    assert "contents: write" not in workflow
    assert "pull-requests: write" not in workflow


def test_certifier_consumes_only_authenticated_qa_review_run_artifacts():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "workflows: [wow-v17-independent-qa-check]" in workflow
    assert "SOURCE_REVIEW_WORKFLOW_RUN_UNTRUSTED_OR_NOT_SUCCESSFUL" in workflow
    assert "SOURCE_REVIEW_ARTIFACT_MISSING_OR_DUPLICATE" in workflow
    assert "SOURCE_REVIEW_PR_HEAD_MOVED_OR_REF_MISMATCH" in workflow
    assert "refs?|related to" in workflow
    assert "--source-review-artifact-id" in workflow
    assert "INDEPENDENT_REVIEW_EVIDENCE_MISSING" in workflow
    assert ".event == \"workflow_dispatch\" or .event == \"workflow_run\"" in workflow
    assert "ref: main" in workflow


def test_certifier_serializes_exact_heads_without_cancelling_existing_runs():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    concurrency = workflow.split("concurrency:\n", 1)[1].split("\njobs:\n", 1)[0]

    assert "github.event.check_run.head_sha" in concurrency
    assert "github.event.pull_request.head.sha" in concurrency
    assert "github.event.workflow_run.pull_requests[0].head.sha" in concurrency
    assert "cancel-in-progress: false" in concurrency


def test_source_review_artifact_name_regex_captures_pr_and_head_under_bash_nounset():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    regex_line = next(
        line.strip()
        for line in workflow.splitlines()
        if line.strip().startswith("[[ \"$artifact_name\" =~ ")
    ).removesuffix(" || {")
    regex_line += " || exit 1"
    valid_name = f"wow-v17-independent-source-review-1822-{'a' * 40}-54321-1"
    accepted = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            "\n".join(
                (
                    f'artifact_name="{valid_name}"',
                    'SOURCE_RUN_ID=54321',
                    'SOURCE_ATTEMPT=1',
                    regex_line,
                    'source_pr="${BASH_REMATCH[1]}"',
                    'source_sha="${BASH_REMATCH[2]}"',
                    'printf "%s %s\\n" "$source_pr" "$source_sha"',
                )
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == f"1822 {'a' * 40}"

    rejected = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            "\n".join(
                (
                    'artifact_name="wow-v17-independent-source-review-1822-not-a-sha-54321-1"',
                    'SOURCE_RUN_ID=54321',
                    'SOURCE_ATTEMPT=1',
                    regex_line,
                    'source_pr="${BASH_REMATCH[1]}"',
                )
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0


@pytest.fixture
def run_identity_step(tmp_path):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Resolve exact PR identity and incident\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    stub = """
gh() {
  if [ "$*" = "api /repos/${GITHUB_REPOSITORY}/pulls/1822" ]; then
    printf '%s\\n' "$TEST_PR_JSON"
  elif [ "$*" = "issue view 1806 --repo ${GITHUB_REPOSITORY} --json number,title,state,url" ]; then
    printf '%s\\n' '{"number":1806}'
  else
    echo "UNEXPECTED_GH_INVOCATION:$*" >&2
    return 1
  fi
}
"""

    def run(pr, **overrides):
        env = os.environ | {
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_OUTPUT": str(tmp_path / "outputs"),
            "EVENT_PR": "1822",
            "INPUT_PR": "",
            "INPUT_INCIDENT": "",
            "TEST_PR_JSON": json.dumps(pr),
        } | overrides
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", stub + script],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    return run


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"draft": False}, "false"),
        ({"draft": True}, "true"),
        ({}, "unknown"),
        ({"draft": None}, "unknown"),
        ({"draft": "false"}, "unknown"),
        ({"draft": "true"}, "unknown"),
        ({"draft": 0}, "unknown"),
        ({"draft": []}, "unknown"),
        ({"draft": {}}, "unknown"),
    ],
)
def test_workflow_draft_parsing_is_boolean_only(fields, expected, run_identity_step):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assignment = next(line.strip() for line in workflow.splitlines() if line.strip().startswith("draft=$("))
    parsed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", assignment + '\nprintf "%s" "$draft"'],
        env=os.environ | {"pr_json": json.dumps(fields)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    assert parsed.stdout == expected

    pr = evidence()["pr"]
    pr.pop("draft")
    pr.update(fields, body="Closes #1806")
    result = run_identity_step(pr)
    if expected == "false":
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        assert "EXISTING_PR_NOT_READY_FOR_REVIEW" in result.stderr


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("state",), "closed", "EXISTING_PR_NOT_OPEN"),
        (("base", "ref"), "other", "EXISTING_PR_BASE_NOT_CANONICAL_MAIN"),
        (("base", "repo", "full_name"), "fork/WOW-Dashboard", "EXISTING_PR_BASE_NOT_CANONICAL_MAIN"),
        (("head", "repo", "full_name"), "fork/WOW-Dashboard", "EXISTING_PR_HEAD_REPOSITORY_MISMATCH"),
        (("head", "sha"), "not-a-sha", "EXISTING_PR_IDENTITY_INCOMPLETE"),
        (("head", "ref"), "", "EXISTING_PR_IDENTITY_INCOMPLETE"),
        (("body",), "", "EXISTING_PR_INCIDENT_ID_MISSING"),
    ],
)
def test_non_draft_workflow_preserves_identity_guards(path, value, reason, run_identity_step):
    pr = evidence()["pr"]
    pr["body"] = "Closes #1806"
    target = pr
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    result = run_identity_step(pr)

    assert result.returncode == 1
    assert reason in result.stderr


def test_workflow_rejects_invalid_pr_number(run_identity_step):
    result = run_identity_step(evidence()["pr"], EVENT_PR="invalid")

    assert result.returncode == 1
    assert "EXISTING_PR_NUMBER_INVALID" in result.stderr


def test_applicable_workflow_run_without_source_identity_still_fails_closed(run_identity_step):
    result = run_identity_step(evidence()["pr"], EVENT_PR="", SOURCE_PR="")

    assert result.returncode == 1
    assert "EXISTING_PR_NUMBER_INVALID" in result.stderr


def test_source_review_stale_head_sha_fails_closed(run_identity_step):
    pr = evidence()["pr"]
    pr["body"] = "Closes #1806"
    result = run_identity_step(
        pr, EVENT_PR="", SOURCE_PR="1822", SOURCE_HEAD_SHA="b" * 40, SOURCE_HEAD_REF="fix/issue-1806"
    )

    assert result.returncode == 1
    assert "SOURCE_REVIEW_PR_HEAD_MOVED_OR_REF_MISMATCH" in result.stderr


@pytest.fixture
def run_route_step(tmp_path):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Route event applicability\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    stub = """
gh() {
  if [ "$*" = "api /repos/${GITHUB_REPOSITORY}/actions/runs/${SOURCE_RUN_ID}/artifacts?per_page=100" ]; then
    printf '%s\\n' "$TEST_ARTIFACTS_JSON"
  elif [ "$*" = "api /repos/${GITHUB_REPOSITORY}/check-runs/${CHECK_RUN_ID}" ]; then
    printf '%s\\n' "$TEST_RELEASE_CHECK_RUN_JSON"
  elif [ "$*" = "api /repos/${GITHUB_REPOSITORY}/pulls/${RELEASE_PR_NUMBER}" ]; then
    printf '%s\\n' "$TEST_RELEASE_PR_JSON"
  elif [[ "$*" == "api /repos/${GITHUB_REPOSITORY}/pulls?state=open&base=main&per_page=100&page="* ]]; then
    printf '%s\\n' "$TEST_RELEASE_PRS_JSON"
  else
    echo "UNEXPECTED_GH_INVOCATION:$*" >&2
    return 1
  fi
}
"""

    def run(**overrides):
        outputs = tmp_path / "outputs"
        outputs.write_text("", encoding="utf-8")
        env = os.environ | {
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_OUTPUT": str(outputs),
            "EVENT_NAME": "workflow_run",
            "REVIEW_ACTIVE": "true",
            "SOURCE_RUN_ID": "40062800000",
            "SOURCE_RUN_ATTEMPT": "1",
            "SOURCE_CONCLUSION": "success",
            "TEST_ARTIFACTS_JSON": json.dumps({"artifacts": []}),
            "CHECK_RUN_ID": "601",
            "CHECK_RUN_NAME": "WOW Release Authority exact-head",
            "CHECK_RUN_APP_ID": "5245853",
            "CHECK_RUN_STATUS": "completed",
            "CHECK_RUN_CONCLUSION": "success",
            "CHECK_RUN_HEAD_SHA": HEAD,
            "CHECK_RUN_PRS": "[]",
            "RELEASE_PR_NUMBER": "1822",
            "TEST_RELEASE_CHECK_RUN_JSON": "{}",
            "TEST_RELEASE_PR_JSON": "{}",
            "TEST_RELEASE_PRS_JSON": "[]",
        } | overrides
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", stub + script],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
        )
        parsed = dict(
            line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines() if "=" in line
        )
        return result, parsed

    return run


POLICY_ARTIFACT = {"artifacts": [{"name": "wow-v17-qa-policy-40062800000-1"}]}


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        # Observed in run 40062825503: QA completion while source review is inactive.
        ({"REVIEW_ACTIVE": "", "TEST_ARTIFACTS_JSON": json.dumps(POLICY_ARTIFACT)}, "INDEPENDENT_SOURCE_REVIEW_INACTIVE_NO_EXACT_CANARY"),
        ({"REVIEW_ACTIVE": "false", "TEST_ARTIFACTS_JSON": json.dumps(POLICY_ARTIFACT)}, "INDEPENDENT_SOURCE_REVIEW_INACTIVE_NO_EXACT_CANARY"),
        ({"SOURCE_CONCLUSION": "failure"}, "SOURCE_QA_RUN_NOT_SUCCESSFUL:failure"),
        ({"SOURCE_CONCLUSION": "skipped"}, "SOURCE_QA_RUN_NOT_SUCCESSFUL:skipped"),
        ({"SOURCE_CONCLUSION": ""}, "SOURCE_QA_RUN_NOT_SUCCESSFUL:none"),
        ({}, "SOURCE_QA_RUN_HAD_NO_EXACT_HEAD_TARGET"),
    ],
)
def test_inapplicable_workflow_run_exits_neutrally_without_receipt(overrides, reason, run_route_step):
    result, outputs = run_route_step(**overrides)

    assert result.returncode == 0, result.stderr
    assert outputs == {"applicable": "false", "reason": reason}
    assert "EXISTING_PR_NUMBER_INVALID" not in result.stderr


@pytest.mark.parametrize("event", ["pull_request_target", "workflow_dispatch"])
def test_direct_pr_events_are_always_applicable(event, run_route_step):
    result, outputs = run_route_step(EVENT_NAME=event, REVIEW_ACTIVE="", SOURCE_RUN_ID="", SOURCE_CONCLUSION="")

    assert result.returncode == 0, result.stderr
    assert outputs == {"applicable": "true"}
    assert "UNEXPECTED_GH_INVOCATION" not in result.stderr



def test_authenticated_exact_attempt_canary_wakes_certifier_global_off(run_route_step):
    exact = {
        "name": "wow-v17-independent-source-review-2247-" + "a" * 40 + "-40062800000-1",
        "expired": False,
    }
    expected = {"artifacts": POLICY_ARTIFACT["artifacts"] + [exact]}
    result, outputs = run_route_step(
        REVIEW_ACTIVE="false", TEST_ARTIFACTS_JSON=json.dumps(expected),
    )
    assert result.returncode == 0, result.stderr
    assert outputs == {"applicable": "true"}


@pytest.mark.parametrize("change", ["expired", "older_attempt", "duplicate"])
def test_unbound_or_duplicate_canary_remains_neutral(change, run_route_step):
    exact = {
        "name": "wow-v17-independent-source-review-2247-" + "a" * 40 + "-40062800000-1",
        "expired": False,
    }
    if change == "expired":
        exact["expired"] = True
    elif change == "older_attempt":
        exact["name"] = exact["name"][:-1] + "0"
    payload = POLICY_ARTIFACT["artifacts"] + [exact]
    if change == "duplicate":
        payload.append(dict(exact))
    result, outputs = run_route_step(
        REVIEW_ACTIVE="false", TEST_ARTIFACTS_JSON=json.dumps({"artifacts": payload}),
    )
    assert result.returncode == 0, result.stderr
    assert outputs == {
        "applicable": "false",
        "reason": "INDEPENDENT_SOURCE_REVIEW_INACTIVE_NO_EXACT_CANARY",
    }


def test_targeted_successful_qa_run_is_applicable(run_route_step):
    result, outputs = run_route_step(TEST_ARTIFACTS_JSON=json.dumps(POLICY_ARTIFACT))

    assert result.returncode == 0, result.stderr
    assert outputs == {"applicable": "true"}


def release_check_run(prs=None, *, app_id=5245853, head_sha=HEAD, status="completed", conclusion="success"):
    return {
        "id": 601,
        "name": "WOW Release Authority exact-head",
        "app": {"id": app_id},
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "pull_requests": prs if prs is not None else [{"number": 1822, "head": {"sha": HEAD}}],
    }


def release_pr(*, number=1822, sha=HEAD, state="open", draft=False, head_repo=REPOSITORY,
               base_ref="main", base_repo=REPOSITORY):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "head": {
            "sha": sha,
            "ref": "fix/issue-1806",
            "repo": {"full_name": head_repo},
        },
        "base": {
            "ref": base_ref,
            "repo": {"full_name": base_repo},
        },
    }


def run_release_check(run_route_step, *, event_prs=None, api_prs=None, api_check=None, pr=None, inventory=None,
                      **overrides):
    event_prs = event_prs if event_prs is not None else [{"number": 1822, "head": {"sha": HEAD}}]
    api_prs = api_prs if api_prs is not None else event_prs
    api_check = api_check if api_check is not None else release_check_run(api_prs)
    pr = pr if pr is not None else release_pr()
    inventory = inventory if inventory is not None else [pr]
    return run_route_step(
        EVENT_NAME="check_run",
        REVIEW_ACTIVE="",
        CHECK_RUN_PRS=json.dumps(event_prs),
        TEST_RELEASE_CHECK_RUN_JSON=json.dumps(api_check),
        TEST_RELEASE_PR_JSON=json.dumps(pr),
        TEST_RELEASE_PRS_JSON=json.dumps(inventory),
        **overrides,
    )


def test_genuine_late_release_success_wakes_certifier_for_unique_current_pr(run_route_step):
    result, outputs = run_release_check(run_route_step)

    assert result.returncode == 0, result.stderr
    assert outputs == {
        "pr_number": "1822",
        "head_sha": HEAD,
        "head_ref": "fix/issue-1806",
        "applicable": "true",
    }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"CHECK_RUN_APP_ID": "1"}, "RELEASE_CHECK_EVENT_NOT_AUTHENTIC_SUCCESS"),
        ({"CHECK_RUN_NAME": "spoofed"}, "RELEASE_CHECK_EVENT_NOT_AUTHENTIC_SUCCESS"),
        ({"CHECK_RUN_STATUS": "in_progress"}, "RELEASE_CHECK_EVENT_NOT_AUTHENTIC_SUCCESS"),
        ({"CHECK_RUN_CONCLUSION": "failure"}, "RELEASE_CHECK_EVENT_NOT_AUTHENTIC_SUCCESS"),
    ],
)
def test_release_wakeup_rejects_spoofed_or_non_success_event(overrides, reason, run_route_step):
    result, outputs = run_release_check(run_route_step, **overrides)

    assert result.returncode == 0, result.stderr
    assert outputs == {"applicable": "false", "reason": reason}


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"api_check": release_check_run(app_id=1)}, "RELEASE_CHECK_API_IDENTITY_OR_PR_ASSOCIATION_INVALID"),
        ({"api_check": {**release_check_run(), "id": 602}},
         "RELEASE_CHECK_API_IDENTITY_OR_PR_ASSOCIATION_INVALID"),
        ({"api_check": release_check_run(head_sha="b" * 40)},
         "RELEASE_CHECK_API_IDENTITY_OR_PR_ASSOCIATION_INVALID"),
        ({"event_prs": []}, "RELEASE_CHECK_PR_ASSOCIATION_MISSING_OR_AMBIGUOUS"),
        (
            {"event_prs": [{"number": 1822, "head": {"sha": HEAD}},
                           {"number": 1823, "head": {"sha": HEAD}}]},
            "RELEASE_CHECK_PR_ASSOCIATION_MISSING_OR_AMBIGUOUS",
        ),
        ({"api_prs": []}, "RELEASE_CHECK_PR_ASSOCIATION_MISSING_OR_AMBIGUOUS"),
        ({"pr": release_pr(sha="b" * 40)}, "RELEASE_CHECK_PR_MOVED_OR_NOT_CANONICAL"),
        ({"pr": release_pr(head_repo="fork/WOW-Dashboard")}, "RELEASE_CHECK_PR_MOVED_OR_NOT_CANONICAL"),
        ({"pr": release_pr(base_ref="other")}, "RELEASE_CHECK_PR_MOVED_OR_NOT_CANONICAL"),
        ({"inventory": [release_pr(), release_pr(number=1823)]}, "RELEASE_CHECK_PR_TARGET_NOT_UNIQUE"),
    ],
)
def test_release_wakeup_rejects_unbound_or_moving_pr(kwargs, reason, run_route_step):
    result, outputs = run_release_check(run_route_step, **kwargs)

    assert result.returncode == 0, result.stderr
    assert outputs == {"applicable": "false", "reason": reason}


def test_release_wakeup_fails_closed_when_current_pr_inventory_is_truncated(run_route_step):
    inventory = [release_pr()] + [
        release_pr(number=2000 + index, sha="b" * 40) for index in range(99)
    ]

    result, outputs = run_release_check(run_route_step, inventory=inventory)

    assert result.returncode == 1
    assert "RELEASE_CHECK_PR_INVENTORY_LIMIT_EXCEEDED" in result.stderr
    assert "applicable" not in outputs


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"SOURCE_RUN_ID": ""}, "SOURCE_REVIEW_RUN_ID_OR_ATTEMPT_INVALID"),
        ({"SOURCE_RUN_ID": "0"}, "SOURCE_REVIEW_RUN_ID_OR_ATTEMPT_INVALID"),
        ({"TEST_ARTIFACTS_JSON": "{}"}, "SOURCE_REVIEW_ARTIFACT_INVENTORY_INVALID"),
        ({"TEST_ARTIFACTS_JSON": "not-json"}, "SOURCE_REVIEW_ARTIFACT_INVENTORY_INVALID"),
    ],
)
def test_route_fails_closed_on_untrusted_source_identity(overrides, reason, run_route_step):
    result, outputs = run_route_step(**overrides)

    assert result.returncode == 1
    assert reason in result.stderr
    assert "applicable" not in outputs


def test_every_certification_step_is_gated_on_the_route():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    steps = workflow.split("      - name: ")[2:]

    assert workflow.split("      - name: ")[1].startswith("Route event applicability\n")
    assert steps
    for step in steps:
        assert "        if: steps.route.outputs.applicable == 'true'" in step, step.splitlines()[0]


def evidence(*, author=None, review_state="APPROVED", review_sha=HEAD):
    author = author or {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN, "type": "User"}
    return {
        "complete_inventory": True,
        "pr": {
            "number": 1822,
            "state": "open",
            "draft": False,
            "merged_at": None,
            "changed_files": 1,
            "user": author,
            "head": {
                "sha": HEAD,
                "ref": "fix/issue-1806",
                "repo": {"full_name": REPOSITORY},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": REPOSITORY},
            },
        },
        "changed_files": [{"filename": "src/fix.py", "previous_filename": None, "status": "modified"}],
        "reviews": [
            {
                "id": 12,
                "state": review_state,
                "commit_id": review_sha,
                "submitted_at": "2026-10-17T23:00:00Z",
                "user": {**REVIEWER, "type": "User"},
            }
        ],
        "workflow_runs": [
            {
                "id": index + 100,
                "name": name,
                "path": path,
                "head_sha": HEAD,
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-17T23:00:00Z",
            }
            for index, (name, path) in enumerate(verifier.REQUIRED_WORKFLOWS.items())
        ],
        "check_runs": [
            {
                "id": index + 200,
                "name": name,
                "head_sha": HEAD,
                "app": {"id": app_id},
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-10-17T23:00:00Z",
            }
            for index, (name, app_id) in enumerate(verifier.REQUIRED_CHECKS.items())
        ],
    }


def build(data=None, *, trusted_reviewers=ALLOWLIST, now=NOW):
    return verifier.build_receipt(
        data or evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        trusted_reviewers=trusted_reviewers,
        now=now,
    )


BOT_AUTHOR = {"id": 4242, "login": "copilot-swe-agent", "type": "Bot"}


def bootstrap_evidence(
    *,
    author=None,
    owner_state="APPROVED",
    owner_sha=HEAD,
    qa_conclusion="success",
    qa_app=5245832,
    check_sha=HEAD,
):
    data = evidence(author=author or BOT_AUTHOR)
    data["changed_files"] = [{
        "filename": ".github/workflows/wow-v17-existing-pr-governance-review.yml",
        "previous_filename": None,
        "status": "modified",
    }]
    data["reviews"] = [
        {
            "id": 12,
            "state": "APPROVED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T22:00:00Z",
            "user": {**REVIEWER, "type": "User"},
        },
        {
            "id": 77,
            "state": owner_state,
            "commit_id": owner_sha,
            "submitted_at": "2026-10-17T23:10:00Z",
            "user": {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN, "type": "User"},
        },
    ]
    data["check_runs"] = []
    for index, (name, app_id) in enumerate(verifier.BOOTSTRAP_CHECKS.items()):
        conclusion = qa_conclusion if name == "WOW Independent QA exact-head" else "success"
        resolved_app = qa_app if name == "WOW Independent QA exact-head" else app_id
        data["check_runs"].append({
            "id": index + 300,
            "name": name,
            "head_sha": check_sha,
            "app": {"id": resolved_app},
            "status": "completed",
            "conclusion": conclusion,
            "started_at": f"2026-10-17T23:{index:02d}:00Z",
        })
    return data


def build_bootstrap(data=None, *, now=NOW):
    return verifier.build_trust_root_bootstrap(
        data or bootstrap_evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        repo_root=REPO_ROOT,
        now=now,
    )


def verify_bootstrap(receipt, *, now=NOW, live_reviews=None, live_checks=None, data=None):
    source = data or bootstrap_evidence()
    return verifier.verify_trust_root_bootstrap(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        live_reviews=source["reviews"] if live_reviews is None else live_reviews,
        live_checks=source["check_runs"] if live_checks is None else live_checks,
        repo_root=REPO_ROOT,
        now=now,
    )


def verify(receipt, *, head=HEAD, now=NOW, live_reviews=None):
    return verifier.verify_receipt(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=head,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        live_reviews=live_reviews if live_reviews is not None else evidence()["reviews"],
        now=now,
    )


def automated_review_data(data=None, *, now=NOW):
    source = data or evidence()
    author = source["pr"]["user"]
    files = [{
        "filename": "src/fix.py",
        "previous_filename": None,
        "status": "modified",
        "patch": "@@ -1 +1 @@\n-old\n+new",
        "additions": 1,
        "deletions": 1,
    }]
    _, digest = source_review.canonical_diff(files, expected_count=1)
    artifact = source_review.build_artifact(
        repository=REPOSITORY,
        pr={
            "number": 1822,
            "state": "open",
            "draft": False,
            "merged_at": None,
            "changed_files": 1,
            "user": {"id": author["id"], "login": author["login"]},
            "head": {
                "sha": HEAD,
                "ref": "fix/issue-1806",
                "repo": {"full_name": REPOSITORY},
            },
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        },
        files=files,
        commit={
            "sha": HEAD,
            "commit": {"tree": {"sha": "d" * 40}, "message": "Fix the source issue"},
            "parents": [{"sha": "e" * 40}],
            "author": {"login": "copilot-swe-agent"},
            "committer": {"login": "copilot-swe-agent"},
        },
        review_result={
            "decision": "PASS",
            "summary": "Reviewed all changed source files.",
            "reviewed_head_sha": HEAD,
            "reviewed_diff_sha256": digest,
            "reviewed_files": ["src/fix.py"],
            "assessment": {
                "source_review": "PASS",
                "security_review": "PASS",
                "probability_integrity_review": "PASS",
            },
            "findings": [],
        },
        run={
            "id": 54321,
            "run_attempt": 1,
            "name": source_review.WORKFLOW_NAME,
            "workflow_id": source_review.WORKFLOW_ID,
            "path": source_review.WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "repository": REPOSITORY,
            "head_sha": "b" * 40,
        },
        now=now,
    )
    return artifact


SOURCE_REVIEW_RUN = {
    "id": 54321,
    "run_attempt": 1,
    "name": f"{source_review.WORKFLOW_NAME} event=workflow_dispatch pr=1822",
    "workflow_id": source_review.WORKFLOW_ID,
    "path": source_review.WORKFLOW_PATH,
    "event": "workflow_dispatch",
    "head_branch": "main",
    "head_sha": "b" * 40,
    "repository": {"full_name": REPOSITORY},
    "head_repository": {"full_name": REPOSITORY},
    "status": "completed",
    "conclusion": "success",
}


def test_builds_versioned_exact_head_review_evidence_without_legacy_pass_fields():
    receipt = build()

    assert receipt["receipt_schema_version"] == 2
    assert receipt["evidence_state"] == "REVIEW_EVIDENCE_VERIFIED"
    assert "handoff" not in receipt
    assert "governance_complete" not in receipt
    assert receipt["can_execute"] is False
    verify(receipt)


def test_standalone_automated_review_is_a_separate_versioned_exact_head_receipt():
    receipt = verifier.build_receipt(
        evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        automated_review=automated_review_data(),
        source_review_run=SOURCE_REVIEW_RUN,
        source_review_artifact_id=555,
        now=NOW,
    )

    verifier.verify_receipt(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        live_reviews=[],
        source_review_run=SOURCE_REVIEW_RUN,
        now=NOW,
    )

    assert receipt["receipt_schema_version"] == 3
    assert receipt["trusted_reviewers"] == []
    assert receipt["independent_review"]["type"] == "AUTOMATED_SOURCE_REVIEW"
    assert receipt["independent_review"]["artifact_id"] == 555
    assert receipt["independent_review"]["artifact"]["source"]["run_id"] == "54321"
    assert receipt["evidence_state"] == "REVIEW_EVIDENCE_VERIFIED"
    assert receipt["can_execute"] is False


@pytest.mark.parametrize(
    ("source_run", "reason"),
    [
        ({**SOURCE_REVIEW_RUN, "event": "pull_request_target"}, "WORKFLOW_RUN_UNTRUSTED"),
        ({**SOURCE_REVIEW_RUN, "path": ".github/workflows/fork.yml"}, "WORKFLOW_RUN_UNTRUSTED"),
        ({**SOURCE_REVIEW_RUN, "head_branch": "attacker"}, "WORKFLOW_RUN_UNTRUSTED"),
        ({**SOURCE_REVIEW_RUN, "conclusion": "failure"}, "WORKFLOW_RUN_UNTRUSTED"),
        ({**SOURCE_REVIEW_RUN, "run_attempt": 2}, "RUN_ATTEMPT_MISMATCH"),
    ],
)
def test_automated_review_requires_current_authenticated_source_run(source_run, reason):
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match=reason):
        verifier.build_receipt(
            evidence(),
            expected_repository=REPOSITORY,
            expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806",
            expected_pr_number=1822,
            expected_incident_id="1806",
            expected_workflow_run_id="12345",
            expected_workflow_run_attempt=1,
            automated_review=automated_review_data(),
            source_review_run=source_run,
            source_review_artifact_id=555,
            now=NOW,
        )


def test_automated_review_requires_bound_sha_diff_author_and_artifact_identity():
    stale = automated_review_data()
    stale["target"]["head_sha"] = "b" * 40
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="HEAD_MISMATCH"):
        verifier.build_receipt(
            evidence(),
            expected_repository=REPOSITORY,
            expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806",
            expected_pr_number=1822,
            expected_incident_id="1806",
            expected_workflow_run_id="12345",
            expected_workflow_run_attempt=1,
            automated_review=stale,
            source_review_run=SOURCE_REVIEW_RUN,
            source_review_artifact_id=555,
            now=NOW,
        )

    wrong_diff = automated_review_data()
    wrong_diff["diff"]["file_paths"] = ["src/other.py"]
    wrong_diff["review"]["reviewed_files"] = ["src/other.py"]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="DIFF_INVENTORY_MISMATCH"):
        verifier.build_receipt(
            evidence(),
            expected_repository=REPOSITORY,
            expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806",
            expected_pr_number=1822,
            expected_incident_id="1806",
            expected_workflow_run_id="12345",
            expected_workflow_run_attempt=1,
            automated_review=wrong_diff,
            source_review_run=SOURCE_REVIEW_RUN,
            source_review_artifact_id=555,
            now=NOW,
        )

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="ARTIFACT_ID_MISSING"):
        verifier.build_receipt(
            evidence(),
            expected_repository=REPOSITORY,
            expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806",
            expected_pr_number=1822,
            expected_incident_id="1806",
            expected_workflow_run_id="12345",
            expected_workflow_run_attempt=1,
            automated_review=automated_review_data(),
            source_review_run=SOURCE_REVIEW_RUN,
            now=NOW,
        )


def test_bot_author_is_not_a_required_identity_for_ordinary_fixes():
    data = evidence(author={"id": 77, "login": "wow-engineering[bot]", "type": "Bot"})

    assert build(data)["independent_review"]["reviewer"] == {**REVIEWER, "type": "User"}


def test_empty_allowlist_holds():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="ALLOWLIST_MISSING"):
        build(trusted_reviewers=[])


@pytest.mark.parametrize(
    "reviewer",
    [
        {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN},
        {"id": 88, "login": "unknown[bot]"},
    ],
)
def test_owner_or_bot_cannot_be_added_as_independent_reviewer(reviewer):
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="NOT_INDEPENDENT_HUMAN"):
        build(trusted_reviewers=[reviewer])


def test_duplicate_allowlist_entries_hold():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="DUPLICATE_REVIEWER"):
        build(trusted_reviewers=[REVIEWER, REVIEWER])


def test_unapproved_or_stale_head_review_holds():
    data = evidence(review_state="COMMENTED")
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)

    data = evidence(review_sha="b" * 40)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)


def test_author_cannot_approve_their_own_pr():
    data = evidence(author={"id": REVIEWER["id"], "login": REVIEWER["login"], "type": "User"})

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)


def test_latest_trusted_reviewer_state_must_remain_approved():
    data = evidence()
    data["reviews"].append(
        {
            "id": 13,
            "state": "CHANGES_REQUESTED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {**REVIEWER, "type": "User"},
        }
    )

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="APPROVAL_MISSING"):
        build(data)


def test_receipt_rejects_withdrawn_or_replaced_live_approval():
    receipt = build()
    withdrawn = evidence()["reviews"] + [
        {
            "id": 13,
            "state": "DISMISSED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {**REVIEWER, "type": "User"},
        }
    ]

    with pytest.raises(
        verifier.ExistingPrGovernanceReceiptError,
        match="EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING|WITHDRAWN_OR_CHANGED",
    ):
        verify(receipt, live_reviews=withdrawn)


@pytest.mark.parametrize("field", ["complete_inventory", "reviews", "check_runs", "workflow_runs"])
def test_incomplete_or_missing_inventory_holds(field):
    data = evidence()
    if field == "complete_inventory":
        data[field] = False
    else:
        data[field] = None
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError):
        build(data)


def test_unknown_review_identity_holds():
    data = evidence()
    data["reviews"][0]["user"] = {"login": "independent-reviewer", "type": "User"}

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="IDENTITY_UNKNOWN"):
        build(data)


def test_unallowlisted_human_review_identity_holds():
    data = evidence()
    data["reviews"].append(
        {
            "id": 13,
            "state": "COMMENTED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {"id": 444, "login": "unknown-human", "type": "User"},
        }
    )

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="NOT_ALLOWLISTED"):
        build(data)


@pytest.mark.parametrize(("name", "app_id"), [("WOW required-three regression", 1)])
def test_wrong_check_app_identity_holds(name, app_id):
    data = evidence()
    check = next(item for item in data["check_runs"] if item["name"] == name)
    check["app"]["id"] = app_id

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CHECK_MISSING"):
        build(data)


def test_non_green_source_regression_holds():
    data = evidence()
    data["check_runs"][-1]["conclusion"] = "failure"

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CHECK_NOT_GREEN"):
        build(data)


def test_pre_governance_receipt_precedes_qa_and_release_but_final_checks_remain_mandatory():
    data = evidence()
    data["check_runs"].append(
        {
            "id": 500,
            "name": release_checks.QA_CHECK,
            "head_sha": HEAD,
            "app_id": 5245832,
            "status": "in_progress",
            "conclusion": None,
            "started_at": "2026-10-17T23:30:00Z",
        }
    )
    receipt = build(data)
    assert receipt["evidence_state"] == "REVIEW_EVIDENCE_VERIFIED"
    assert set(receipt["required_checks"]) == {
        "WOW required-three regression",
        "WOW governed probability backend",
        "WOW additional required regression",
    }
    assert "merge_authorized" not in receipt
    assert "qa" not in receipt and "release" not in receipt

    later = release_evidence()
    missing_qa = release_checks.release_findings(
        later, qa_app_id=5245832, release_app_id=5245853
    )
    assert "RELEASE_QA_MISSING" in missing_qa
    assert release_checks.decision("release", missing_qa, later)["conclusion"] == "failure"

    later["check_runs"].append(final_app_check(release_checks.QA_CHECK, 5245832, "success", 601))
    assert release_checks.release_findings(
        later, qa_app_id=5245832, release_app_id=5245853
    ) == []
    assert release_checks.decision("release", [], later)["conclusion"] == "success"

    later["check_runs"][0]["conclusion"] = "failure"
    later["check_runs"].append(final_app_check(release_checks.QA_CHECK, 5245832, "failure", 602))
    failed_qa = release_checks.qa_findings(later)
    failed_release = release_checks.release_findings(
        later, qa_app_id=5245832, release_app_id=5245853
    )
    assert release_checks.decision("qa", failed_qa, later)["conclusion"] == "failure"
    assert "RELEASE_QA_FAILED" in failed_release
    assert release_checks.decision("release", failed_release, later)["conclusion"] == "failure"
    assert release_checks.decision("release", failed_release, later)["merge_or_release_authorized"] is False

    failed_release = release_checks.release_findings(
        release_evidence(), qa_app_id=5245832, release_app_id=5245832
    )
    assert "RELEASE_IDENTITY_NOT_DISTINCT" in failed_release
    assert release_checks.decision("release", failed_release, release_evidence())["conclusion"] == "failure"


def final_app_check(name, app_id, conclusion, check_id):
    return {
        "id": check_id,
        "name": name,
        "head_sha": HEAD,
        "app_id": app_id,
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": "2026-10-17T23:45:00Z",
    }


def release_evidence():
    checks = [
        {
            "id": index + 1,
            "name": name,
            "head_sha": HEAD,
            "app_id": 15368,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-10-17T23:40:00Z",
        }
        for index, name in enumerate(
            (*release_checks.QA_EVIDENCE_CHECKS, release_checks.TRUSTED_GOVERNANCE_CHECK)
        )
    ]
    source_pr_evidence = evidence(author={"id": 77, "login": "wow-engineering[bot]", "type": "Bot"})
    source_review_artifact = automated_review_data(
        source_pr_evidence,
        now=datetime.now(timezone.utc),
    )
    return {
        "repository": verifier.CANONICAL_REPOSITORY,
        "pr_number": 1822,
        "expected_head_sha": HEAD,
        "owner_login": verifier.OWNER_LOGIN,
        "evidence_errors": [],
        "pr": {
            "body": "Incident: #2136",
            "state": "open",
            "draft": False,
            "merged": False,
            "head_sha": HEAD,
            "head_ref": "fix/issue-1806",
            "head_repo": verifier.CANONICAL_REPOSITORY,
            "base_ref": "main",
            "author": "wow-engineering[bot]",
            "author_id": 77,
            "changed_files": 1,
        },
        "files": ["src/fix.py"],
        "source_review_paths": ["src/fix.py"],
        "source_review": {
            "artifact_id": 555,
            "artifact": source_review_artifact,
            "run": SOURCE_REVIEW_RUN,
        },
        "qa_review_policy": {
            "mode": "independent",
            "target": {"pr_number": 1822, "head_sha": HEAD, "head_ref": "fix/issue-1806"},
            "canary_scope": None,
            "source": {"run_id": "12345", "run_attempt": 1},
        },
        "independent_review_active": True,
        "current_review_active": False,
        "check_runs": checks,
        "reviews": [],
    }


def test_missing_required_workflow_and_failed_latest_run_hold():
    data = evidence()
    data["workflow_runs"] = data["workflow_runs"][:-1]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="WORKFLOW_MISSING"):
        build(data)

    data = evidence()
    data["workflow_runs"][0]["conclusion"] = "failure"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="WORKFLOW_NOT_GREEN"):
        build(data)


def zero_job_shadow(*, verified=True, repository=REPOSITORY, head_repository=REPOSITORY, event="pull_request"):
    shadow = {
        "id": 999,
        "name": "wow-verify",
        "path": verifier.REQUIRED_WORKFLOWS["wow-verify"],
        "head_sha": HEAD,
        "status": "completed",
        "conclusion": "action_required",
        "event": event,
        "repository": {"full_name": repository},
        "head_repository": {"full_name": head_repository},
        "created_at": "2026-10-17T23:30:00Z",
    }
    if verified:
        shadow["pre_job_zero_jobs_verified"] = True
    return shadow


def test_verified_zero_job_action_required_shadow_does_not_hide_executed_success():
    data = evidence()
    data["workflow_runs"].append(zero_job_shadow())

    receipt = build(data)

    assert receipt["required_workflows"][0]["run_id"] == 100


@pytest.mark.parametrize(
    ("shadow", "reason"),
    [
        (zero_job_shadow(verified=False), "ZERO_JOB_SHADOW_UNVERIFIED"),
        (zero_job_shadow(repository="foreign/WOW-Dashboard"), "ZERO_JOB_REPOSITORY_MISMATCH"),
        (zero_job_shadow(head_repository="foreign/WOW-Dashboard"), "ZERO_JOB_REPOSITORY_MISMATCH"),
        (zero_job_shadow(event="workflow_dispatch"), "ZERO_JOB_SHADOW_UNVERIFIED"),
        ({**zero_job_shadow(), "status": "in_progress"}, "ZERO_JOB_SHADOW_UNVERIFIED"),
    ],
)
def test_unverified_action_required_shadow_fails_closed(shadow, reason):
    data = evidence()
    data["workflow_runs"].append(shadow)

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match=reason):
        build(data)


def test_zero_job_shadow_without_preceding_success_and_newer_executed_red_hold():
    data = evidence()
    data["workflow_runs"] = [
        run for run in data["workflow_runs"] if run["name"] != "wow-verify"
    ] + [zero_job_shadow()]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="WORKFLOW_MISSING"):
        build(data)

    data = evidence()
    data["workflow_runs"].append(zero_job_shadow())
    data["workflow_runs"].append({
        **data["workflow_runs"][0],
        "id": 1000,
        "created_at": "2026-10-17T23:40:00Z",
        "conclusion": "failure",
    })
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="WORKFLOW_NOT_GREEN"):
        build(data)


def run_exact_head_ci_step(tmp_path, runs, *, detail=None, jobs=None):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Verify exact-head deterministic CI\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    stub = """
gh() {
  case "$*" in
    "api /repos/${GITHUB_REPOSITORY}/actions/runs?head_sha=${HEAD_SHA}&per_page=100&page=1")
      printf '%s\\n' "$TEST_WORKFLOW_RUNS"
      ;;
    "api /repos/${GITHUB_REPOSITORY}/actions/runs/999")
      printf '%s\\n' "$TEST_ZERO_JOB_RUN"
      ;;
    "api /repos/${GITHUB_REPOSITORY}/actions/runs/999/jobs?per_page=100&page=1")
      printf '%s\\n' "$TEST_ZERO_JOB_INVENTORY"
      ;;
    *)
      echo "UNEXPECTED_GH_INVOCATION:$*" >&2
      return 1
      ;;
  esac
}
seq() { echo 1; }
sleep() { :; }
"""
    output_path = tmp_path / "outputs"
    output_path.write_text("", encoding="utf-8")
    evidence_dir = tmp_path / ".governance-review-evidence"
    evidence_dir.mkdir(exist_ok=True)
    env = os.environ | {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_OUTPUT": str(output_path),
        "HEAD_SHA": HEAD,
        "TEST_WORKFLOW_RUNS": json.dumps({"workflow_runs": runs}),
        "TEST_ZERO_JOB_RUN": json.dumps(detail or {}),
        "TEST_ZERO_JOB_INVENTORY": json.dumps(jobs or {"total_count": 0, "jobs": []}),
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", stub + script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = evidence_dir / "workflow-runs.json"
    return result, json.loads(output.read_text(encoding="utf-8")) if output.exists() else None


def zero_job_run_detail(**overrides):
    return {
        "id": 999,
        "name": "wow-verify",
        "path": verifier.REQUIRED_WORKFLOWS["wow-verify"],
        "head_sha": HEAD,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "action_required",
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
    } | overrides


def test_workflow_authenticates_and_marks_only_zero_job_shadow(tmp_path):
    runs = evidence()["workflow_runs"] + [{
        **zero_job_run_detail(),
        "created_at": "2026-10-17T23:30:00Z",
    }]

    result, verified_runs = run_exact_head_ci_step(
        tmp_path,
        runs,
        detail=zero_job_run_detail(),
    )

    assert result.returncode == 0, result.stderr
    shadow = next(item for item in verified_runs if item["id"] == 999)
    assert shadow["pre_job_zero_jobs_verified"] is True


@pytest.mark.parametrize(
    ("detail", "jobs", "reason"),
    [
        (zero_job_run_detail(repository={"full_name": "foreign/WOW-Dashboard"}), None,
         "EXISTING_PR_ZERO_JOB_RUN_IDENTITY_INVALID"),
        (zero_job_run_detail(path=".github/workflows/foreign.yml"), None,
         "EXISTING_PR_ZERO_JOB_RUN_IDENTITY_INVALID"),
        (zero_job_run_detail(head_sha="b" * 40), None,
         "EXISTING_PR_ZERO_JOB_RUN_IDENTITY_INVALID"),
        (zero_job_run_detail(), {"total_count": 1, "jobs": [{"id": 1}]},
         "EXISTING_PR_ACTION_REQUIRED_HAS_JOBS"),
        (zero_job_run_detail(), {"total_count": 0, "jobs": [{"id": 1}]},
         "EXISTING_PR_ACTION_REQUIRED_HAS_JOBS"),
        (zero_job_run_detail(), {"total_count": 0}, "EXISTING_PR_ACTION_REQUIRED_HAS_JOBS"),
    ],
)
def test_workflow_rejects_unverifiable_or_nonzero_action_required_jobs(tmp_path, detail, jobs, reason):
    runs = evidence()["workflow_runs"] + [{
        **zero_job_run_detail(),
        "created_at": "2026-10-17T23:30:00Z",
    }]

    result, _ = run_exact_head_ci_step(tmp_path, runs, detail=detail, jobs=jobs)

    assert result.returncode == 1
    assert reason in result.stderr


def test_workflow_fails_on_newer_real_red_or_shadow_without_older_success(tmp_path):
    good = evidence()["workflow_runs"]
    shadow = {**zero_job_run_detail(), "created_at": "2026-10-17T23:30:00Z"}
    red = {
        **good[0],
        "id": 1000,
        "created_at": "2026-10-17T23:40:00Z",
        "conclusion": "failure",
    }
    result, _ = run_exact_head_ci_step(tmp_path, good + [shadow, red], detail=zero_job_run_detail())
    assert result.returncode == 1
    assert "EXISTING_PR_REQUIRED_CI_FAILED:wow-verify" in result.stderr

    no_success = [run for run in good if run["name"] != "wow-verify"] + [shadow]
    result, _ = run_exact_head_ci_step(tmp_path, no_success, detail=zero_job_run_detail())
    assert result.returncode == 1
    assert "EXISTING_PR_REQUIRED_CI_HAS_NO_EXECUTED_SUCCESS:wow-verify" in result.stderr


def test_pending_newer_workflow_still_holds_after_bounded_wait(tmp_path):
    runs = evidence()["workflow_runs"]
    runs.append({
        **runs[0],
        "id": 1000,
        "created_at": "2026-10-17T23:40:00Z",
        "status": "in_progress",
        "conclusion": None,
    })

    result, _ = run_exact_head_ci_step(tmp_path, runs)

    assert result.returncode == 1
    assert "EXISTING_PR_REQUIRED_CI_INCOMPLETE_AFTER_BOUNDED_WAIT" in result.stderr


def run_receipt_artifact_step(tmp_path, artifacts, *, total=None):
    # Emulate GitHub's exact-name server-filtered artifact inventory, rather
    # than the old unbounded repository-wide artifact scan.
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow.split("      - name: Check for an existing exact-head receipt\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    stub = """
gh() {
  case "$*" in
    "api /repos/${GITHUB_REPOSITORY}/actions/artifacts?name=wow-v17-engineering-governance-${HEAD_SHA}&per_page=100&page=1")
      printf '%s\\n' "$TEST_ARTIFACTS_JSON"
      ;;
    *)
      echo "UNEXPECTED_GH_INVOCATION:$*" >&2
      return 1
      ;;
  esac
}
"""
    output_path = tmp_path / "outputs"
    output_path.write_text("", encoding="utf-8")
    env = os.environ | {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_OUTPUT": str(output_path),
        "HEAD_SHA": HEAD,
        "TEST_ARTIFACTS_JSON": json.dumps({
            "total_count": len(artifacts) if total is None else total,
            "artifacts": artifacts,
        }),
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", stub + script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = dict(
        line.split("=", 1) for line in output_path.read_text(encoding="utf-8").splitlines()
    )
    return result, output


def test_existing_governance_receipt_suppresses_duplicate_upload(tmp_path):
    existing = {"id": 13, "name": f"wow-v17-engineering-governance-{HEAD}", "expired": False}
    result, output = run_receipt_artifact_step(tmp_path, [existing])

    assert result.returncode == 0, result.stderr
    assert output == {"exists": "true"}


def test_duplicate_governance_receipts_fail_closed(tmp_path):
    existing = {"id": 13, "name": f"wow-v17-engineering-governance-{HEAD}", "expired": False}
    result, _ = run_receipt_artifact_step(tmp_path, [existing, existing])

    assert result.returncode == 1
    assert "EXISTING_PR_RECEIPT_ARTIFACT_DUPLICATE" in result.stderr


def test_receipt_artifact_inventory_must_finish_within_bound(tmp_path):
    # More than one bounded page for the exact-name inventory fails closed;
    # a large set of unrelated repository artifacts no longer blocks progress.
    result, _ = run_receipt_artifact_step(tmp_path, [], total=101)

    assert result.returncode == 1
    assert "EXISTING_PR_RECEIPT_ARTIFACT_DUPLICATE" in result.stderr


@pytest.mark.parametrize("path", [".github/workflows/unsafe.yml", ".agents/skills/unsafe.md", "render.yaml"])
def test_trust_root_changes_cannot_be_certified(path):
    data = evidence()
    data["changed_files"] = [{"filename": path, "previous_filename": None, "status": "modified"}]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_CHANGE"):
        build(data)


def test_renaming_a_trust_root_is_rejected():
    data = evidence()
    data["changed_files"] = [{
        "filename": "src/renamed.py",
        "previous_filename": ".github/scripts/verify.py",
        "status": "renamed",
    }]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_CHANGE"):
        build(data)


@pytest.mark.parametrize(
    ("file_record", "reason"),
    [
        ({"previous_filename": None, "status": "modified"}, "DIFF_FILE_SCHEMA_INVALID"),
        ({"filename": "src/fix.py", "previous_filename": None}, "DIFF_FILE_SCHEMA_INVALID"),
        (
            {"filename": "src/fix.py", "previous_filename": None, "status": "unknown"},
            "DIFF_STATUS_INVALID",
        ),
        (
            {"filename": ".", "previous_filename": None, "status": "modified"},
            "DIFF_PATH_INVALID",
        ),
        (
            {"filename": "src/fix.py", "previous_filename": None, "status": "renamed"},
            "DIFF_PREVIOUS_FILENAME_MISSING",
        ),
    ],
)
def test_changed_file_inventory_requires_typed_provenance(file_record, reason):
    data = evidence()
    data["changed_files"] = [file_record]

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match=reason):
        build(data)


def test_every_dynamically_declared_governance_trust_root_is_protected():
    protected = verifier._protected_paths(REPO_ROOT)
    policy_path = REPO_ROOT / "artifacts/wow-engine/v17/agent_identity_policy.py"
    policy_spec = importlib.util.spec_from_file_location("agent_identity_policy_test", policy_path)
    policy = importlib.util.module_from_spec(policy_spec)
    assert policy_spec and policy_spec.loader
    policy_spec.loader.exec_module(policy)

    assert set(policy.governance_trust_roots(REPO_ROOT)) <= protected
    assert set(policy.trust_root_paths(REPO_ROOT)) <= protected

    for path in protected:
        data = evidence()
        data["changed_files"] = [{"filename": path, "previous_filename": None, "status": "modified"}]
        with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_CHANGE"):
            build(data)


def test_draft_fork_or_stale_pr_head_holds():
    data = evidence()
    data["pr"]["draft"] = True
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="STATE_OR_BASE_MISMATCH"):
        build(data)

    data = evidence()
    data["pr"]["head"]["repo"]["full_name"] = "fork/WOW-Dashboard"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="STATE_OR_BASE_MISMATCH"):
        build(data)

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TARGET_MISMATCH"):
        verify(build(), head="b" * 40)


def test_schema_mutation_and_legacy_schema_fail_closed():
    receipt = build()
    receipt["governance_complete"] = True
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="SCHEMA_INVALID"):
        verify(receipt)

    receipt = build()
    receipt["receipt_schema_version"] = 1
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="VERSION_UNSUPPORTED"):
        verify(receipt)


def test_receipt_cannot_relabel_a_bot_as_an_independent_human():
    receipt = build()
    receipt["independent_review"]["reviewer"]["type"] = "Bot"

    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REVIEW_INVALID"):
        verify(receipt)


def test_receipt_ttl_is_30_minutes_within_provider_sunset():
    current = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
    receipt = build(now=current)
    assert receipt["fallback_expires_utc"] == "2026-10-18T00:30:00Z"
    verify(receipt, now=current)
    verify(receipt, now=current + timedelta(minutes=29, seconds=59))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_EXPIRED_OR_FUTURE_RECEIPT"):
        verify(receipt, now=current + timedelta(minutes=30))


def test_temporary_provider_sunset_blocks_new_receipts_and_replay():
    before_sunset = datetime(2026, 10, 31, 23, 40, tzinfo=timezone.utc)
    receipt = build(now=before_sunset)
    assert receipt["fallback_expires_utc"] == "2026-11-01T00:10:00Z"
    verify(receipt, now=before_sunset + timedelta(minutes=19, seconds=59))
    at_sunset = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        verify(receipt, now=at_sunset)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        build(now=at_sunset)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
        build(now=datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc))


def test_authenticated_automated_review_receipts_remain_usable_after_legacy_sunset():
    after_sunset = datetime(2026, 11, 1, 0, 1, tzinfo=timezone.utc)
    receipt = verifier.build_receipt(
        evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        automated_review=automated_review_data(now=after_sunset),
        source_review_run=SOURCE_REVIEW_RUN,
        source_review_artifact_id=555,
        now=after_sunset,
    )
    verifier.verify_receipt(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        live_reviews=[],
        source_review_run=SOURCE_REVIEW_RUN,
        now=after_sunset + timedelta(minutes=29),
    )
    assert receipt["receipt_schema_version"] == 3
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="EXPIRED_OR_FUTURE_RECEIPT"):
        verifier.verify_receipt(
            receipt,
            expected_repository=REPOSITORY,
            expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806",
            expected_workflow_run_id="12345",
            expected_workflow_name=verifier.WORKFLOW_NAME,
            expected_pr_number=1822,
            live_reviews=[],
            source_review_run=SOURCE_REVIEW_RUN,
            now=after_sunset + timedelta(minutes=30),
        )


def test_receipt_expiry_cannot_be_extended_or_backdated():
    receipt = build()
    receipt["fallback_expires_utc"] = "2036-11-02T00:00:00Z"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="FALLBACK_EXPIRY_MISMATCH"):
        verify(receipt)

    receipt = build()
    receipt["created_at_utc"] = "2026-10-17T00:00:00Z"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="FALLBACK_EXPIRY_MISMATCH"):
        verify(receipt)


def test_independent_approval_still_required_with_fresh_receipt():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"):
        build(data=evidence(review_state="COMMENTED"))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"):
        build(data=evidence(review_state="DISMISSED"))


def test_v2_fallback_sunset_is_immutable_and_fail_closed():
    # Production policy is pinned separately; never infer this from wall time.
    cutoff = datetime.fromisoformat(verifier.FALLBACK_PROVIDER_SUNSET_UTC.replace("Z", "+00:00"))
    assert cutoff == datetime(2026, 11, 1, tzinfo=timezone.utc)
    receipt = build(now=NOW)
    verify(receipt, now=NOW)
    for expired_time in (cutoff, cutoff + timedelta(seconds=1)):
        with pytest.raises(verifier.ExistingPrGovernanceReceiptError,
                           match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
            verify(receipt, now=expired_time)
        with pytest.raises(verifier.ExistingPrGovernanceReceiptError,
                           match="TEMPORARY_FALLBACK_PROVIDER_SUNSET"):
            build(now=expired_time)


def test_consumer_context_requires_independently_bound_pr_and_live_reviews(tmp_path, monkeypatch, capsys):
    # Historical acceptance uses explicit time injection into the pure verifier,
    # never a module-global datetime override or production clock override.
    receipt = build(now=NOW)
    verify(receipt, now=NOW)
    live_reviews = tmp_path / "reviews.json"
    live_reviews.write_text(json.dumps(evidence()["reviews"]), encoding="utf-8")
    (tmp_path / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "verify",
        "--receipt", str(tmp_path / "receipt.json"),
        "--repository", REPOSITORY,
        "--head-sha", HEAD,
        "--head-ref", "fix/issue-1806",
        "--workflow-run-id", "12345",
        "--workflow-name", verifier.WORKFLOW_NAME,
        "--pr-number", "1822",
        "--live-reviews-json", str(live_reviews),
    ])

    # The real CLI must reject V2 after sunset. It never receives a test clock.
    assert datetime.now(timezone.utc) >= datetime.fromisoformat(
        verifier.FALLBACK_PROVIDER_SUNSET_UTC.replace("Z", "+00:00")
    )
    assert verifier.main() == 1
    outcome = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert outcome == {"status": "HOLD", "reason": "TEMPORARY_FALLBACK_PROVIDER_SUNSET"}


def test_consumer_context_does_not_infer_pr_number_from_event(tmp_path, monkeypatch):
    (tmp_path / "receipt.json").write_text(json.dumps(build()), encoding="utf-8")
    (tmp_path / "reviews.json").write_text(json.dumps(evidence()["reviews"]), encoding="utf-8")
    (tmp_path / "event.json").write_text(json.dumps({"pull_request": {"number": 1822}}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(tmp_path / "event.json"))
    monkeypatch.setattr(sys, "argv", [
        "verify",
        "--receipt", str(tmp_path / "receipt.json"),
        "--repository", REPOSITORY,
        "--head-sha", HEAD,
        "--head-ref", "fix/issue-1806",
        "--workflow-run-id", "12345",
        "--workflow-name", verifier.WORKFLOW_NAME,
        "--live-reviews-json", str(tmp_path / "reviews.json"),
    ])

    assert verifier.main() == 1


def test_consumer_context_rejects_other_pr(tmp_path):
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="PR_NUMBER_MISMATCH"):
        verifier.verify_receipt(
            build(),
            expected_repository=REPOSITORY,
            expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806",
            expected_workflow_run_id="12345",
            expected_workflow_name=verifier.WORKFLOW_NAME,
            expected_pr_number=1823,
            live_reviews=evidence()["reviews"],
            now=NOW,
        )


def test_terminal_and_execution_invariants_are_enforced():
    receipt = build()
    receipt["can_execute"] = True
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CAN_EXECUTE"):
        verify(receipt)

    receipt = build()
    receipt["terminal_authority"] = "OTHER"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TERMINAL_AUTHORITY"):
        verify(receipt)


def test_trusted_governance_workflow_executes_legacy_and_v2_verifier_argv_shapes(tmp_path):
    workflow = (REPO_ROOT / ".github/workflows/wow-v17-trusted-governance-gate.yml").read_text()
    start = workflow.index('verifier=".github/scripts/verify_engineering_governance_receipt.py"')
    end_marker = 'echo "Trusted exact-head governance receipt verified. can_execute=false."'
    end = workflow.index(end_marker, start) + len(end_marker)
    invocation = workflow[start:end]

    def run(workflow_name, receipt, live_reviews=None, live_checks=None, source_run=None):
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        reviews_path = tmp_path / "reviews.json"
        reviews_path.write_text(json.dumps(live_reviews or []), encoding="utf-8")
        checks_path = tmp_path / "checks.json"
        checks_path.write_text(json.dumps(live_checks or []), encoding="utf-8")
        source_run_path = tmp_path / "source-review-run.json"
        if source_run is not None:
            source_run_path.write_text(json.dumps(source_run), encoding="utf-8")
        elif source_run_path.exists():
            source_run_path.unlink()
        script = (
            'set -euo pipefail\n'
            'receipt="$RECEIPT_PATH"\n'
            'live_reviews="$LIVE_REVIEWS_PATH"\n'
            'live_checks="$LIVE_CHECKS_PATH"\n'
            'cert_mode=$(python -c \'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("certification_mode") or "")\' "$receipt")\n'
            'receipt_schema=$(python -c \'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("receipt_schema_version") or "")\' "$receipt")\n'
            'source_review_run="$SOURCE_REVIEW_RUN_PATH"\n'
            f"{invocation}\n"
        )
        env = os.environ | {
            "WORKFLOW_NAME": workflow_name,
            "RECEIPT_PATH": str(receipt_path),
            "LIVE_REVIEWS_PATH": str(reviews_path),
            "LIVE_CHECKS_PATH": str(checks_path),
            "SOURCE_REVIEW_RUN_PATH": str(tmp_path / "source-review-run.json"),
            "PR_NUMBER": "1822",
            "PR_HEAD_SHA": HEAD,
            "PR_HEAD_REF": "fix/issue-1806",
            "RUN_ID": "12345",
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    legacy = {
        "receipt_schema_version": 1,
        "workflow_name": "wow-v17-chatgpt-engineering-worker",
        "repository": REPOSITORY,
        "workflow_run_id": "12345",
        "implementation": {
            "changed": True,
            "branch": "fix/issue-1806",
            "head_sha": HEAD,
            "incident_id": "1806",
        },
        "handoff": {
            "lead_dispatch": {"decision": "REPAIR"},
            "triage": {"repairable": True, "incident_id": "1806"},
            "engineering": {"changed": True, "incident_id": "1806"},
            "independent_review": {"decision": "PASS"},
            "system_architect": {"decision": "NOT_APPLICABLE"},
            "deterministic_regression": {"status": "0"},
            "qa": {"decision": "PASS"},
        },
        "governance_complete": True,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
    legacy_valid = run("wow-v17-chatgpt-engineering-worker", legacy)
    assert legacy_valid.returncode == 0, legacy_valid.stderr
    legacy["implementation"]["head_sha"] = "b" * 40
    legacy_invalid = run("wow-v17-chatgpt-engineering-worker", legacy)
    assert legacy_invalid.returncode == 1
    assert "receipt head SHA mismatch" in legacy_invalid.stdout

    # Real subprocess rejects expired V2 while other controls remain testable.
    receipt = build(now=NOW)
    reviews = evidence()["reviews"]
    v2_valid = run(verifier.WORKFLOW_NAME, receipt, reviews)
    assert v2_valid.returncode == 1, v2_valid.stderr
    assert "TEMPORARY_FALLBACK_PROVIDER_SUNSET" in v2_valid.stdout
    withdrawn = reviews + [
        {
            "id": 13,
            "state": "DISMISSED",
            "commit_id": HEAD,
            "submitted_at": "2026-10-17T23:01:00Z",
            "user": {**REVIEWER, "type": "User"},
        }
    ]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError,
                       match="EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING"):
        verifier.verify_receipt(
            receipt, expected_repository=REPOSITORY, expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806", expected_workflow_run_id="12345",
            expected_workflow_name=verifier.WORKFLOW_NAME, expected_pr_number=1822,
            live_reviews=withdrawn, now=NOW,
        )

    renamed_source = dict(receipt)
    renamed_source["changed_files"] = [
        {
            "filename": "src/relocated.py",
            "previous_filename": ".github/scripts/verify_existing_pr_governance_receipt.py",
            "status": "renamed",
        }
    ]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError,
                       match="TRUST_ROOT_CHANGE_NOT_CERTIFIABLE"):
        verifier.verify_receipt(
            renamed_source, expected_repository=REPOSITORY, expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806", expected_workflow_run_id="12345",
            expected_workflow_name=verifier.WORKFLOW_NAME, expected_pr_number=1822,
            live_reviews=reviews, now=NOW,
        )

    boot_data = bootstrap_evidence()
    boot = build_bootstrap(boot_data, now=datetime.now(timezone.utc))
    boot_ok = run(verifier.WORKFLOW_NAME, boot, boot_data["reviews"], boot_data["check_runs"])
    assert boot_ok.returncode == 0, boot_ok.stdout + boot_ok.stderr
    assert "TRUST_ROOT_BOOTSTRAP_VERIFIED" in boot_ok.stdout
    assert "REVIEW_EVIDENCE_VERIFIED" not in boot_ok.stdout
    withdrawn_owner = json.loads(json.dumps(boot_data["reviews"]))
    withdrawn_owner[-1]["state"] = "CHANGES_REQUESTED"
    boot_withdrawn = run(verifier.WORKFLOW_NAME, boot, withdrawn_owner, boot_data["check_runs"])
    assert boot_withdrawn.returncode == 1
    assert "TRUST_ROOT_OWNER_APPROVAL_MISSING" in boot_withdrawn.stdout

    automated = verifier.build_receipt(
        evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        automated_review=automated_review_data(now=datetime.now(timezone.utc)),
        source_review_run=SOURCE_REVIEW_RUN,
        source_review_artifact_id=555,
        now=datetime.now(timezone.utc),
    )
    automated_valid = run(
        verifier.WORKFLOW_NAME, automated, live_reviews=[], source_run=SOURCE_REVIEW_RUN
    )
    assert automated_valid.returncode == 0, automated_valid.stdout + automated_valid.stderr
    automated_missing_source = run(verifier.WORKFLOW_NAME, automated, live_reviews=[])
    assert automated_missing_source.returncode == 1
    assert "SOURCE_REVIEW_RUN_METADATA_MISSING" in automated_missing_source.stdout


def test_trusted_gate_revalidates_authenticated_automated_review_source():
    workflow = TRUSTED_GATE.read_text(encoding="utf-8")

    assert "workflows: [wow-v17-existing-pr-governance-review]" in workflow
    assert "GOVERNANCE_SOURCE_REVIEW_RUN_UNTRUSTED" in workflow
    assert "GOVERNANCE_SOURCE_REVIEW_ARTIFACT_UNTRUSTED" in workflow
    assert "GOVERNANCE_SOURCE_REVIEW_ARTIFACT_CONTENT_MISMATCH" in workflow
    assert "GOVERNANCE_SOURCE_REVIEW_ARTIFACT_CHECKSUM_MISMATCH" in workflow
    assert "verify_unique_artifact_inventory" in workflow
    assert "--source-review-run-json" in workflow
    assert ".workflow_run.id == $run_id" in workflow


def test_trusted_gate_accepts_only_authenticated_release_check_run_certifications():
    workflow = TRUSTED_GATE.read_text(encoding="utf-8")

    assert "github.event.workflow_run.event == 'check_run'" in workflow
    assert '(.event == "workflow_run" or .event == "check_run")' in workflow
    assert "Release check-run certification must run from protected main" in workflow


def test_release_and_continuation_consumers_resolve_authenticated_source_review_metadata():
    for path in (RELEASE_WORKFLOW, MORNING_GREEN_WORKFLOW):
        workflow = path.read_text(encoding="utf-8")
        assert "GH_TOKEN: ${{ github.token }}" in workflow
        assert "verify_existing_pr_governance_receipt.py" in workflow
        assert "--receipt" in workflow


def test_trust_root_bootstrap_receipt_uses_owner_approval_not_an_allowlist():
    receipt = build_bootstrap()
    verify_bootstrap(receipt)
    assert receipt["certification_mode"] == "TRUST_ROOT_BOOTSTRAP"
    assert receipt["evidence_state"] == "TRUST_ROOT_BOOTSTRAP_VERIFIED"
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert "independent_review" not in receipt
    assert "trusted_reviewers" not in receipt
    assert set(receipt["required_checks"]) == set(verifier.BOOTSTRAP_CHECKS)
    assert "WOW Independent QA exact-head" in receipt["required_checks"]
    assert "WOW Release Authority exact-head" in receipt["required_checks"]
    for name in verifier.REQUIRED_CHECKS:
        assert name in receipt["required_checks"]


def test_trust_root_bootstrap_holds_for_missing_or_stale_owner_approval():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_MISSING"):
        build_bootstrap(bootstrap_evidence(owner_state="CHANGES_REQUESTED"))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_MISSING"):
        build_bootstrap(bootstrap_evidence(owner_sha="b" * 40))
    data = bootstrap_evidence()
    data["reviews"] = [review for review in data["reviews"] if review["user"]["id"] != verifier.OWNER_ID]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_MISSING"):
        build_bootstrap(data)


def test_owner_authored_trust_root_change_cannot_bootstrap():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_AUTHORED"):
        build_bootstrap(bootstrap_evidence(author={
            "id": verifier.OWNER_ID,
            "login": verifier.OWNER_LOGIN,
            "type": "User",
        }))


def test_ordinary_diff_cannot_use_the_bootstrap_receipt():
    data = bootstrap_evidence()
    data["changed_files"] = [{"filename": "src/fix.py", "previous_filename": None, "status": "modified"}]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_REQUIRED"):
        build_bootstrap(data)


def test_bootstrap_requires_green_qa_release_and_exact_check_provenance():
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_NOT_GREEN"):
        build_bootstrap(bootstrap_evidence(qa_conclusion="failure"))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_MISSING"):
        build_bootstrap(bootstrap_evidence(qa_app=1))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_MISSING"):
        build_bootstrap(bootstrap_evidence(check_sha="b" * 40))


def test_bootstrap_receipt_expires_in_30_minutes_without_a_provider_sunset():
    current = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
    receipt = build_bootstrap(now=current)
    assert receipt["fallback_expires_utc"] == "2026-10-18T00:30:00Z"
    verify_bootstrap(receipt, now=current)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_EXPIRED_OR_FUTURE_RECEIPT"):
        verify_bootstrap(receipt, now=current + timedelta(minutes=30))
    at_sunset = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    post_sunset_receipt = build_bootstrap(now=at_sunset)
    verify_bootstrap(post_sunset_receipt, now=at_sunset + timedelta(minutes=29))
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TEMPORARY_FALLBACK_EXPIRED_OR_FUTURE_RECEIPT"):
        verify_bootstrap(post_sunset_receipt, now=at_sunset + timedelta(minutes=30))


def test_live_recheck_rejects_replaced_owner_review_and_superseded_checks():
    data = bootstrap_evidence()
    receipt = build_bootstrap(data)
    replaced = json.loads(json.dumps(data["reviews"]))
    replaced.append({
        "id": 88,
        "state": "APPROVED",
        "commit_id": HEAD,
        "submitted_at": "2026-10-17T23:20:00Z",
        "user": {"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN, "type": "User"},
    })
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="TRUST_ROOT_OWNER_APPROVAL_WITHDRAWN_OR_CHANGED"):
        verify_bootstrap(receipt, data=data, live_reviews=replaced)
    superseded = json.loads(json.dumps(data["check_runs"]))
    superseded.append({
        "id": 999,
        "name": "WOW Release Authority exact-head",
        "head_sha": HEAD,
        "app": {"id": 5245853},
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-10-17T23:40:00Z",
    })
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_SUPERSEDED"):
        verify_bootstrap(receipt, data=data, live_checks=superseded)


def test_protected_worker_paths_classify_as_trust_root_bootstrap():
    for path in (
        ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
        ".github/workflows/wow-v17-claude-engineering-worker.yml",
        ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        "render.yaml",
    ):
        data = bootstrap_evidence()
        data["changed_files"] = [{"filename": path, "previous_filename": None, "status": "modified"}]
        assert verifier.classify_changed_files(
            data["changed_files"], repo_root=REPO_ROOT, expected_count=1,
        ) == verifier.BOOTSTRAP_MODE
    ordinary = evidence()
    assert verifier.classify_changed_files(
        ordinary["changed_files"], repo_root=REPO_ROOT, expected_count=1,
    ) == verifier.ORDINARY_MODE


def test_cli_splits_bootstrap_and_ordinary_receipts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(REPO_ROOT)
    data = bootstrap_evidence()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "receipt.json"
    common = [
        "--evidence", str(evidence_path),
        "--repository", REPOSITORY,
        "--head-sha", HEAD,
        "--head-ref", "fix/issue-1806",
        "--workflow-run-id", "12345",
        "--workflow-run-attempt", "1",
        "--workflow-name", verifier.WORKFLOW_NAME,
        "--pr-number", "1822",
        "--incident-id", "1806",
    ]
    monkeypatch.setattr(sys, "argv", ["verify", *common, "--classify"])
    assert verifier.main() == 0
    monkeypatch.setattr(sys, "argv", ["verify", *common, "--output", str(output), "--bootstrap"])
    assert verifier.main() == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["certification_mode"] == "TRUST_ROOT_BOOTSTRAP"
    assert receipt["can_execute"] is False
    monkeypatch.setattr(sys, "argv", [
        "verify", *common, "--output", str(output), "--bootstrap",
        "--trusted-reviewers-json", json.dumps(ALLOWLIST),
    ])
    assert verifier.main() == 1
    ordinary = evidence()
    evidence_path.write_text(json.dumps(ordinary), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify", *common, "--classify"])
    assert verifier.main() == 0
    monkeypatch.setattr(sys, "argv", [
        "verify", *common, "--output", str(output),
        "--trusted-reviewers-json", json.dumps(ALLOWLIST),
    ])
    # Ordinary V2 is no longer a permitted live CLI outcome. The previous
    # bootstrap receipt must remain intact after a rejected ordinary build.
    assert verifier.main() == 1
    outcome = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert outcome == {"status": "HOLD", "reason": "TEMPORARY_FALLBACK_PROVIDER_SUNSET"}
    assert json.loads(output.read_text(encoding="utf-8")) == receipt
    # Preserve deterministic historical positive coverage without freezing
    # process-global datetime or making expired live receipts acceptable.
    historical = build(now=NOW)
    verify(historical, now=NOW)
    assert historical["certification_mode"] == verifier.CERTIFICATION_MODE


@pytest.mark.parametrize('filename,previous_filename', (
    ('.claude/skills/__new_skill__/SKILL.md', None),
    ('.claude/agents/__new_agent__.md', None),
    ('.claude/settings.json', None),
    ('docs/renamed-skill.md', '.claude/skills/__old_skill__/SKILL.md'),
    ('.claude/agents/renamed.md', 'docs/previous.md'),
))
def test_claude_instructions_cannot_use_ordinary_source_receipt(filename, previous_filename):
    files = [{'filename': filename, 'previous_filename': previous_filename}]
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError,
                       match='TRUST_ROOT_CHANGE_NOT_CERTIFIABLE'):
        verifier._validate_protected_diff(files, repo_root=REPO_ROOT)


def test_exact_name_artifact_inventory_lookup_is_bounded_and_fail_closed(tmp_path):
    """Do not scan all artifacts; enforce cardinality, identity and expiry."""
    import shutil
    if shutil.which("jq") is None:
        pytest.skip("jq required for protected GitHub runner shell contract")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    lookup = workflow.split(
        "      - name: Check for an existing exact-head receipt\n", 1
    )[1]
    script = textwrap.dedent(lookup.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    assert "artifacts?name=${artifact_name}&per_page=100&page=1" in script
    assert "EXISTING_PR_RECEIPT_ARTIFACT_DUPLICATE" in script
    head = "b" * 40
    name = f"wow-v17-engineering-governance-{head}"
    stub = r'''
gh() {
  [ "$1" = "api" ] || return 1
  [ "$2" = "/repos/${GITHUB_REPOSITORY}/actions/artifacts?name=wow-v17-engineering-governance-${HEAD_SHA}&per_page=100&page=1" ] || return 1
  printf '%s\n' "$TEST_ARTIFACTS_JSON"
}
'''
    def evaluate(items, *, total=None):
        payload = {"total_count": len(items) if total is None else total, "artifacts": items}
        target = tmp_path / "output"
        target.write_text("", encoding="utf-8")
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", stub + script],
            env=os.environ | {
                "HEAD_SHA": head,
                "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_OUTPUT": str(target),
                "TEST_ARTIFACTS_JSON": json.dumps(payload),
            },
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        ), target.read_text(encoding="utf-8")

    absent, output = evaluate([])
    assert absent.returncode == 0, absent.stderr
    assert "exists=false" in output

    expired, output = evaluate([{"id": 13, "name": name, "expired": True}])
    assert expired.returncode == 0, expired.stderr
    assert "exists=false" in output

    present, output = evaluate([{"id": 14, "name": name, "expired": False}])
    assert present.returncode == 0, present.stderr
    assert "exists=true" in output

    duplicate, _ = evaluate([
        {"id": 14, "name": name, "expired": False},
        {"id": 15, "name": name, "expired": False},
    ])
    assert duplicate.returncode != 0
    assert "EXISTING_PR_RECEIPT_ARTIFACT_DUPLICATE" in duplicate.stderr

    wrong_name, _ = evaluate([{"id": 14, "name": name + "-other", "expired": False}])
    assert wrong_name.returncode != 0
    assert "EXISTING_PR_RECEIPT_ARTIFACT_INVENTORY_INVALID" in wrong_name.stderr

    malformed, _ = evaluate([{"id": 14, "name": name}])
    assert malformed.returncode != 0
    assert "EXISTING_PR_RECEIPT_ARTIFACT_INVENTORY_INVALID" in malformed.stderr

    page_mismatch, _ = evaluate([{"id": 14, "name": name, "expired": False}], total=2)
    assert page_mismatch.returncode != 0
    assert "EXISTING_PR_RECEIPT_ARTIFACT_INVENTORY_INVALID" in page_mismatch.stderr

    overflow, _ = evaluate([], total=101)
    assert overflow.returncode != 0
    assert "EXISTING_PR_RECEIPT_ARTIFACT_DUPLICATE" in overflow.stderr


# P0 #2136: zero-credit deterministic Class A is a separate reduced-assurance
# receipt, never a human or adversarial review substitute for model/governance.
def class_a_evidence():
    data = evidence(author={"id": verifier.OWNER_ID, "login": verifier.OWNER_LOGIN, "type": "User"})
    data["reviews"] = []
    data["changed_files"] = [{
        "filename": "artifacts/wow-engine/learning/evaluate.py",
        "previous_filename": None,
        "status": "modified",
    }]
    data["pr"]["changed_files"] = 1
    for index, (name, app_id) in enumerate(verifier.DETERMINISTIC_CHECKS.items()):
        data["check_runs"].append({
            "id": 800 + index,
            "name": name,
            "head_sha": HEAD,
            "app": {"id": app_id},
            "status": "completed",
            "conclusion": "success",
            "started_at": f"2026-10-17T23:{10+index:02d}:00Z",
            "completed_at": f"2026-10-17T23:{10+index:02d}:01Z",
            "output": {"summary": (
                "ZERO-CREDIT DETERMINISTIC QA ONLY; no semantic/adversarial source "
                "review performed. Exact-head evidence satisfied. No merge/deploy "
                "authority. can_execute=false."
            )},
        })
    return data


def build_class_a(data=None, *, now=NOW):
    return verifier.build_receipt(
        data if data is not None else class_a_evidence(),
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_pr_number=1822,
        expected_incident_id="1806",
        expected_workflow_run_id="12345",
        expected_workflow_run_attempt=1,
        deterministic_class_a=True,
        now=now,
    )


def verify_class_a(receipt, data=None, *, now=NOW):
    source = data if data is not None else class_a_evidence()
    return verifier.verify_receipt(
        receipt,
        expected_repository=REPOSITORY,
        expected_head_sha=HEAD,
        expected_head_ref="fix/issue-1806",
        expected_workflow_run_id="12345",
        expected_workflow_name=verifier.WORKFLOW_NAME,
        expected_pr_number=1822,
        live_reviews=source["reviews"],
        live_checks=source["check_runs"],
        now=now,
    )


def test_class_a_deterministic_receipt_is_explicitly_not_semantic():
    source = class_a_evidence()
    receipt = build_class_a(source)
    assert receipt["receipt_schema_version"] == 4
    assert receipt["certification_mode"] == verifier.DETERMINISTIC_MODE
    assert receipt["assurance"] == verifier.DETERMINISTIC_ASSURANCE
    assert receipt["independent_review"] is None
    assert receipt["trusted_reviewers"] == []
    assert receipt["evidence_state"] == "CLASS_A_DETERMINISTIC_VERIFIED"
    assert receipt["can_execute"] is False
    assert receipt["terminal_authority"] == verifier.TERMINAL_AUTHORITY
    assert set(verifier.DETERMINISTIC_CHECKS).issubset(receipt["required_checks"])
    verify_class_a(receipt, source)


@pytest.mark.parametrize("path", [
    ".github/workflows/wow-v17-independent-qa-check.yml",
    ".agents/skills/wow-nightly-engineering-autopilot/SKILL.md",
    "artifacts/wow-engine/v17/independent_release_checks.py",
    "artifacts/wow-engine/v17/engineering_dispatch_manifest.json",
    "artifacts/flask-scoring-api/models/scorer.py",
    "render.yaml",
    "docs/wow/engineering/V17_AUTONOMY_FIRST_GOVERNANCE_DESIGN.md",
])
def test_class_a_denies_governance_model_and_nonallowlisted_paths(path):
    source = class_a_evidence()
    source["changed_files"][0]["filename"] = path
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_|TRUST_ROOT_"):
        build_class_a(source)


@pytest.mark.parametrize("status,previous", [
    ("renamed", "artifacts/wow-engine/learning/README.md"),
    ("copied", "artifacts/wow-engine/learning/README.md"),
    ("removed", None),
])
def test_class_a_denies_renames_copies_and_removals(status, previous):
    source = class_a_evidence()
    source["changed_files"][0].update(status=status, previous_filename=previous)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_"):
        build_class_a(source)


@pytest.mark.parametrize("field,value,reason", [
    ("head_sha", "b" * 40, "REQUIRED_CHECK_MISSING"),
    ("conclusion", "failure", "REQUIRED_CHECK_NOT_GREEN"),
    ("status", "in_progress", "REQUIRED_CHECK_NOT_GREEN"),
])
def test_class_a_rejects_stale_or_failed_release_app(field, value, reason):
    source = class_a_evidence()
    source["check_runs"][-1][field] = value
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match=reason):
        build_class_a(source)



def test_class_a_exact_canonical_training_inventory_not_entire_directory():
    expected = {
        "artifacts/wow-engine/learning/AGENT_TRAINING.md",
        "artifacts/wow-engine/learning/README.md",
        "artifacts/wow-engine/learning/challenges/reference.py",
        "artifacts/wow-engine/learning/challenges/run_challenge_tests.py",
        "artifacts/wow-engine/learning/challenges/starter.py",
        "artifacts/wow-engine/learning/challenges/test_contracts.py",
        "artifacts/wow-engine/learning/data/cases.json",
        "artifacts/wow-engine/learning/evaluate.py",
        "artifacts/wow-engine/learning/tests/test_evaluate.py",
        "artifacts/wow-engine/test_v17_triple_d_learning_smoke.py",
    }
    assert verifier.CLASS_A_EXACT_PATHS == expected
    source = class_a_evidence()
    source["pr"]["changed_files"] = len(expected)
    source["changed_files"] = [
        {"filename": path, "status": "added", "previous_filename": None}
        for path in sorted(expected)
    ]
    assert build_class_a(source)["certification_mode"] == verifier.DETERMINISTIC_MODE
    for unsafe in (
        "artifacts/wow-engine/learning/new_unreviewed.py",
        "artifacts/wow-engine/learning/../../v17/independent_release_checks.py",
    ):
        source["changed_files"][0]["filename"] = unsafe
        with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_|DIFF_PATH_INVALID"):
            build_class_a(source)
        source["changed_files"][0]["filename"] = sorted(expected)[0]

def test_class_a_rejects_foreign_qa_app_and_false_semantic_label():
    source = class_a_evidence()
    source["check_runs"][-2]["app"]["id"] = 15368
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="REQUIRED_CHECK_MISSING"):
        build_class_a(source)
    source = class_a_evidence()
    source["check_runs"][-1]["output"]["summary"] = "All checks passed"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_ASSURANCE_LABEL_MISSING"):
        build_class_a(source)


def test_class_a_rejects_legacy_review_relabel_and_no_live_checks():
    source = class_a_evidence()
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_CANNOT_IMPERSONATE"):
        verifier.build_receipt(
            source,
            expected_repository=REPOSITORY, expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806", expected_pr_number=1822,
            expected_incident_id="1806", expected_workflow_run_id="12345",
            expected_workflow_run_attempt=1, trusted_reviewers=ALLOWLIST,
            deterministic_class_a=True, now=NOW,
        )
    receipt = build_class_a(source)
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_LIVE_CHECKS_MISSING"):
        verifier.verify_receipt(
            receipt, expected_repository=REPOSITORY, expected_head_sha=HEAD,
            expected_head_ref="fix/issue-1806", expected_workflow_run_id="12345",
            expected_workflow_name=verifier.WORKFLOW_NAME,
            expected_pr_number=1822, live_reviews=[], now=NOW,
        )


def test_class_a_replay_detects_newer_failure_and_replaced_checks():
    source = class_a_evidence()
    receipt = build_class_a(source)
    source["check_runs"][-1]["id"] = 911
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_LIVE_CHECK_REPLACED"):
        verify_class_a(receipt, source)
    source = class_a_evidence()
    receipt = build_class_a(source)
    source["check_runs"][-1]["conclusion"] = "failure"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_LIVE_CHECK_NOT_GREEN"):
        verify_class_a(receipt, source)


def test_class_a_schema_cannot_claim_semantic_review_or_change_assurance():
    source = class_a_evidence()
    receipt = build_class_a(source)
    receipt["independent_review"] = {"type": "AUTOMATED_SOURCE_REVIEW"}
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_MUST_NOT_CLAIM"):
        verify_class_a(receipt, source)
    receipt = build_class_a(source)
    receipt["assurance"] = "INDEPENDENT_SEMANTIC_REVIEW_PASSED"
    with pytest.raises(verifier.ExistingPrGovernanceReceiptError, match="CLASS_A_ASSURANCE_IDENTITY_MISMATCH"):
        verify_class_a(receipt, source)


def test_class_a_workflow_and_consumers_require_no_provider_or_human_approval():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "WOW_QA_ZERO_CREDIT_MODE" in workflow
    assert "--deterministic-class-a" in workflow
    assert "INDEPENDENT_REVIEW_EVIDENCE_MISSING" in workflow  # other classes still hold
    for path in (TRUSTED_GATE, RELEASE_WORKFLOW, MORNING_GREEN_WORKFLOW):
        source = path.read_text(encoding="utf-8")
        assert "CLASS_A_DETERMINISTIC" in source or path == TRUSTED_GATE
        assert "--live-checks-json" in source
    assert '[ "$receipt_schema" = "4" ]' in TRUSTED_GATE.read_text(encoding="utf-8")


def test_class_a_classifier_does_not_capture_other_ordinary_or_protected_prs():
    eligible = class_a_evidence()["changed_files"]
    assert verifier.classify_changed_files(
        eligible, repo_root=REPO_ROOT, expected_count=len(eligible),
    ) == verifier.DETERMINISTIC_MODE

    mixed = eligible + [{"filename": "src/unrelated.py", "status": "added", "previous_filename": None}]
    assert verifier.classify_changed_files(
        mixed, repo_root=REPO_ROOT, expected_count=len(mixed),
    ) == verifier.ORDINARY_MODE

    new_training = [{"filename": "artifacts/wow-engine/learning/unapproved_new.py",
                     "status": "added", "previous_filename": None}]
    assert verifier.classify_changed_files(
        new_training, repo_root=REPO_ROOT, expected_count=1,
    ) == verifier.ORDINARY_MODE

    protected = eligible + [{
        "filename": ".github/workflows/release.yml", "status": "modified", "previous_filename": None,
    }]
    assert verifier.classify_changed_files(
        protected, repo_root=REPO_ROOT, expected_count=len(protected),
    ) == verifier.BOOTSTRAP_MODE


def test_class_a_workflow_does_not_force_deterministic_route_on_nonclass_a():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "TRUST_ROOT_BOOTSTRAP|ORDINARY_ENGINEERING|CLASS_A_DETERMINISTIC" in workflow
    assert '"$classification" = "CLASS_A_DETERMINISTIC"' in workflow
    assert "--trusted-reviewers-json" in workflow
    assert "--source-review-json" in workflow  # reviewed non-Class-A PRs retain their path
    assert "INDEPENDENT_REVIEW_EVIDENCE_MISSING" in workflow
