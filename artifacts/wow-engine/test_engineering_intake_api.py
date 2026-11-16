"""TDD regression tests for GitHub intake page validation and completeness."""
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).parent / 'v17' / 'engineering_intake_api.py'


def load():
    spec = importlib.util.spec_from_file_location('engineering_intake_api', SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(i):
    return {'number': i, 'state': 'open', 'labels': []}


def worker(i):
    return {'id': i, 'display_title': f'worker incident={i}', 'created_at': '2026-11-16T00:00:00Z',
            'updated_at': '2026-11-16T00:01:00Z', 'status': 'completed', 'conclusion': 'failure'}


class FakeRun:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        result = next(self.responses)
        if isinstance(result, tuple):
            return subprocess.CompletedProcess(args, result[0], stdout=result[1], stderr='mock-error')
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(result), stderr='')


class IntakeTests(unittest.TestCase):
    def test_incomplete_worker_evidence_cannot_reach_duplicate_dispatch(self):
        m = load()
        selector_spec = importlib.util.spec_from_file_location(
            'intake_selector', SRC.with_name('engineering_24x7_selector.py'))
        selector = importlib.util.module_from_spec(selector_spec)
        selector_spec.loader.exec_module(selector)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'runs.json'
            dest.write_text('[{"sentinel":true}]')
            payload = {'total_count': 1, 'workflow_runs': [{'id': 23}]}
            with self.assertRaises(m.IntakeFailure):
                m.collect(repo='gregoryharper84-ship-it/WOW-Dashboard', runs=dest,
                          worker=m.WORKERS[0], runner=FakeRun([payload] * 3), sleep=lambda _: None)
                decision = selector.plan(
                    [{'number': 23, 'state': 'open', 'labels': ['P0']}], [],
                    runs=json.loads(dest.read_text()))
                self.fail(f'incomplete evidence reached selection: {decision}')
            self.assertEqual(json.loads(dest.read_text()), [{'sentinel': True}])

    def test_missing_worker_ownership_fields_are_rejected(self):
        for field in ('display_title', 'status', 'created_at', 'updated_at', 'conclusion'):
            with self.subTest(field=field):
                m = load()
                item = worker(23)
                del item[field]
                payload = {'total_count': 1, 'workflow_runs': [item]}
                with self.assertRaises(m.IntakeFailure):
                    m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs', '/runs',
                                  runner=FakeRun([payload] * 3), sleep=lambda _: None)

    def test_invalid_worker_ownership_fields_are_rejected(self):
        for field, value in (
            ('id', True), ('display_title', ''), ('display_title', 23),
            ('status', None), ('status', 'unknown'), ('created_at', 'invalid'),
            ('created_at', '2026-11-16T00:00:00'), ('updated_at', None),
            ('updated_at', '2026-11-15T00:00:00Z'), ('conclusion', None),
            ('conclusion', 'unknown'),
        ):
            with self.subTest(field=field, value=value):
                m = load()
                item = {**worker(23), field: value}
                payload = {'total_count': 1, 'workflow_runs': [item]}
                with self.assertRaises(m.IntakeFailure):
                    m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs', '/runs',
                                  runner=FakeRun([payload] * 3), sleep=lambda _: None)

    def test_active_worker_with_null_conclusion_is_valid(self):
        m = load()
        item = {**worker(23), 'status': 'in_progress', 'conclusion': None}
        result = m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs', '/runs',
                              runner=FakeRun([{'total_count': 1, 'workflow_runs': [item]}]),
                              sleep=lambda _: None)
        self.assertEqual(result[0]['status'], 'in_progress')

    def test_moving_page_cannot_hide_an_open_pr_or_issue(self):
        for kind in ('prs', 'issues'):
            with self.subTest(kind=kind):
                m = load()
                # Row 4 closes after page one, shifting still-open row 2 out
                # of the first enumeration. The second enumeration exposes it.
                run = FakeRun([[row(4), row(3)], [row(1)],
                               [row(3), row(2)], [row(1)]])
                with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_INVENTORY_UNSTABLE'):
                    m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', kind, '/items',
                                  runner=run, sleep=lambda _: None, page_size=2)

    def test_same_ids_with_changed_ownership_evidence_are_held(self):
        m = load()
        run = FakeRun([[{**row(1), 'body': 'Incident: #23'}],
                       [{**row(1), 'body': 'Incident: #24'}]])
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_INVENTORY_UNSTABLE'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'prs', '/pulls',
                          runner=run, sleep=lambda _: None)

    def test_reordered_identical_inventory_is_complete(self):
        m = load()
        result = m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'prs', '/pulls',
                              runner=FakeRun([[row(1), row(2)], [row(2), row(1)]]),
                              sleep=lambda _: None)
        self.assertEqual(result, [row(1), row(2)])

    def test_duplicate_run_id_across_providers_is_rejected(self):
        m = load()
        payload = {'total_count': 1, 'workflow_runs': [worker(23)]}
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'runs.json'
            dest.write_text('[]')
            with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_DUPLICATE_ID'):
                m.collect(repo='gregoryharper84-ship-it/WOW-Dashboard', runs=dest,
                          runner=FakeRun([payload, payload]), sleep=lambda _: None)
            self.assertEqual(dest.read_text(), '[]')

    def test_duplicate_worker_id_across_pages_is_rejected(self):
        m = load()
        run = FakeRun([{'total_count': 3, 'workflow_runs': [worker(1), worker(2)]},
                       {'total_count': 3, 'workflow_runs': [worker(2)]}])
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_DUPLICATE_ID'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs', '/runs',
                          runner=run, sleep=lambda _: None, page_size=2)

    def test_worker_total_drift_across_pages_is_rejected(self):
        m = load()
        run = FakeRun([{'total_count': 3, 'workflow_runs': [worker(1), worker(2)]},
                       {'total_count': 2, 'workflow_runs': [worker(3)]}])
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_TOTAL_MISMATCH'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs', '/runs',
                          runner=run, sleep=lambda _: None, page_size=2)

    def test_api_error_in_later_inventory_preserves_every_output(self):
        m = load()
        with tempfile.TemporaryDirectory() as d:
            issues, prs = Path(d) / 'issues.json', Path(d) / 'prs.json'
            issues.write_text('[{"old":"issues"}]')
            prs.write_text('[{"old":"prs"}]')
            run = FakeRun([[row(1)], [row(1)], (1, ''), (1, ''), (1, '')])
            with self.assertRaises(m.IntakeFailure):
                m.collect(repo='gregoryharper84-ship-it/WOW-Dashboard', issues=issues, prs=prs,
                          runner=run, sleep=lambda _: None)
            self.assertEqual(json.loads(issues.read_text()), [{'old': 'issues'}])
            self.assertEqual(json.loads(prs.read_text()), [{'old': 'prs'}])

    def test_failed_atomic_replace_preserves_previous_file_and_cleans_temp(self):
        m = load()
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'runs.json'
            dest.write_text('[]')
            with patch.object(m.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    m._write_json_atomic(dest, [worker(23)])
            self.assertEqual(dest.read_text(), '[]')
            self.assertEqual(list(Path(d).iterdir()), [dest])

    def test_truncated_first_page_is_retried_and_complete(self):
        m = load()
        run = FakeRun([(0, '[{"number":'), [row(1)], [row(1)]])
        items = m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'issues', '/issues?state=open', runner=run,
                              sleep=lambda _: None, page_size=50)
        self.assertEqual(items, [row(1)])
        self.assertEqual(len(run.calls), 3)
        self.assertIn('page=1', run.calls[0][2])

    def test_failed_second_page_does_not_return_first_page(self):
        m = load()
        run = FakeRun([[row(i) for i in range(1, 51)], (1, ''), (1, ''), (1, '')])
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_API_FAILURE.*issues.*page=2'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'issues', '/issues?state=open', runner=run,
                          sleep=lambda _: None, page_size=50)
        self.assertEqual(len(run.calls), 4)

    def test_malformed_json_exhausted_is_explicit_failure(self):
        m = load()
        run = FakeRun([(0, ''), (0, ''), (0, '')])
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_JSON_INVALID'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'issues', '/issues?state=open', runner=run,
                          sleep=lambda _: None, page_size=50)

    def test_duplicate_cross_page_identity_is_held(self):
        m = load()
        run = FakeRun([[row(i) for i in range(1, 51)], [row(50)]])
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_DUPLICATE_ID'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'issues', '/issues?state=open', runner=run,
                          sleep=lambda _: None, page_size=50)

    def test_worker_1000_cap_fails_closed_even_if_page_short(self):
        m = load()
        run = FakeRun([{'total_count': 1000, 'workflow_runs': [worker(1)]}])
        with self.assertRaisesRegex(m.IntakeFailure, 'WORKER_RUN_HISTORY_TRUNCATED'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs', '/actions/workflows/a.yml/runs', runner=run,
                          sleep=lambda _: None, page_size=50)

    def test_workflow_runs_mapped_and_validated(self):
        m = load()
        run = FakeRun([{'total_count': 1, 'workflow_runs': [worker(23)]}])
        items = m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs', '/actions/workflows/a.yml/runs', runner=run,
                              sleep=lambda _: None, page_size=50)
        self.assertEqual(items[0]['databaseId'], 23)
        self.assertEqual(items[0]['displayTitle'], 'worker incident=23')
        self.assertEqual(items[0]['updatedAt'], '2026-11-16T00:01:00Z')

    def test_issue_pr_rows_are_filtered_after_validated_raw_paging(self):
        m = load()
        issue_pr = {'number': 2, 'pull_request': {'url': 'example'}}
        run = FakeRun([[row(1), issue_pr], [row(1), issue_pr]])
        self.assertEqual(m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'issues', '/issues?state=open',
                                       runner=run, sleep=lambda _: None), [row(1)])

    def test_shape_error_fails_closed_not_empty_success(self):
        m = load()
        run = FakeRun([{'error': 'not a list'}] * 3)
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_JSON_INVALID'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'prs', '/pulls?state=open',
                          runner=run, sleep=lambda _: None)

    def test_atomic_output_preserves_previous_file_on_error(self):
        m = load()
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'runs.json'
            dest.write_text('[{"sentinel":true}]')
            with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_JSON_INVALID'):
                m.collect(repo='gregoryharper84-ship-it/WOW-Dashboard', runs=dest, runner=FakeRun([(0, '')] * 3),
                          sleep=lambda _: None)
            self.assertEqual(json.loads(dest.read_text()), [{'sentinel': True}])

    def test_two_providers_join_without_losing_identity(self):
        m = load()
        data = [ {'total_count': 1, 'workflow_runs': [worker(1)]},
                 {'total_count': 1, 'workflow_runs': [worker(2)]} ]
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'runs.json'
            m.collect(repo='gregoryharper84-ship-it/WOW-Dashboard', runs=dest, runner=FakeRun(data),
                      sleep=lambda _: None)
            rows = json.loads(dest.read_text())
            self.assertEqual([x['databaseId'] for x in rows], [1, 2])

    def test_worker_total_count_mismatch_fails_closed(self):
        m = load()
        run = FakeRun([{'total_count': 2, 'workflow_runs': [worker(1)]}])
        with self.assertRaisesRegex(m.IntakeFailure, 'ENGINEERING_INTAKE_TOTAL_MISMATCH'):
            m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs',
                          '/actions/workflows/a.yml/runs', runner=run, sleep=lambda _: None)

    def test_empty_worker_history_is_valid(self):
        m = load()
        run = FakeRun([{'total_count': 0, 'workflow_runs': []}])
        self.assertEqual(m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'runs',
                           '/actions/workflows/a.yml/runs', runner=run, sleep=lambda _: None), [])

    def test_no_sensitive_response_text_in_logs(self):
        m = load()
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(m.IntakeFailure):
                m.fetch_pages('gregoryharper84-ship-it/WOW-Dashboard', 'issues', '/issues?state=open',
                              runner=FakeRun([(1, 'TOKEN_SECRET')]*3), sleep=lambda _: None)
        self.assertNotIn('TOKEN_SECRET', err.getvalue())


if __name__ == '__main__':
    unittest.main()
