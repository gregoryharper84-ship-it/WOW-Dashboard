"""Negative-path static contract for quota-governed same-PR Claude fallback (#1688).

These tests run in the required governed backend shards. They never mint credentials,
authorize merges, modify model probabilities or execute a wager.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUTO = ROOT / '.github/workflows/wow-v17-engineering-auto-repair.yml'
CLAUDE = ROOT / '.github/workflows/wow-v17-claude-engineering-worker.yml'


def test_fallback_requires_proven_provider_failure_before_claude_dispatch():
    s = AUTO.read_text()
    assert 'engineering_provider_failover.py classify' in s
    assert 'failover_eligible' in s
    assert 'if [ "${ANTHROPIC_AVAILABLE:-false}" = "true" ]' in s
    assert 'provider=anthropic' in s
    assert 'gh workflow run wow-v17-chatgpt-engineering-worker.yml' in s


def test_both_worker_routes_preserve_exact_incident_pr_and_head():
    s = AUTO.read_text()
    a = s[s.index('if [ "$provider" = "anthropic" ]; then'):]
    assert 'gh workflow run wow-v17-claude-engineering-worker.yml' in a
    assert a.count('-f repair_pr="$PR_NUMBER" -f repair_head_sha="$HEAD_SHA"') >= 2
    assert '-f target_incident="$incident" -f lease_group="$lease_group"' in a
    assert 'DISPATCH_HOLD_SUPERSEDED_REQUIRES_RECONCILIATION' in s


def test_claude_requires_exact_bound_app_pr_and_same_head():
    s=CLAUDE.read_text()
    for item in (
        'CLAUDE_REPAIR_TARGET_INCOMPLETE_OR_INVALID',
        'CLAUDE_REPAIR_TARGET_STALE_OR_UNTRUSTED',
        'CLAUDE_REPAIR_INCIDENT_MISMATCH',
        'CLAUDE_REPAIR_HEAD_MOVED',
        '.user.login == "wow-engineering[bot]"',
        '.draft == false',
        'git switch --detach "$REPAIR_SHA"',
        '--force-with-lease="refs/heads/$branch:$REPAIR_SHA"',
    ):
        assert item in s, item


def test_claude_cannot_create_second_pr_for_targeted_repair():
    s=CLAUDE.read_text()
    assert "if: steps.impl.outputs.changed == 'true' && steps.repair_target.outputs.bound != 'true'" in s
    assert 'ACTIONABLE_REPAIR_WRONG_PR' in s
    assert 'REPAIR_PR: ${{ steps.repair_target.outputs.pr_number }}' in s
    assert 'Repair artifact: PR #' in s


def test_no_model_or_execution_authority_added():
    s=CLAUDE.read_text(); a=AUTO.read_text()
    assert 'can_execute=false' in s
    assert 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true' in s
    assert 'can_execute=false' in a
    assert 'V17_TERMINAL_REDUCER' in s
