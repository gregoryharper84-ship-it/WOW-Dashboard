from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# The source tree is artifacts/wow-engine (hyphenated), not an importable
# artifacts.wow_engine namespace. Load the co-located module without changing
# application imports, the writer lease, or any protected workflow.
_receipt_path = Path(__file__).with_name("cursor_terminal_status_receipt.py")
_spec = importlib.util.spec_from_file_location("cursor_terminal_status_receipt", _receipt_path)
assert _spec is not None and _spec.loader is not None
receipt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(receipt)


def _responses(*, run_status="FINISHED", latest_run_id=None, head_sha=None,
               files=None, commit_sha=None, agent_status="IDLE", run_id=None):
    pr_url = (
        f"{receipt.GITHUB_API}/repos/{receipt.REPOSITORY}/pulls/"
        f"{receipt.PR_NUMBER}"
    )
    files_url = f"{pr_url}/files?per_page=100&page=1"
    commits_url = f"{pr_url}/commits?per_page=100&page=1"
    return {
        f"{receipt.CURSOR_API}/agents/{receipt.AGENT_ID}": {
            "id": receipt.AGENT_ID,
            "latestRunId": latest_run_id or receipt.RUN_ID,
            "status": agent_status,
        },
        f"{receipt.CURSOR_API}/agents/{receipt.AGENT_ID}/runs/{receipt.RUN_ID}": {
            "id": run_id or receipt.RUN_ID,
            "agentId": receipt.AGENT_ID,
            "status": run_status,
            "result": "completed",
        },
        pr_url: {
            "number": receipt.PR_NUMBER,
            "state": "open",
            "changed_files": len(receipt.EXPECTED_FILES),
            "head": {
                "sha": head_sha or receipt.EXPECTED_HEAD,
                "repo": {"full_name": receipt.REPOSITORY},
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": receipt.REPOSITORY},
            },
        },
        files_url: (
            [{"filename": name} for name in sorted(receipt.EXPECTED_FILES)]
            if files is None else files
        ),
        commits_url: [{"sha": commit_sha or receipt.EXPECTED_HEAD}],
    }


def _collect(responses):
    seen = []

    def api(url, *, token, basic=False):
        seen.append((url, token, basic))
        return responses[url]

    result = receipt.collect_receipt("cursor-secret", "github-token", api=api)
    return result, seen


def test_collects_run_specific_terminal_receipt_and_exact_pr_changes():
    result, requests = _collect(_responses())

    assert result["status"] == "CURSOR_TERMINAL_STATUS_VERIFIED"
    assert result["terminal_status"] == "FINISHED"
    assert result["latest_run_id"] == receipt.RUN_ID
    assert result["head_sha"] == receipt.EXPECTED_HEAD
    assert result["changed_files"] == sorted(receipt.EXPECTED_FILES)
    assert result["lease_released"] is False
    assert result["can_execute"] is False
    assert len(requests) == 5
    assert all(url.startswith((receipt.CURSOR_API, receipt.GITHUB_API))
               for url, _, _ in requests)
    assert [basic for _, _, basic in requests] == [True, True, False, False, False]


@pytest.mark.parametrize("status", ["ERROR", "CANCELLED", "EXPIRED"])
def test_non_success_terminal_states_still_prove_only_run_termination(status):
    result, _ = _collect(_responses(run_status=status))

    assert result["status"] == "CURSOR_TERMINAL_STATUS_VERIFIED"
    assert result["terminal_status"] == status
    assert result["lease_released"] is False


def test_idle_agent_does_not_substitute_for_nonterminal_run_status():
    with pytest.raises(receipt.ReceiptBlocked, match="CURSOR_TERMINAL_STATUS_UNVERIFIED"):
        _collect(_responses(run_status="RUNNING", agent_status="IDLE"))


def test_active_agent_blocks_even_when_target_run_claims_terminal():
    with pytest.raises(receipt.ReceiptBlocked, match="CURSOR_AGENT_STILL_ACTIVE"):
        _collect(_responses(agent_status="ACTIVE"))


def test_latest_run_pointer_must_match_target_run():
    with pytest.raises(receipt.ReceiptBlocked, match="CURSOR_LATEST_RUN_MISMATCH"):
        _collect(_responses(latest_run_id="run-some-other-id"))


def test_run_identity_must_match_target_run():
    with pytest.raises(receipt.ReceiptBlocked, match="CURSOR_RUN_ID_MISMATCH"):
        _collect(_responses(run_id="run-some-other-id"))


@pytest.mark.parametrize(
    ("override", "expected_code"),
    [
        ({"head_sha": "a" * 40}, "SOURCE_PR_HEAD_OR_BASE_UNVERIFIED"),
        ({"files": [{"filename": "unexpected.py"}]}, "SOURCE_PR_FILES_MISMATCH"),
        ({"commit_sha": "a" * 40}, "SOURCE_PR_COMMIT_MISMATCH"),
    ],
)
def test_source_pr_head_and_changes_are_bound_fail_closed(override, expected_code):
    with pytest.raises(receipt.ReceiptBlocked, match=expected_code):
        _collect(_responses(**override))


def test_credentials_are_required_without_any_api_call():
    def unexpected_call(*args, **kwargs):
        pytest.fail("API must not be called without credentials")

    with pytest.raises(receipt.ReceiptBlocked, match="CURSOR_API_KEY_MISSING"):
        receipt.collect_receipt("", "github-token", api=unexpected_call)
    with pytest.raises(receipt.ReceiptBlocked, match="GITHUB_READ_TOKEN_MISSING"):
        receipt.collect_receipt("cursor-secret", "", api=unexpected_call)
