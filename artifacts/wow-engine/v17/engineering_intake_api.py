"""Fail-closed, bounded GitHub intake for the existing WOW engineering controller.

No dispatch, secrets, review decisions, release gates, or model execution here.
Each API page is individually validated; no partial inventory is published.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

PAGE_SIZE = 50
ATTEMPTS = 3
LIMITS = {'issues': 5000, 'prs': 2000, 'runs': 999}
WORKERS = (
    'wow-v17-chatgpt-engineering-worker.yml',
    'wow-v17-claude-engineering-worker.yml',
)


class IntakeFailure(RuntimeError):
    """Trust boundary rejected an incomplete GitHub inventory."""


def _rows(payload, kind):
    if kind == 'runs':
        if not isinstance(payload, dict) or not isinstance(payload.get('workflow_runs'), list):
            raise ValueError('missing workflow_runs array')
        total = payload.get('total_count')
        if type(total) is not int or total < 0:
            raise ValueError('missing valid total_count')
        if total >= 1000:
            raise IntakeFailure('WORKER_RUN_HISTORY_TRUNCATED: provider total_count>=1000')
        raw = payload['workflow_runs']
    elif isinstance(payload, list):
        raw = payload
    else:
        raise ValueError('missing collection array')

    for item in raw:
        ident = item.get('id') if kind == 'runs' and isinstance(item, dict) else (
            item.get('number') if isinstance(item, dict) else None)
        if type(ident) is not int or ident < 1:
            raise ValueError('missing row identity')
        if kind != 'runs':
            if item.get('state') != 'open' or 'body' not in item:
                raise ValueError('incomplete row ownership fields')
            if item['body'] is not None and not isinstance(item['body'], str):
                raise ValueError('invalid row ownership body')
        if kind == 'runs':
            title = item.get('display_title')
            if not isinstance(title, str) or not title.strip():
                raise ValueError('missing worker ownership title')
            status = item.get('status')
            if status not in {'queued', 'in_progress', 'completed', 'waiting', 'pending', 'requested'}:
                raise ValueError('missing or unknown worker status')
            stamps = []
            for field in ('created_at', 'updated_at'):
                value = item.get(field)
                if not isinstance(value, str):
                    raise ValueError('missing worker timestamp')
                stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if stamp.tzinfo is None or stamp.utcoffset() is None:
                    raise ValueError('worker timestamp must include timezone')
                stamps.append(stamp)
            if stamps[1] < stamps[0]:
                raise ValueError('worker completion precedes creation')
            if 'conclusion' not in item:
                raise ValueError('missing worker conclusion')
            conclusion = item['conclusion']
            if status == 'completed':
                if conclusion not in {'success', 'failure', 'neutral', 'cancelled', 'skipped',
                                      'timed_out', 'action_required', 'stale', 'startup_failure'}:
                    raise ValueError('missing or unknown completed worker conclusion')
            elif conclusion is not None:
                raise ValueError('active worker cannot have a conclusion')

    if kind == 'runs':
        return raw, [
            {'displayTitle': item.get('display_title'), 'createdAt': item.get('created_at'),
             'updatedAt': item.get('updated_at'), 'status': item.get('status'),
             'conclusion': item.get('conclusion'), 'databaseId': item['id']}
            for item in raw
        ]
    if kind == 'issues':
        return raw, [item for item in raw if item.get('pull_request') is None]
    return raw, raw


def _fetch_enumeration(repo, kind, endpoint, *, runner, sleep, page_size):
    """Validate a bounded enumeration; absence still needs reconciliation."""
    if kind not in LIMITS or not (1 <= page_size <= 100):
        raise ValueError('unrecognized intake kind or page size')
    if repo != 'gregoryharper84-ship-it/WOW-Dashboard':
        raise IntakeFailure('ENGINEERING_INTAKE_REPOSITORY_UNTRUSTED')

    all_items = []
    seen = set()
    page = 1
    expected_total = None
    while True:
        if (page - 1) * page_size >= LIMITS[kind]:
            raise IntakeFailure(f'ENGINEERING_INTAKE_LIMIT_EXCEEDED {kind} page={page}')
        url = f'/repos/{repo}{endpoint}{"&" if "?" in endpoint else "?"}per_page={page_size}&page={page}'
        page_items = None
        raw_count = None
        for attempt in range(1, ATTEMPTS + 1):
            print(f'::notice::ENGINEERING_INTAKE_FETCH {kind} page={page} attempt={attempt}', file=sys.stderr)
            try:
                call = runner(['gh', 'api', url], capture_output=True, text=True, check=False)
                if call.returncode != 0:
                    code = 'ENGINEERING_INTAKE_API_FAILURE'
                    raise ValueError('nonzero GitHub CLI exit')
                try:
                    payload = json.loads(call.stdout)
                    raw, page_items = _rows(payload, kind)
                except (ValueError, TypeError) as exc:
                    code = 'ENGINEERING_INTAKE_JSON_INVALID'
                    raise ValueError('invalid or incomplete GitHub page') from exc
                raw_count = len(raw)
                if kind == 'runs':
                    observed_total = payload['total_count']
                    if expected_total is not None and expected_total != observed_total:
                        raise IntakeFailure(f'ENGINEERING_INTAKE_TOTAL_MISMATCH {kind} page={page}')
                    expected_total = observed_total
                break
            except (OSError, ValueError) as exc:
                if isinstance(exc, OSError):
                    code = 'ENGINEERING_INTAKE_API_FAILURE'
                print(f'::warning::{code} {kind} page={page} attempt={attempt}', file=sys.stderr)
                if attempt == ATTEMPTS:
                    raise IntakeFailure(f'{code} {kind} page={page} attempts={ATTEMPTS}') from exc
                sleep(0.25 * attempt)
        assert page_items is not None and raw_count is not None
        for item in page_items:
            ident = item['databaseId'] if kind == 'runs' else item['number']
            if ident in seen:
                raise IntakeFailure(f'ENGINEERING_INTAKE_DUPLICATE_ID {kind} page={page}')
            seen.add(ident)
        all_items.extend(page_items)
        if len(all_items) > LIMITS[kind]:
            code = 'WORKER_RUN_HISTORY_TRUNCATED' if kind == 'runs' else 'ENGINEERING_INTAKE_LIMIT_EXCEEDED'
            raise IntakeFailure(f'{code} {kind} page={page}')
        print(f'::notice::ENGINEERING_INTAKE_ACCEPT {kind} page={page} rows={raw_count}', file=sys.stderr)
        if raw_count < page_size:
            if kind == 'runs' and len(all_items) != expected_total:
                raise IntakeFailure(f'ENGINEERING_INTAKE_TOTAL_MISMATCH {kind} page={page}')
            return all_items
        page += 1


def _pr_claim_signature(row):
    """Compare only decision-bearing PR metadata, not review counters.

    A new comment changes updated_at and comments without changing the
    incident claim, code SHA or owner. The returned full PR rows stay intact.
    """
    def _ref(value):
        if not isinstance(value, dict):
            return value
        repo = value.get('repo')
        repo_name = repo.get('full_name') if isinstance(repo, dict) else repo
        return (value.get('ref'), value.get('sha'), repo_name)

    def _names(items, field):
        if not isinstance(items, list):
            return items
        return tuple(sorted(str(item.get(field) if isinstance(item, dict) else item)
                            for item in items))

    owner = row.get('user')
    login = owner.get('login') if isinstance(owner, dict) else owner
    return (
        row['number'], row.get('state'), row.get('body'), row.get('title'),
        row.get('draft'), login,
        _ref(row.get('head')), _ref(row.get('base')),
        _names(row.get('labels'), 'name'), _names(row.get('assignees'), 'login'),
    )


def fetch_pages(repo, kind, endpoint, *, runner=subprocess.run, sleep=time.sleep,
                page_size=PAGE_SIZE):
    """Reject issue/PR absence unless two full inventories agree."""
    first = _fetch_enumeration(repo, kind, endpoint, runner=runner, sleep=sleep,
                               page_size=page_size)
    if kind == 'runs':
        return first
    second = _fetch_enumeration(repo, kind, endpoint, runner=runner, sleep=sleep,
                                page_size=page_size)
    # Issue timestamps affect controller priority and still demand exact equality.
    # For PRs, only compare decision-bearing claim fields. Comment counters and
    # updated_at are not absent-PR evidence and must not starve all dispatches.
    if kind == 'prs':
        left = [_pr_claim_signature(row) for row in sorted(first, key=lambda row: row['number'])]
        right = [_pr_claim_signature(row) for row in sorted(second, key=lambda row: row['number'])]
    else:
        left = sorted(first, key=lambda row: row['number'])
        right = sorted(second, key=lambda row: row['number'])
    if left != right:
        raise IntakeFailure(f'ENGINEERING_INTAKE_INVENTORY_UNSTABLE {kind}')
    return first


def _write_json_atomic(target, rows):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.engineering-intake-', delete=False) as file:
            name = file.name
            json.dump(rows, file, separators=(',', ':'))
            file.write('\n')
        os.replace(name, target)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def collect(*, repo, issues=None, prs=None, runs=None, worker=None,
            runner=subprocess.run, sleep=time.sleep):
    """Fetch every requested inventory before writing any of its outputs."""
    if worker and worker not in WORKERS:
        raise IntakeFailure('ENGINEERING_INTAKE_WORKER_UNTRUSTED')
    if not (issues or prs or runs) or (worker and not runs):
        raise ValueError('request an output and attach --worker only to --runs')
    planned = {}
    if issues is not None:
        planned[Path(issues)] = fetch_pages(repo, 'issues', '/issues?state=open',
                                            runner=runner, sleep=sleep)
    if prs is not None:
        planned[Path(prs)] = fetch_pages(repo, 'prs', '/pulls?state=open&base=main',
                                         runner=runner, sleep=sleep)
    if runs is not None:
        merged = []
        seen_runs = set()
        for name in ((worker,) if worker else WORKERS):
            provider_runs = fetch_pages(repo, 'runs', f'/actions/workflows/{name}/runs',
                                         runner=runner, sleep=sleep)
            for item in provider_runs:
                if item['databaseId'] in seen_runs:
                    raise IntakeFailure('ENGINEERING_INTAKE_DUPLICATE_ID runs across providers')
                seen_runs.add(item['databaseId'])
            merged.extend(provider_runs)
        if len(merged) > 1998:
            raise IntakeFailure('WORKER_RUN_HISTORY_TRUNCATED: combined limit')
        planned[Path(runs)] = merged
    for output, rows in planned.items():
        _write_json_atomic(output, rows)
        print(f'::notice::ENGINEERING_INTAKE_OUTPUT {output.name} rows={len(rows)}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', ''))
    parser.add_argument('--issues', type=Path)
    parser.add_argument('--prs', type=Path)
    parser.add_argument('--runs', type=Path)
    parser.add_argument('--worker', choices=WORKERS)
    args = parser.parse_args()
    try:
        collect(repo=args.repo, issues=args.issues, prs=args.prs, runs=args.runs,
                worker=args.worker)
    except (IntakeFailure, ValueError) as exc:
        print(f'::error::{exc}', file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == '__main__':
    main()
