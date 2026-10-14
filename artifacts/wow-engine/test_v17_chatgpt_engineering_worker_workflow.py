from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml"


def test_worker_preserves_edits_when_openai_agent_returns_working_tree_changes():
    text = WORKFLOW.read_text()
    assert 'ChatGPT reported changes but returned no working-tree diff.' in text
    assert 'branch="chatgpt/engineering/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in text
    assert 'git -C "$target" switch -c "$branch"' in text
    assert 'git -C "$target" add --all' in text
    assert 'git commit -m "ChatGPT Engineering: ${incident:-nightly-repair}"' in text
    assert 'git push --set-upstream origin "$branch"' in text
    # Push authors as wow-engineering[bot] via the minted App token, not the ambient
    # Actions token (#1550/#1663 item 1) — see .github/scripts/pr_authoring_token_audit.py.
    assert 'GITHUB_TOKEN: ${{ steps.eng_token.outputs.token }}' in text


def test_worker_uses_openai_writer_with_bounded_claude_read_only_peers():
    text = WORKFLOW.read_text()
    assert "./.github/actions/wow-chatgpt-agent" in text
    assert "./.github/actions/wow-claude-agent" in text
    assert "OPENAI_API_KEY" in text
    assert "ANTHROPIC_API_KEY" in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text
    assert "CROSS_PROVIDER_PEER_DIAGNOSIS_AGENT" in text
    assert "CROSS_PROVIDER_ADVERSARIAL_REVIEW_AGENT" in text
    assert text.count('permission_profile: ":workspace"') == 1
    assert text.count("uses: ./.github/actions/wow-claude-agent") == 3
    assert "CROSS_PROVIDER_QA_COUNTEREXAMPLE_AGENT" in text
    assert "wow-claude-utilization-receipt.json" in text
    assert 'authority: "READ_ONLY_CROSS_PROVIDER"' in text
    assert 'implementation_writer: false' in text
    assert "steps.claude_review.outputs.decision != 'REJECT'" in text
    assert "OpenAI/ChatGPT retains the single implementation lease" in text
    assert "can_execute=false" in text


def test_exact_head_repair_target_is_fail_closed_and_branch_fenced():
    text = WORKFLOW.read_text()
    for signal in (
        'REPAIR_TARGET_INCOMPLETE',
        'REPAIR_TARGET_INVALID',
        'REPAIR_TARGET_STALE_OR_UNTRUSTED',
        'REPAIR_INCIDENT_MISMATCH',
        'REPAIR_HEAD_MOVED',
        'REPAIR_BRANCH_INVALID',
        'REPAIR_REMOTE_HEAD_MOVED',
        'steps.repair_target.outputs.bound != \'true\'',
    ):
        assert signal in text


def test_auto_repair_requires_targeted_dispatch_before_retry_accounting():
    text = (WORKFLOW.parent / 'wow-v17-engineering-auto-repair.yml').read_text()
    assert '-f repair_pr="$PR_NUMBER" -f repair_head_sha="$HEAD_SHA"' in text
    assert 'REPAIR_INCIDENT_NOT_IN_APPROVED_MANIFEST' in text
    assert 'REPAIR_INCIDENT_SEVERITY_MISMATCH' in text
    assert 'REPAIR_P0_DOMAIN_LEASE_MISSING' in text
    assert 'REPAIR_P1_LEASE_MISMATCH' in text
    assert '-f target_incident="$incident" -f lease_group="$lease_group"' in text
    assert "if: steps.targeted_dispatch.outputs.accepted == 'true'" in text
    assert 'AUTO_REPAIR_TARGET_HANDOFF_REQUIRED' not in text


def test_autonomy_p0_is_registered_with_exclusive_manifest_lease():
    import json

    manifest = json.loads((WORKFLOW.parents[2] / 'artifacts/wow-engine/v17/engineering_dispatch_manifest.json').read_text())
    entries = [entry for entry in manifest['restoration'] if entry['issue_number'] == 1688]
    assert len(entries) == 1
    entry = entries[0]
    assert entry['severity'] == 'P0'
    assert entry['execution_lane'] == 'RAPID'
    assert entry['rapid_stream'] == 'AUTONOMY_RESTORE'
    assert entry['lease_group'] == 'P0_AUTONOMY_RESTORE'
    assert 'WORKFLOW_TRUST_ROOT' in entry['conflict_keys']


def test_auto_repair_escapes_untrusted_multiline_hold_summary():
    text = (WORKFLOW.parent / 'wow-v17-engineering-auto-repair.yml').read_text()
    assert 'summary_b64=$(printf %s "$summary" | base64 -w0)' in text
    assert 'HOLD_SUMMARY_B64: ${{ steps.resolve.outputs.summary_b64 }}' in text
    assert 'summary=$(printf %s "$HOLD_SUMMARY_B64" | base64 --decode)' in text
    assert 'summary<<WOW_HOLD_SUMMARY' not in text


def test_ci_postgres_images_avoid_public_ecr_data_limit():
    # Public ECR returned toomanyrequests during protected CI container initialization.
    for workflow in ('wow-verify.yml', 'wow-engine-verify.yml'):
        text = (WORKFLOW.parent / workflow).read_text()
        assert 'image: public.ecr.aws/docker/library/postgres:16' not in text
        assert 'image: postgres:16' in text


def test_attempt_marker_has_authenticated_writer_and_invalid_state_fails_closed():
    text = (WORKFLOW.parent / 'wow-v17-engineering-auto-repair.yml').read_text()
    assert '.user.login == "wow-engineering[bot]"' in text
    assert 'RECOVERY_ATTEMPT_MARKER_INVALID' in text
    assert 'sort_by(.updated_at // .created_at, .id)' in text
    assert 'attempts=0' in text


def test_bound_repair_packet_is_source_pinned_and_excludes_untrusted_instructions():
    text = WORKFLOW.read_text()
    assert "Bind independent HOLD evidence to exact repair branch" in text
    assert "if: steps.repair_target.outputs.bound == 'true'" in text
    assert "REPAIR_INDEPENDENT_APP_IDENTITY_INVALID" in text
    assert 'QA_APP_ID: ${{ vars.WOW_QA_APP_ID }}' in text
    assert 'RELEASE_APP_ID: ${{ vars.WOW_RELEASE_APP_ID }}' in text
    assert 'current.get("status") != "completed"' in text
    assert 'current.get("conclusion") != "failure"' in text
    assert 'v.get("head_sha") == sha' in text
    assert 'v.get("name") == name' in text
    assert 'v.get("app") or {}' in text
    assert 'max(matched, key=lambda v: int(v.get("id") or 0))' in text
    assert "REPAIR_HOLD_SUPERSEDED_OR_UNAVAILABLE" in text
    assert "REPAIR_HOLD_CODES_MISSING" in text
    assert '"schema": "WOW_REPAIR_TARGET_V1"' in text
    assert '"can_execute": False' in text
    assert '"independent_failed_checks": checks' in text
    assert '"output.summary"' not in text
    assert text.index("Bind independent HOLD evidence to exact repair branch") < text.index("Engineering Lead agent")


def test_bound_repair_context_reaches_independent_agents_and_same_branch_writer():
    text = WORKFLOW.read_text()
    assert text.count(".agent-handoff/repair-context.json") >= 5
    assert "exclusively binds your repair to the existing Engineering App PR" in text
    assert "Fix that PR's reproduced regression" in text
    assert 'if [ -n "${REPAIR_BRANCH:-}" ]; then branch="$REPAIR_BRANCH"; fi' in text
    assert '--force-with-lease="refs/heads/$branch:$REPAIR_SHA"' not in text
    assert "          rm -f artifacts/wow-engine/v17/.agent-handoff/repair-context.json" in text
    assert text.index("rm -f artifacts/wow-engine/v17/.agent-handoff/repair-context.json") < text.index('git -C "$target" add --all')
    assert "steps.repair_target.outputs.bound != 'true'" in text
