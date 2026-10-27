"""Static negative contract: QA and Release queues never collide across PRs.

No-target *skipped* events must not crowd genuine QA/Release requests. Real and
uncertain Release evidence must retain fail-closed serialization. Queue changes
are scheduling only: no authorization, evidence or merge authority is granted.
"""
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
QA = 'wow-v17-independent-qa-check'
RELEASE = 'wow-v17-release-authority-check'
ROLES = (QA, RELEASE)
EXPRESSIONS = {
    QA: "${{ inputs.pr_number || github.event.workflow_run.pull_requests[0].number || github.run_id }}",
    RELEASE: "${{ inputs.pr_number || github.event.workflow_run.pull_requests[0].number || (github.event.workflow_run.conclusion == 'skipped' && github.run_id) || 'AUTO' }}",
}


def config(role):
    path = ROOT / '.github/workflows' / f'{role}.yml'
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize('role', ROLES)
def test_queue_never_silently_drops_a_pending_target(role):
    workflow = config(role)
    c = workflow['concurrency']
    assert c['group'] == role + '-' + EXPRESSIONS[role]
    assert c['cancel-in-progress'] is False
    assert c['queue'] == 'max'
    assert 'workflow_dispatch' in str(workflow.get('on', workflow.get(True, '')))
    assert 'workflow_run' in str(workflow.get('on', workflow.get(True, '')))


def _group(role, *, manual=None, upstream=None, run_id=None, upstream_qa_conclusion=None):
    # Mirror the exact checked GitHub expression, not free-form target inference.
    identity = manual or upstream
    if not identity:
        if role == QA or (role == RELEASE and upstream_qa_conclusion == 'skipped'):
            assert isinstance(run_id, int) and run_id > 0
            identity = run_id
        else:
            identity = 'AUTO'  # Uncertain genuine Release evidence stays serialized.
    return f'{role}-{identity}'


@pytest.mark.parametrize('role', ROLES)
def test_manual_and_upstream_same_pr_share_a_serialized_queue(role):
    assert _group(role, manual='2012', run_id=11) == _group(role, upstream=2012, run_id=12)
    assert _group(role, manual='2012', run_id=11) != _group(role, upstream=2011, run_id=12)


def test_qa_release_principals_have_disjoint_queues():
    assert _group(QA, manual='2012', run_id=11) != _group(RELEASE, manual='2012', run_id=11)
    assert _group(QA, run_id=40107281706) != _group(RELEASE, run_id=40107281706)


def test_targetless_qa_runs_are_unique_and_cannot_block_real_pr():
    assert _group(QA, run_id=40107281706) != _group(QA, run_id=40107306470)
    assert _group(QA, run_id=40107281706) != _group(QA, manual='2012')
    qa = config(QA)
    assert "(github.event_name != 'workflow_run' || github.event.workflow_run.pull_requests[0])" in str(qa['jobs']['qa']['if'])
    assert 'github.run_id' in qa['concurrency']['group']


@pytest.mark.parametrize('conclusion', ['success', 'failure', 'cancelled', 'timed_out', None])
def test_unresolved_non_skipped_release_qa_keeps_one_bounded_auto_queue(conclusion):
    one = _group(RELEASE, run_id=40107281706, upstream_qa_conclusion=conclusion)
    two = _group(RELEASE, run_id=40107306470, upstream_qa_conclusion=conclusion)
    assert one == two == f'{RELEASE}-AUTO'
    assert one != _group(RELEASE, manual='2012', run_id=40107281706)


def test_only_provably_skipped_upstream_qa_may_use_unique_release_queue():
    first = _group(RELEASE, run_id=40107281706, upstream_qa_conclusion='skipped')
    second = _group(RELEASE, run_id=40107306470, upstream_qa_conclusion='skipped')
    assert first != second
    assert first != _group(RELEASE, manual='2012')
    release = config(RELEASE)
    assert "github.event.workflow_run.conclusion == 'skipped'" in release['concurrency']['group']
    assert "(github.event_name != 'workflow_run' || github.event.workflow_run.conclusion != 'skipped')" in str(release['jobs']['release']['if'])
    assert release['concurrency']['group'].endswith("|| 'AUTO' }}")


@pytest.mark.parametrize('role', ROLES)
def test_identity_and_exact_head_contract_stays_enabled(role):
    workflow = (ROOT / '.github/workflows' / f'{role}.yml').read_text()
    assert 'head_sha' in workflow
    assert 'protected main' in workflow.lower()
    assert 'permission-checks: write' in workflow
    assert 'can_execute=false' in workflow
    assert 'cancel-in-progress: false' in workflow
