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
    assert 'GITHUB_TOKEN: ${{ github.token }}' in text


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
    assert text.count("uses: ./.github/actions/wow-claude-agent") == 2
    assert "steps.claude_review.outputs.decision != 'REJECT'" in text
    assert "OpenAI/ChatGPT retains the single implementation lease" in text
    assert "can_execute=false" in text
