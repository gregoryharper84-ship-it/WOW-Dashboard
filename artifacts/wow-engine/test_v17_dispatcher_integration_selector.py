"""Workflow contract for the flag-gated integration selector (#2).

The dispatcher is the only automatic engineering entrypoint, so a wiring change
must not disturb provider routing, the single-writer lease, or any existing gate.
These assertions pin the wiring to be additive and reversible.
"""
from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
)


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text()


def test_selector_is_an_explicit_flag_with_safe_default(text):
    assert "      integration_selector:\n" in text
    assert 'default: "off"' in text
    assert "          - \"off\"\n          - report\n          - dispatch\n" in text
    assert "RESERVATION_ENABLED: ${{ vars.WOW_INTEGRATION_RESERVATION_ENABLED }}" in text


def test_selector_step_is_advisory_and_read_only(text):
    assert "- name: Select integration reservation" in text
    assert "        id: integration\n" in text
    assert "        continue-on-error: true\n" in text
    assert "python artifacts/wow-engine/v17/pr_integration_reservation.py select" in text
    assert "--mode off --output" in text
    # A selector failure must never block routing: no gate may depend on
    # steps.integration.outcome.
    assert "steps.integration.outcome" not in text
    # Read-only permissions are preserved.
    assert "  pull-requests: read\n" in text
    assert "pull-requests: write" not in text


def test_existing_dispatch_gates_are_preserved_and_narrowed(text):
    assert (
        "if: steps.route.outputs.action == 'DISPATCH' && "
        "steps.route.outputs.provider == 'openai' && "
        "steps.remote_fence.outcome == 'success' && "
        "steps.integration.outputs.dispatch_repair != 'true'"
    ) in text
    assert (
        "if: steps.route.outputs.action == 'DISPATCH' && "
        "steps.route.outputs.provider == 'anthropic' && "
        "steps.remote_fence.outcome == 'success' && "
        "steps.integration.outputs.dispatch_repair != 'true'"
    ) in text
    # Gemini, Cursor and survival lanes keep their original gates.
    assert (
        "if: steps.route.outputs.action == 'DISPATCH' && "
        "steps.route.outputs.provider == 'gemini' && "
        "steps.remote_fence.outcome == 'success'"
    ) in text
    assert "OWNER_CURSOR_PR_HANDOFF" in text


def test_repair_dispatch_uses_the_workers_existing_exact_head_inputs(text):
    assert "- name: Dispatch reserved integration repair" in text
    assert "        id: dispatch_repair\n" in text
    assert (
        "if: steps.integration.outputs.dispatch_repair == 'true' && "
        "steps.route.outputs.action == 'DISPATCH' && "
        "steps.remote_fence.outcome == 'success'"
    ) in text
    assert '-f repair_pr="$REPAIR_PR"' in text
    assert '-f repair_head_sha="$REPAIR_HEAD"' in text
    assert "wow-v17-chatgpt-engineering-worker.yml" in text
    assert "wow-v17-claude-engineering-worker.yml" in text
    assert "INTEGRATION_REPAIR_PROVIDER_UNSUPPORTED" in text
    assert '[[ "$REPAIR_HEAD" =~ ^[0-9a-f]{40}$ ]]' in text


def test_dispatch_is_repair_only_and_never_duplicates_keep_current(text):
    """keep-current already updates behind-main PRs with an App token."""
    assert "wow-v17-engineering-pr-keep-current" in text
    assert "merely to get a branch current" in text
    # The comment must say the sweep is bot-author specific, not "behind main".
    assert "wow-engineering[bot]" in text
    assert "no automatic" in text
    assert 'echo "refresh_owner_reason=$(jq -r \'.refresh_owner_reason // ""\' "$receipt")"' in text
    assert "BEHIND|DIRTY)" in text
    assert 'echo "requires_code_repair=$(jq -r \'.requires_code_repair // false\' "$receipt")"' in text
    assert 'echo "refresh_owner=$(jq -r \'.refresh_owner // ""\' "$receipt")"' in text
    assert ".refresh_owner == null or (.refresh_owner | type == \"string\")" in text
    assert "- integration_refresh_is_never_paid_worker_work: true" in text


def test_unknown_merge_state_is_unattested_and_cannot_select(text):
    # BEHIND needs an explicit compare; a lazy/absent merge state is not proof
    # the branch is current.
    assert 'case "$merge_state" in' in text
    assert 'CLEAN|BLOCKED|UNSTABLE|DRAFT|HAS_HOOKS)' in text
    assert 'BEHIND|DIRTY)' in text
    assert "compare/$head...main" in text
    assert "*)\n                  behind=null" in text


def test_receipt_validation_extends_without_weakening(text):
    assert "PROVIDER_ROUTE_RECEIPT_INVALID" in text
    assert "REMOTE_WRITER_RECONCILIATION_REQUIRED" in text
    assert "INTEGRATION_RESERVATION_RECEIPT_INVALID" in text
    assert "INTEGRATION_REPAIR_DISPATCH_FAILED" in text
    assert 'and .can_execute == false' in text
    assert 'and .terminal_authority == "V17_TERMINAL_REDUCER"' in text
    assert '$INTEGRATION_STATUS" != "DISABLED"' in text


def test_selector_receipt_is_archived_and_surfaced(text):
    assert "${{ runner.temp }}/integration-reservation.json" in text
    assert "      integration_reservation_status: ${{ steps.integration.outputs.status }}" in text
    assert "- integration_selector_never_merges: true" in text


def test_single_writer_lease_and_no_merge_capability(text):
    assert text.count("group: wow-v17-engineering-implementation-single-writer") == 1
    assert "cancel-in-progress: false" in text
    assert "gh pr merge" not in text
    assert "update-branch" not in text
    assert "  contents: read\n" in text
