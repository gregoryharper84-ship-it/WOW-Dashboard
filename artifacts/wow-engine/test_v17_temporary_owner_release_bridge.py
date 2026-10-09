from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-temporary-owner-release-bridge.yml"


def test_temporary_owner_bridge_is_manual_exact_sha_merge_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert "workflow_dispatch" in doc[True]
    assert "WOW_OWNER_RELEASE_APPROVAL" in text
    assert 'expected_approval="${PR_NUMBER}:${EXPECTED_HEAD_SHA}"' in text
    assert "OWNER_BRIDGE_EXACT_SHA_NOT_AUTHORIZED" in text
    assert "OWNER_BRIDGE_HEAD_CHANGED_AFTER_REVIEW" in text
    assert "-f sha=\"$EXPECTED_HEAD_SHA\"" in text
    assert "production_acceptance=false" in text


def test_temporary_owner_bridge_preserves_independent_review_class_c_hold_and_trust_root_denial():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "wow-claude-agent" in text
    assert 'permission_profile: ":read-only"' in text
    assert '"enum":["A","B","C"]' in text
    assert "OWNER_BRIDGE_CLASS_C_DENIED" in text
    assert "OWNER_BRIDGE_INDEPENDENT_QA_HOLD" in text
    assert "OWNER_BRIDGE_TRUST_ROOT_CHANGE_DENIED" in text


def test_temporary_owner_bridge_requires_all_exact_head_ci_and_v17_invariants():
    text = WORKFLOW.read_text(encoding="utf-8")
    for name in (
        "wow-verify",
        "wow-engine-verify",
        "wow-v17-rapid-repair",
        "wow-v17-change-impact-gate",
        "wow-v17-engineering-auditor-code-health",
        "wow-v17-spread-forward-shadow",
        "wow-v17-release-production-verification-agent",
    ):
        assert name in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "can_execute=false" in text
    assert "DRY_RUN_ONLY" in text


WOW_VERIFY = ROOT / ".github/workflows/wow-verify.yml"


def test_wow_verify_disables_import_time_production_daemons_in_ci():
    text = WOW_VERIFY.read_text(encoding="utf-8")
    assert 'WOW_CI: "1"' in text
    assert 'WNBA_DISABLE_CRON: "1"' in text
    assert 'LLP_DISABLE_SNAPSHOT_CRON: "1"' in text
    assert 'SETTLEMENT_WORKER_DISABLED: "1"' in text


def test_temporary_owner_bridge_accepts_only_owner_exact_sha_comment_trigger():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "issue_comment:" in text
    assert "github.actor == 'gregoryharper84-ship-it'" in text
    assert "startsWith(github.event.comment.body, '/wow-owner-bridge ')" in text
    assert 'authorization_mode="OWNER_EXACT_SHA_COMMENT"' in text
    assert "OWNER_BRIDGE_COMMENT_FORMAT_INVALID" in text
    assert "OWNER_BRIDGE_ACTOR_NOT_OWNER" in text


def test_temporary_owner_bridge_fences_current_main_and_review_drift():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "OWNER_BRIDGE_HEAD_BEHIND_MAIN" in text
    assert "BASE_MAIN_SHA=$MAIN_SHA" in text
    assert "OWNER_BRIDGE_MAIN_MOVED_DURING_REVIEW" in text
    assert "/compare/${MAIN_SHA}...${EXPECTED_HEAD_SHA}" in text
