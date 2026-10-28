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


# Real extraction contract; static string checks alone would not expose the
# formerly incompatible "Incident: #1688" and "Incident: `1688`" formats.
import pytest
from v17.engineering_incident_identity import exact_incident_id

@pytest.mark.parametrize("body", [
    "Incident: #1688", "Incident: `1688`", "Incident: 1688",
    "## P0 repair\nIncident: #1688\nChange-Class: B",
    "## Historical PR\nIncident: `1688`\nSafety: false",
])
def test_repair_ingress_accepts_all_valid_incident_formats(body):
    assert exact_incident_id(body) == "1688"

@pytest.mark.parametrize("body", [
    "", "Incident: #0", "Incident: #01688", "Incident: `#1688`",
    "Incident: #1688\nIncident: `823`", "Incident: 1688\nIncident: 1688",
    "Incident: #1688 injected", "Incident: 1688 #823",
    "Incidents: #1688", "Incident: #1688\rgarbage",
])
def test_repair_ingress_rejects_ambiguous_or_malformed_incident(body):
    with pytest.raises(ValueError):
        exact_incident_id(body)

def test_both_recovery_workflows_use_identical_strict_parser():
    auto = AUTO.read_text()
    claude = CLAUDE.read_text()
    executable = "python artifacts/wow-engine/v17/engineering_incident_identity.py"
    assert executable in auto and executable in claude
    assert "REPAIR_INCIDENT_MISSING_OR_AMBIGUOUS" in auto
    assert "CLAUDE_REPAIR_INCIDENT_MISSING_OR_AMBIGUOUS" in claude
    assert '"$incident" = "$TARGET_INCIDENT"' in claude


def test_claude_ambient_token_cannot_mutate_source_or_pull_requests():
    """Only the established Engineering App token can publish source changes.

    Legacy issue-comment bookkeeping is App-authenticated but still awaits
    canonical transactional fencing. This check prevents accidentally
    restoring a second ambient GitHub writer.
    """
    worker = CLAUDE.read_text()
    permissions = worker.split("\npermissions:\n", 1)[1].split("\nconcurrency:", 1)[0]
    assert "  contents: read\n" in permissions
    assert "  pull-requests: read\n" in permissions
    assert "  issues: read\n" in permissions
    assert "  contents: write\n" not in permissions
    assert "  pull-requests: write\n" not in permissions
    assert "  issues: write\n" not in permissions
    assert "GITHUB_TOKEN: ${{ steps.eng_token.outputs.token }}" in worker
    assert "GH_TOKEN: ${{ steps.eng_token.outputs.token }}" in worker
    assert 'git push --force-with-lease="refs/heads/$branch:$REPAIR_SHA"' in worker
    assert 'git push --set-upstream origin "$branch"' in worker

    # Existing comment operations must also use the trusted App identity.
    for name in (
        "Initialize persistent-worker safety state",
        "Persist dual-stream heartbeat and receipt",
        "Append incident delivery receipt",
    ):
        block = worker.split("      - name: " + name, 1)[1].split("\n      - name:", 1)[0]
        assert "GH_TOKEN: ${{ steps.eng_token.outputs.token }}" in block
        assert "GH_TOKEN: ${{ github.token }}" not in block


def test_provider_and_handoff_workflows_have_no_ambient_repository_write_authority():
    """Prevents a bypass credential from returning on any provider route."""
    workflows = (
        CLAUDE,
        ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
        ROOT / ".github/workflows/wow-v17-gemini-engineering-worker.yml",
        ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        ROOT / ".github/workflows/wow-v17-cursor-pr-handoff-bridge.yml",
    )
    for path in workflows:
        source = path.read_text()
        permissions = source.split("\npermissions:\n", 1)[1].split("\nconcurrency:", 1)[0]
        assert "  contents: read\n" in permissions, path
        assert "  pull-requests: read\n" in permissions, path
        assert "  issues: read\n" in permissions, path
        assert "  contents: write\n" not in permissions, path
        assert "  pull-requests: write\n" not in permissions, path
        assert "  issues: write\n" not in permissions, path
