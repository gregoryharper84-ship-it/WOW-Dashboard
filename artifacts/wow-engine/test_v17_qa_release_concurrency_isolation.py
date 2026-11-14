"""Static negative contract: QA and Release queues never collide across PRs.

Scope is solely scheduling. These tests do not assert approval or merge.
"""
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLES = (
    'wow-v17-independent-qa-check',
    'wow-v17-release-authority-check',
)
QA_EXPRESSION = "${{ inputs.pr_number || github.event.workflow_run.pull_requests[0].number || github.run_id }}"
RELEASE_EXPRESSION = "${{ needs.admit-release.outputs.pr_number }}"


def config(role):
    path = ROOT / '.github/workflows' / f'{role}.yml'
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize('role', ROLES)
def test_queue_never_silently_drops_a_pending_target(role):
    workflow = config(role)
    if role == 'wow-v17-release-authority-check':
        assert 'concurrency' not in workflow, 'no global AUTO Release queue'
        assert 'concurrency' not in workflow['jobs']['admit-release']
        assert workflow['jobs']['release']['needs'] == 'admit-release'
        c = workflow['jobs']['release']['concurrency']
        assert c['group'] == role + '-pr-' + RELEASE_EXPRESSION
        assert workflow['jobs']['release']['if'].find(
            "needs.admit-release.outputs.applicable == 'true'") >= 0
    else:
        c = workflow['concurrency']
        assert c['group'] == role + '-' + QA_EXPRESSION
    assert c['cancel-in-progress'] is False
    assert c['queue'] == 'max'
    assert 'workflow_dispatch' in str(workflow.get('on', workflow.get(True, '')))
    assert 'workflow_run' in str(workflow.get('on', workflow.get(True, '')))


def _group(role, *, manual=None, upstream=None, run_id=None):
    # Release may not acquire a writer without authenticated PR identity.
    # QA run_id is a safe neutral fallback for PRless observer events,
    # while genuine QA targets still serialize under their exact PR number.
    pr = manual or upstream
    if role == ROLES[1]:
        return f'{role}-pr-{pr}' if pr else None
    if pr is None:
        assert isinstance(run_id, int) and run_id > 0
        pr = run_id
    return f'{role}-{pr}'


@pytest.mark.parametrize('role', ROLES)
def test_manual_and_upstream_same_pr_share_a_serialized_queue(role):
    assert _group(role, manual='2012', run_id=11) == _group(role, upstream=2012, run_id=12)
    assert _group(role, manual='2012', run_id=11) != _group(role, upstream=2011, run_id=12)


def test_qa_release_principals_have_disjoint_queues():
    assert _group(ROLES[0], manual='2012', run_id=11) != _group(ROLES[1], manual='2012', run_id=11)
    assert _group(ROLES[0], run_id=40107281706) != _group(ROLES[1], run_id=40107281706)


@pytest.mark.parametrize('role', ROLES)
def test_unidentified_runs_do_not_displace_unrelated_prs(role):
    if role == ROLES[1]:
        # Unknown identity is read-only and may not enter a Release queue.
        assert _group(role, run_id=40107281706) is None
        assert _group(role, run_id=40107306470) is None
        group = config(role)['jobs']['release']['concurrency']['group']
        assert group.endswith(RELEASE_EXPRESSION)
        assert "github.run_id" not in group, "Release requires authenticated PR, never arbitrary run-ID"
        assert config(role)['jobs']['admit-release']['outputs']['applicable'] == (
            '${{ steps.target.outputs.applicable }}')
    else:
        assert _group(role, run_id=40107281706) != _group(role, run_id=40107306470)
        group = config(role)['concurrency']['group']
        assert group.endswith("|| github.run_id }}")
        assert '(github.event_name != \'workflow_run\' || github.event.workflow_run.pull_requests[0])' in str(config(role)['jobs']['qa']['if'])
    assert _group(role, run_id=40107281706) != _group(role, manual='2012', run_id=40107281706)
    assert _group(role, run_id=40107281706) != _group(role, upstream=1977, run_id=40104744926)


@pytest.mark.parametrize('role', ROLES)
def test_identity_and_exact_head_contract_stays_enabled(role):
    workflow = (ROOT / '.github/workflows' / f'{role}.yml').read_text()
    assert 'head_sha' in workflow
    assert 'protected main' in workflow.lower()
    assert 'permission-checks: write' in workflow
    assert 'can_execute=false' in workflow
    assert 'cancel-in-progress: false' in workflow
