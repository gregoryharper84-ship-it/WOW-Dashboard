from pathlib import Path
import importlib.util
import sys
import textwrap

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "pr_authoring_token_audit.py"
REPO_ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("pr_authoring_token_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def write_workflow(tmp_path, body):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "sample.yml").write_text(textwrap.dedent(body))
    return tmp_path


GOOD_PUSH_STEP = """\
    name: wow-test
    on:
      workflow_dispatch:
    jobs:
      worker:
        runs-on: ubuntu-latest
        steps:
          - name: Mint wow-engineering App token
            id: eng_token
            uses: ./.github/actions/wow-engineering-token
          - name: Push branch
            env:
              GITHUB_TOKEN: ${{ steps.eng_token.outputs.token }}
            run: |
              git push --set-upstream origin "$branch"
"""

BAD_PUSH_STEP = """\
    name: wow-test
    on:
      workflow_dispatch:
    jobs:
      worker:
        runs-on: ubuntu-latest
        steps:
          - name: Push branch
            env:
              GITHUB_TOKEN: ${{ github.token }}
            run: |
              git push --set-upstream origin "$branch"
"""

BAD_PR_CREATE_NO_TOKEN = """\
    name: wow-test
    on:
      workflow_dispatch:
    jobs:
      worker:
        runs-on: ubuntu-latest
        steps:
          - name: Open PR
            run: |
              gh pr create --title x --body y
"""

GOOD_READ_ONLY_STEP_UNCHANGED = """\
    name: wow-test
    on:
      workflow_dispatch:
    jobs:
      worker:
        runs-on: ubuntu-latest
        steps:
          - name: List PRs
            env:
              GH_TOKEN: ${{ github.token }}
            run: |
              gh pr list --state open
"""

BAD_JOB_LEVEL_ENV_FALLBACK = """\
    name: wow-test
    on:
      workflow_dispatch:
    jobs:
      worker:
        runs-on: ubuntu-latest
        env:
          GH_TOKEN: ${{ github.token }}
        steps:
          - name: Open PR
            run: |
              gh pr create --title x --body y
"""

GOOD_JOB_LEVEL_ENV_FALLBACK = """\
    name: wow-test
    on:
      workflow_dispatch:
    jobs:
      worker:
        runs-on: ubuntu-latest
        env:
          GH_TOKEN: ${{ steps.eng_token.outputs.token }}
        steps:
          - name: Open PR
            run: |
              gh pr create --title x --body y
"""


def test_push_step_with_engineering_token_passes(tmp_path):
    root = write_workflow(tmp_path, GOOD_PUSH_STEP)
    assert audit.scan_repo(root) == []


def test_push_step_with_default_token_is_a_violation(tmp_path):
    root = write_workflow(tmp_path, BAD_PUSH_STEP)
    violations = audit.scan_repo(root)
    assert len(violations) == 1
    assert violations[0]["step"] == "Push branch"
    assert violations[0]["resolved_token"] == "${{ github.token }}"


def test_pr_create_step_with_no_token_at_all_is_a_violation(tmp_path):
    root = write_workflow(tmp_path, BAD_PR_CREATE_NO_TOKEN)
    violations = audit.scan_repo(root)
    assert len(violations) == 1
    assert violations[0]["resolved_token"] is None


def test_read_only_step_is_not_flagged(tmp_path):
    root = write_workflow(tmp_path, GOOD_READ_ONLY_STEP_UNCHANGED)
    assert audit.scan_repo(root) == []


def test_job_level_env_fallback_default_token_is_a_violation(tmp_path):
    root = write_workflow(tmp_path, BAD_JOB_LEVEL_ENV_FALLBACK)
    violations = audit.scan_repo(root)
    assert len(violations) == 1


def test_job_level_env_fallback_engineering_token_passes(tmp_path):
    root = write_workflow(tmp_path, GOOD_JOB_LEVEL_ENV_FALLBACK)
    assert audit.scan_repo(root) == []


def test_main_exits_nonzero_and_prints_typed_code_on_violation(tmp_path, capsys):
    write_workflow(tmp_path, BAD_PUSH_STEP)
    rc = audit.main(["--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "PR_AUTHORING_TOKEN_NOT_ENGINEERING_APP" in captured.err


def test_main_exits_zero_on_clean_repo(tmp_path, capsys):
    write_workflow(tmp_path, GOOD_PUSH_STEP)
    rc = audit.main(["--repo-root", str(tmp_path)])
    assert rc == 0


def test_real_repo_workflows_pass_the_audit():
    """Integration acceptance test for #1550/#1663 item 1: repo-wide, not a fixture."""
    violations = audit.scan_repo(REPO_ROOT)
    assert violations == []
