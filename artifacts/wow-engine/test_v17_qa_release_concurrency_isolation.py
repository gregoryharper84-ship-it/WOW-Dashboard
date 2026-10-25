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
EXPRESSION = "${{ inputs.pr_number || github.event.workflow_run.pull_requests[0].number || 'AUTO' }}"


def config(role):
    path = ROOT / '.github/workflows' / f'{role}.yml'
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize('role', ROLES)
def test_queue_never_silently_drops_a_pending_target(role):
    workflow = config(role)
    c = workflow['concurrency']
    assert c['group'] == role + '-' + EXPRESSION
    assert c['cancel-in-progress'] is False
    assert c['queue'] == 'max'
    assert 'workflow_dispatch' in str(workflow.get('on', workflow.get(True, '')))
    assert 'workflow_run' in str(workflow.get('on', workflow.get(True, '')))


def _group(role, *, manual=None, upstream=None, run_id=None):
    # Mirror inputs.pr_number || pull_requests[0].number || 'AUTO'.
    # run_id is on the event and is not part of the key, so it cannot guess a PR.
    del run_id
    return f'{role}-{manual or upstream or "AUTO"}'


@pytest.mark.parametrize('role', ROLES)
def test_manual_and_upstream_same_pr_share_a_serialized_queue(role):
    assert _group(role, manual='2012', run_id=11) == _group(role, upstream=2012, run_id=12)
    assert _group(role, manual='2012', run_id=11) != _group(role, upstream=2011, run_id=12)


def test_qa_release_principals_have_disjoint_queues():
    assert _group(ROLES[0], manual='2012', run_id=11) != _group(ROLES[1], manual='2012', run_id=11)
    assert _group(ROLES[0], run_id=40107281706) != _group(ROLES[1], run_id=40107281706)


@pytest.mark.parametrize('role', ROLES)
def test_unidentified_runs_do_not_displace_unrelated_prs(role):
    assert _group(role, run_id=40107281706) == _group(role, run_id=40107306470) == f'{role}-AUTO'
    assert _group(role, run_id=40107281706) != _group(role, manual='2012', run_id=40107281706)
    assert _group(role, run_id=40107281706) != _group(role, upstream=1977, run_id=40104744926)
    assert '2016' not in _group(role, run_id=40107281706)
    group = config(role)['concurrency']['group']
    assert "github.run_id" not in group
    assert group.endswith("|| 'AUTO' }}")


@pytest.mark.parametrize('role', ROLES)
def test_identity_and_exact_head_contract_stays_enabled(role):
    workflow = (ROOT / '.github/workflows' / f'{role}.yml').read_text()
    assert 'head_sha' in workflow
    assert 'protected main' in workflow.lower()
    assert 'permission-checks: write' in workflow
    assert 'can_execute=false' in workflow
    assert 'cancel-in-progress: false' in workflow
