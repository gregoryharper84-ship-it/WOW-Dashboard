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


def test_worker_uses_only_openai_agent_runtime():
    text = WORKFLOW.read_text()
    assert "./.github/actions/wow-chatgpt-agent" in text
    assert "OPENAI_API_KEY" in text
    assert "wow-claude-agent" not in text
    assert "ANTHROPIC_API_KEY" not in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in text
