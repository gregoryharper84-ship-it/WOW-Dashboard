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
    assert 'REPAIR_TARGET_NOT_APPROVED_P1_GLOBAL' in text
    assert "if: steps.targeted_dispatch.outputs.accepted == 'true'" in text
    assert 'AUTO_REPAIR_TARGET_HANDOFF_REQUIRED' not in text
