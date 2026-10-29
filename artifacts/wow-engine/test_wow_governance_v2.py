"""Run Governance V2 source tests in real required backend CI shards.

The protected-main reviewer checks out main. That is a runtime safety feature,
but its self-test cannot validate unmerged PR source. This existing backend
pytest shard is a separate required-CI gate on the PR head itself.
"""
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".github/scripts/wow_simple_security_review.py"
WORKFLOW = REPO / ".github/workflows/wow-v17-simple-security-review.yml"
TEST_DIR = REPO / ".github/scripts/tests"


def test_source_review_negative_suite_runs_on_pr_head():
    assert SCRIPT.is_file() and WORKFLOW.is_file()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(TEST_DIR),
         "-p", "test_wow_simple_security_review.py", "-v"],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    # Verify complete negative suite without brittle exact-count gating.
    # Named security regressions cannot silently disappear on future additions.
    import re
    count = re.search(r"Ran (\d+) tests?", result.stderr)
    assert count is not None and int(count.group(1)) >= 36, result.stderr
    for name in (
        "test_global_reject_prevents_second_pr_review_without_provider_call",
        "test_provider_reject_persists_even_if_pr_moves_before_reread",
        "test_unclassified_http_holds_identify_safe_endpoint_without_provider_switch",
        "test_in_progress_reservation_fail_closed_when_reviewer_crashes",
        "test_reject_ledger_committed_before_head_check_and_failed_commit_holds",
        "test_reject_content_ledger_is_not_duplicated_by_same_sha_page_limit",
        "test_content_hash_is_stable_across_pr_sha_and_base_not_file_bytes",
        "test_review_client_denies_patch_to_anything_except_qa_check_run",
    ):
        assert name in result.stderr, result.stderr


def test_protected_main_workflow_and_separate_qa_app_are_required():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    code = SCRIPT.read_text(encoding="utf-8")
    assert "pull_request_target:" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "environment: wow-qa" in workflow
    assert "ref: main" in workflow
    assert "persist-credentials: false" in workflow
    assert "permission-checks: write" in workflow
    assert "steps.qa.outputs.token" in workflow
    assert "github.event.pull_request.head.ref" not in workflow
    assert "NoCredentialRedirect" in code
    assert "HTTP_CLIENT.open" in code
    assert "except ProviderUnavailable" in code
    assert "except OwnerFallbackRequired" in code
    assert "OWNER_IS_SOURCE_AUTHOR_OR_IDENTITY_UNVERIFIED" in code
    assert "OWNER_ADOPTION_COMMIT_PROVENANCE_UNVERIFIED" in code
    assert "except Hold as exc" in code
    assert 'CHECK_NAME = "WOW Independent Security Review"' in code
