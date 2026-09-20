from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-claude-engineering-worker.yml"


def test_worker_preserves_edits_when_claude_omits_branch_output():
    text = WORKFLOW.read_text()
    assert 'Claude reported changes but returned neither a branch nor a working-tree diff.' in text
    assert 'branch="claude/engineering/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in text
    assert 'git switch -c "$branch"' in text
    assert 'git add --all' in text
    assert 'git commit -m "Claude Engineering: ${incident:-nightly-repair}"' in text
    assert 'git push --set-upstream origin "$branch"' in text
    assert 'GITHUB_TOKEN: ${{ github.token }}' in text
