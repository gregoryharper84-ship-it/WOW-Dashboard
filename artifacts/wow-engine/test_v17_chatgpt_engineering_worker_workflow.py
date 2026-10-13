from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml"


def test_worker_preserves_edits_when_openai_agent_returns_working_tree_changes():
    text = WORKFLOW.read_text()
    assert 'ChatGPT reported changes but returned no working-tree diff.' in text
    assert 'branch="chatgpt/engineering/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in text
    assert 'git switch -c "$branch"' in text
    assert 'git add --all' in text
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
        '--force-with-lease="refs/heads/$branch:$REPAIR_SHA"',
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
