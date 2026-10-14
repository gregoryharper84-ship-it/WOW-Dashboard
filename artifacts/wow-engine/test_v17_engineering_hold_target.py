"""Adversarial target routing regression suite; no live GitHub secrets required."""
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib.util
import pytest
module_file = Path(__file__).resolve().parent / 'v17/engineering_hold_target.py'
if not module_file.is_file():
    module_file = Path(__file__).resolve().parent / 'engineering_hold_target.py'
spec = importlib.util.spec_from_file_location('engineering_hold_target', module_file)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

S1 = 'a' * 40
S2 = 'b' * 40
MAIN = 'c' * 40
QA, RELEASE = 5245832, 5245853
BASE = '/repos/gregoryharper84-ship-it/WOW-Dashboard'


def run(role='qa'):
    workflow = 'wow-v17-independent-qa-check' if role == 'qa' else 'wow-v17-release-authority-check'
    return {'workflow_run': {
        'name': workflow + ' event=workflow_run pr=AUTO',
        'path': '.github/workflows/' + workflow + '.yml',
        'head_sha': MAIN, 'head_branch': 'main',
        'head_repository': {'full_name': h.REPO},
        'event': 'workflow_run', 'status': 'completed', 'conclusion': 'success',
        'run_started_at': '2026-10-14T04:30:00Z',
        'updated_at': '2026-10-14T04:31:00Z',
    }}


def pr(number=101, sha=S1, author=h.APP_AUTHOR, draft=False):
    return {'number': number, 'state': 'open', 'draft': draft,
            'user': {'login': author}, 'base': {'ref': 'main'},
            'head': {'sha': sha, 'repo': {'full_name': h.REPO}}}


def check(checkid=777, sha=S1, app=QA, name=h.QA_CHECK, conclusion='failure',
          completed='2026-10-14T04:30:30Z', summary=None):
    return {'id': checkid, 'name': name, 'head_sha': sha, 'app': {'id': app},
            'status': 'completed', 'conclusion': conclusion, 'completed_at': completed,
            'output': {'summary': summary if summary is not None else
                       'Typed findings:\n- `QA_REGRESSION_FAILED`\n\nFail-closed. can_execute=false.'}}


def fetcher(prs=None, checks=None, truncated=False):
    prs = prs if prs is not None else [pr()]
    checks = checks if checks is not None else {S1: [check()]}
    def fetch(path):
        assert 'page=' in path
        if '/pulls?' in path:
            return prs
        for sha, items in checks.items():
            if '/commits/' + sha + '/check-runs' in path:
                return {'check_runs': items, 'total_count': len(items)+(1 if truncated else 0)}
        raise AssertionError(path)
    return fetch


def select(fetch=None, event=None, event_name='workflow_run', now=None):
    return h.discover(fetch or fetcher(), event if event is not None else run(), event_name,
                      h.REPO, QA, RELEASE,
                      now=now or datetime(2026, 10, 14, 4, 31, tzinfo=timezone.utc))


def test_event_main_sha_resolves_only_app_pr_head():
    target = select()['targets'][0]
    assert target == {'pr_number': 101, 'head_sha': S1,
                      'source_check_id': 777, 'source_app_id': QA, 'source_role': 'qa'}
    assert target['head_sha'] != run()['workflow_run']['head_sha']


def test_multiple_app_prs_are_not_dropped():
    f = fetcher([pr(101, S1), pr(202, S2)],
                {S1: [check(777, S1)], S2: [check(888, S2)]})
    assert [x['pr_number'] for x in select(fetch=f)['targets']] == [101, 202]


def test_spoofed_check_app_is_not_authority():
    assert select(fetch=fetcher(checks={S1: [check(app=999)]}))['targets'] == []


def test_owner_pr_and_draft_are_never_routed():
    f = fetcher([pr(101, S1, author='gregoryharper84-ship-it'), pr(202, S2, draft=True)],
                {S1: [check()], S2: [check(10, S2)]})
    assert select(fetch=f)['targets'] == []


def test_latest_pass_supersedes_old_fail():
    f = fetcher(checks={S1: [check(777), check(778, conclusion='success')]})
    assert select(fetch=f)['targets'] == []


def test_late_old_hold_cannot_replay_for_new_event():
    f = fetcher(checks={S1: [check(completed='2026-10-14T03:10:00Z')]})
    assert select(fetch=f)['targets'] == []


def test_new_head_ignores_prior_head_hold():
    f = fetcher(prs=[pr(101, S2)], checks={S2: [check(777, S1)]})
    assert select(fetch=f)['targets'] == []


def test_other_role_not_dispatched_on_qa_completion():
    f = fetcher(checks={S1: [check(app=RELEASE, name=h.RELEASE_CHECK)]})
    assert select(fetch=f)['targets'] == []
    assert select(fetch=f,event=run('release'))['targets'][0]['source_role'] == 'release'


def test_untrusted_trigger_path_is_rejected():
    ev = run();ev['workflow_run']['path']='.github/workflows/evil.yml'
    with pytest.raises(h.Blocked, match='UNTRUSTED_UPSTREAM_WORKFLOW'):
        select(event=ev)


def test_untrusted_trigger_repo_or_main_is_rejected():
    ev = run();ev['workflow_run']['head_repository']={'full_name':'fork/evil'}
    with pytest.raises(h.Blocked, match='UNTRUSTED_UPSTREAM_RUN_IDENTITY'):
        select(event=ev)
    ev = run();ev['workflow_run']['head_branch']='feature'
    with pytest.raises(h.Blocked, match='UNTRUSTED_UPSTREAM_RUN_IDENTITY'):
        select(event=ev)


def test_unbound_or_duplicate_qa_release_id_fails_closed():
    with pytest.raises(h.Blocked, match='INDEPENDENT_APP_IDENTITIES_INVALID'):
        h.discover(fetcher(),run(),'workflow_run',h.REPO,QA,QA)
    with pytest.raises(h.Blocked, match='INDEPENDENT_APP_IDENTITIES_INVALID'):
        h.discover(fetcher(),run(),'workflow_run',h.REPO,'',RELEASE)


def test_invalid_hold_summary_fails_closed():
    f = fetcher(checks={S1: [check(summary='PASS')]})
    with pytest.raises(h.Blocked, match='APP_HOLD_SUMMARY_MALFORMED'):
        select(fetch=f)


def test_truncated_check_inventory_fails_closed():
    with pytest.raises(h.Blocked, match='INVENTORY_TRUNCATED'):
        select(fetch=fetcher(truncated=True))


def test_scheduled_reentry_picks_undispatched_valid_head():
    target = select(event={},event_name='schedule')['targets'][0]
    assert target['head_sha'] == S1


def test_old_unresolved_hold_stays_schedulable_until_acknowledged():
    f=fetcher(checks={S1: [check(completed='2026-10-11T04:30:30Z')]})
    result = select(fetch=f,event={},event_name='schedule')
    assert [t['pr_number'] for t in result['targets']] == [101]


def test_future_dated_independent_check_fails_closed():
    f=fetcher(checks={S1: [check(completed='2026-10-15T04:30:30Z')]})
    assert select(fetch=f,event={},event_name='schedule')['targets'] == []


def test_bounded_event_batch_does_not_fail_entire_queue():
    prs=[pr(i+1, f'{i+1:040x}') for i in range(h.MAX_TARGETS+1)]
    cs={p['head']['sha']:[check(777+i,p['head']['sha'])] for i,p in enumerate(prs)}
    result=select(fetch=fetcher(prs,cs))
    assert len(result['targets']) == h.MAX_TARGETS
    assert result['can_execute'] is False
    assert any(x['finding']=='BOUNDED_HOLD_BATCH_DEFERRED' for x in result['deferred'])


def test_hourly_rotation_visits_all_57_outstanding_holds():
    n=57
    prs=[pr(i+1, f'{i+1:040x}') for i in range(n)]
    cs={p['head']['sha']:[check(777+i,p['head']['sha'],
         completed='2026-10-11T04:30:30Z')] for i,p in enumerate(prs)}
    seen=set()
    for hour in range(8):
        now=datetime(2026,10,14,5+hour,tzinfo=timezone.utc)
        result=select(fetch=fetcher(prs,cs),event={},event_name='schedule',now=now)
        assert len(result['targets']) <= h.MAX_TARGETS
        seen.update(t['pr_number'] for t in result['targets'])
    assert seen == set(range(1,n+1))


def test_workflow_template_enforces_protected_pr_and_same_head_marker():
    root = Path(__file__).resolve().parents[2]
    wf = root / '.github/workflows/wow-v17-engineering-auto-repair.yml'
    template = wf.read_text() if wf.is_file() else (Path(__file__).parent/'patch_workflow.py').read_text()
    assert 'already_dispatched=true' in template
    assert 'source_check_id' in template
    assert 'head_sha: ${HEAD_SHA}' in template
    assert 'HOLD_SUPERSEDED_OR_UNTRUSTED' in template
    assert 'fromJSON(needs.discover.outputs.targets)' in template
    assert 'HOLD_APP_IDENTITY_MISMATCH' in template
    assert '          GH_TOKEN: ${{ github.token }}' in template


def _load_real_planner():
    planner_file = Path(__file__).resolve().parent / 'v17/engineering_recovery_planner.py'
    if not planner_file.is_file():
        pytest.skip('Full planner is present in GitHub checkout; local bundle tests exercise the patch recipe')
    spec = importlib.util.spec_from_file_location('engineering_recovery_planner', planner_file)
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    return planner


def test_actual_planner_routes_colon_typed_regression_failure():
    planner = _load_real_planner()
    codes = planner.parse_findings_from_summary(
        'Typed findings:\n- `QA_EVIDENCE_FAILED:WOW required-three regression`\nFail-closed. can_execute=false.')
    assert codes == ['QA_EVIDENCE_FAILED:WOW required-three regression']
    verdict = planner.plan_recovery(pr_number=1, findings=codes, attempts=0, is_wow_engineering_pr=True)
    assert verdict['action'] == 'DISPATCH_REPAIR' and verdict['attempts'] == 1


def test_actual_planner_does_not_repair_while_ci_or_qa_evidence_missing():
    planner = _load_real_planner()
    for code in ('QA_EVIDENCE_MISSING:WOW governed probability backend',
                 'QA_EVIDENCE_INCOMPLETE:CHECK_RUNS_UNREADABLE:403',
                 'RELEASE_QA_FAILED', 'RELEASE_QA_MISSING'):
        verdict = planner.plan_recovery(pr_number=1, findings=[code], attempts=2,
                                        is_wow_engineering_pr=True)
        assert verdict['action'] == 'WAITING_ON_EVIDENCE'
        assert verdict['attempts'] == 2


def test_recovery_reservation_precedes_non_idempotent_dispatch():
    from pathlib import Path
    flow = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-engineering-auto-repair.yml"
    text = flow.read_text()
    assert "- name: Reserve exact-head recovery before dispatch" in text
    assert text.index("- name: Reserve exact-head recovery before dispatch") < text.index(
        "- name: Dispatch exact-head repair to Engineering App worker")
    assert "steps.reservation.outputs.claimed == 'true'" in text
    assert "state: DISPATCH_RESERVED" in text
    assert "state: DISPATCH_ACCEPTED" in text
    assert "COMMENT_ID: ${{ steps.reservation.outputs.comment_id }}" in text
    assert "RECOVERY_RESERVATION_ACK_UNREADABLE" in text


def test_legacy_attempt_marker_cannot_reissue_ambiguous_head():
    from pathlib import Path
    flow = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-engineering-auto-repair.yml"
    text = flow.read_text()
    assert "RECOVERY_MARKER_HEAD_INVALID_OR_LEGACY" in text
    assert 'recorded_head=$(jq -er' in text
    assert '<<<"$marker_comment" 2>/dev/null || true' not in text


def test_latest_independent_hold_revalidated_just_before_dispatch():
    from pathlib import Path
    flow = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-engineering-auto-repair.yml"
    text = flow.read_text()
    start = text.index("- name: Dispatch exact-head repair to Engineering App worker")
    end = text.index("- name: Record recovery attempt after accepted dispatch", start)
    section = text[start:end]
    assert 'DISPATCH_HOLD_SUPERSEDED_REQUIRES_RECONCILIATION' in section
    assert 'source_check_name="WOW Independent QA exact-head"' in section
    assert 'source_check_name="WOW Release Authority exact-head"' in section
    assert section.index('latest_authored=') < section.index('gh workflow run wow-v17-chatgpt-engineering-worker.yml')
    assert 'SOURCE_CHECK_ID: ${{ steps.resolve.outputs.source_check_id }}' in section
