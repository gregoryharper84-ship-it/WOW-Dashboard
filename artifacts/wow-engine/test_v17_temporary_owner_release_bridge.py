from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-temporary-owner-release-bridge.yml"


def test_temporary_owner_bridge_is_manual_exact_sha_merge_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert "workflow_dispatch" in doc[True]
    assert "WOW_OWNER_RELEASE_APPROVAL" in text
    assert '[ "$OWNER_APPROVAL" = "${pr}:${head}" ]' in text
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


def test_temporary_owner_bridge_binds_to_open_referenced_incident():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "OWNER_BRIDGE_INCIDENT_NOT_OPEN" in text
    assert "OWNER_BRIDGE_PR_INCIDENT_LINK_MISSING" in text
    assert "grep -Eq" in text


def _run_authorization_only(tmp_path, *, event_name="issue_comment", secret="", actor="gregoryharper84-ship-it", comment_sha=None):
    """Execute the real bridge bash preflight up to its first GitHub API call."""
    import os
    import subprocess

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["owner-authorization"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "authorize")
    expected_sha = "a" * 40
    request_sha = expected_sha if comment_sha is None else comment_sha
    environment = dict(os.environ)
    environment.update({
        "EVENT_NAME": event_name,
        "EVENT_ACTOR": actor,
        "EVENT_PR_NUMBER": "1575",
        "COMMENT_BODY": f"/wow-owner-bridge {request_sha} incident=1388",
        "INPUT_PR_NUMBER": "1575",
        "INPUT_HEAD_SHA": expected_sha,
        "INPUT_INCIDENT_ID": "1388",
        "OWNER_APPROVAL": secret,
        "GITHUB_ENV": str(tmp_path / "github_env"),
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
    })
    return subprocess.run(
        ["bash", "-c", script], env=environment, capture_output=True,
        text=True, check=False,
    )


def test_owner_bridge_comment_requires_exact_separate_secret(tmp_path):
    head = "a" * 40
    absent = _run_authorization_only(tmp_path, secret="")
    assert absent.returncode != 0
    assert "OWNER_BRIDGE_APPROVAL_SECRET_MISSING" in absent.stderr

    mismatch = _run_authorization_only(tmp_path, secret=f"1574:{head}")
    assert mismatch.returncode != 0
    assert "OWNER_BRIDGE_EXACT_SHA_NOT_AUTHORIZED" in mismatch.stderr

    authorized = _run_authorization_only(tmp_path, secret=f"1575:{head}")
    assert authorized.returncode == 0, authorized.stderr
    receipt = (tmp_path / "github_output").read_text()
    assert "pr_number=1575" in receipt
    assert f"head_sha={head}" in receipt
    assert "authorization_mode=OWNER_EXACT_SHA_COMMENT" in receipt


def test_owner_bridge_comment_rejects_stale_sha_or_nonowner(tmp_path):
    head = "a" * 40
    stale = _run_authorization_only(
        tmp_path, secret=f"1575:{head}", comment_sha="b" * 40,
    )
    assert stale.returncode != 0
    assert "OWNER_BRIDGE_EXACT_SHA_NOT_AUTHORIZED" in stale.stderr

    nonowner = _run_authorization_only(
        tmp_path, secret=f"1575:{head}", actor="engineering-bot",
    )
    assert nonowner.returncode != 0
    assert "OWNER_BRIDGE_ACTOR_NOT_OWNER" in nonowner.stderr


def test_owner_bridge_dispatch_path_still_requires_same_secret(tmp_path):
    head = "a" * 40
    absent = _run_authorization_only(tmp_path, event_name="workflow_dispatch")
    assert absent.returncode != 0
    assert "OWNER_BRIDGE_APPROVAL_SECRET_MISSING" in absent.stderr

    good = _run_authorization_only(
        tmp_path, event_name="workflow_dispatch", secret=f"1575:{head}",
    )
    assert good.returncode == 0, good.stderr
    assert "authorization_mode=OWNER_ENVIRONMENT_SECRET" in (
        tmp_path / "github_output"
    ).read_text()


def test_owner_bridge_denial_has_durable_run_summary_when_incident_unset():
    """An auth failure must never die in the always() reporting step."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    receipt = next(step["run"] for step in doc["jobs"]["owner-bridge"]["steps"]
                   if step.get("name") == "Publish bounded receipt")
    assert "if: always()" not in receipt  # Workflow owns the if, not bash
    assert "${INCIDENT_ID:-unresolved}" in receipt
    assert "${PR_NUMBER:-unresolved}" in receipt
    assert "${EXPECTED_HEAD_SHA:-unresolved}" in receipt
    assert '>> "$GITHUB_STEP_SUMMARY"' in receipt
    assert "OWNER_BRIDGE_RECEIPT_NO_VALIDATED_INCIDENT" in receipt
    assert 'if [[ "${safe_incident}" =~ ^[0-9]+$ ]]' in receipt
    assert "production_acceptance=false" in receipt


def test_owner_held_approval_is_readonly_and_precedes_any_model_review():
    """A shared owner actor cannot trigger Claude without the owner factor."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    auth = document["jobs"]["owner-authorization"]
    review = document["jobs"]["owner-readonly-qa"]
    release = document["jobs"]["owner-bridge"]
    secret_expr = "${{ secrets.WOW_OWNER_RELEASE_APPROVAL }}"
    assert auth["environment"] == "wow-release"
    assert auth["timeout-minutes"] <= 5
    for name in ("contents", "issues", "pull-requests"):
        assert auth["permissions"][name] == "read"
    assert "environment" not in review
    assert "environment" not in release
    assert "OWNER_APPROVAL" not in auth.get("env", {})
    secret_step = next(step for step in auth["steps"]
                       if step.get("id") == "authorize")
    assert secret_step["env"]["OWNER_APPROVAL"] == secret_expr
    assert '[ "$OWNER_APPROVAL" = "${pr}:${head}" ]' in secret_step["run"]
    assert "OWNER_BRIDGE_APPROVAL_SECRET_MISSING" in secret_step["run"]
    assert auth["outputs"]["head_sha"] == "${{ steps.authorize.outputs.head_sha }}"
    for job in (review, release):
        assert secret_expr not in str(job)
        assert "OWNER_APPROVAL" not in job.get("env", {})
    assert review["needs"] == "owner-authorization"
    assert "needs.owner-authorization.result == 'success'" in review["if"]
    assert release["needs"] == ["owner-authorization", "owner-readonly-qa"]
    assert "needs.owner-authorization.result == 'success'" in release["if"]
    assert "needs.owner-readonly-qa.result == 'success'" in release["if"]
    assert "model_review_started: false" in str(auth["steps"])


def test_readonly_qa_and_merge_job_have_disjoint_authorities():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    review = doc["jobs"]["owner-readonly-qa"]
    release = doc["jobs"]["owner-bridge"]
    for name in ("contents", "issues", "pull-requests", "actions"):
        assert review["permissions"][name] == "read"
    assert "environment" not in review
    assert "WOW_OWNER_RELEASE_APPROVAL" not in str(review)
    qa_steps = [step for step in review["steps"]
                if "wow-claude-agent" in step.get("uses", "")]
    assert len(qa_steps) == 1
    assert qa_steps[0]["with"]["permission_profile"] == ":read-only"
    assert "wow-claude-agent" not in str(release["steps"])
    assert "needs.owner-readonly-qa.outputs.exact_head_sha" in str(release)
    assert "OWNER_BRIDGE_QA_SHA_MISMATCH" in str(release)
    assert "OWNER_BRIDGE_CLASS_C_DENIED" in str(release)
    gate = next(step for step in release["steps"]
                if step.get("id") == "preflight")
    assert "OWNER_APPROVAL" not in gate.get("env", {})
    assert '"$PR_NUMBER" = "$AUTH_PR"' in gate["run"]
    assert '"$EXPECTED_HEAD_SHA" = "$AUTH_SHA"' in gate["run"]
    assert '"$INCIDENT_ID" = "$AUTH_INCIDENT"' in gate["run"]


def test_owner_denial_happens_before_any_paid_agent():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    auth = doc["jobs"]["owner-authorization"]
    qa = doc["jobs"]["owner-readonly-qa"]
    merge = doc["jobs"]["owner-bridge"]
    assert auth["steps"][0]["id"] == "authorize"
    assert not any("wow-claude-agent" in step.get("uses", "")
                   for step in auth["steps"])
    assert qa["needs"] == "owner-authorization"
    assert "needs.owner-authorization.result == 'success'" in qa["if"]
    assert "needs.owner-authorization.result == 'success'" in merge["if"]
    assert "WOW_OWNER_RELEASE_APPROVAL" not in str(qa)
    assert "WOW_OWNER_RELEASE_APPROVAL" not in str(merge)


def test_isolated_qa_pins_source_and_blocks_unsafe_challenges():
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    review = document["jobs"]["owner-readonly-qa"]
    prepare = next(s for s in review["steps"]
                   if s.get("id") == "qa_prepare")["run"]
    assert ".head.sha == $h" in prepare
    assert '.base.ref == "main"' in prepare
    assert '.head.repo.full_name == $repo' in prepare
    assert "candidate.diff" in prepare
    validator = next(s for s in review["steps"]
                     if s.get("id") == "qa_check")["run"]
    assert '.decision == "PASS"' in validator
    assert '.exact_head_sha == $sha' in validator
    assert '.change_class == "A" or .change_class == "B"' in validator
    assert 'echo "decision=PASS"' in validator
