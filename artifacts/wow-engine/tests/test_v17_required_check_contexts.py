from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPREAD = ROOT / ".github/workflows/wow-v17-spread-forward-shadow.yml"
RELEASE = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"


def test_spread_required_context_is_emitted_for_every_main_pr():
    text = SPREAD.read_text(encoding="utf-8")
    pull_request_block = text.split("  pull_request:", 1)[1].split("  push:", 1)[0]

    assert "branches:" in pull_request_block
    assert "- main" in pull_request_block
    assert "paths:" not in pull_request_block
    assert "name: Verify governed line forward shadows" in text
    assert "Required-context no-op for unrelated PRs" in text
    assert "if: steps.impact.outputs.affected == 'true'" in text


def test_release_required_context_exists_before_merge_and_real_verification_remains_post_merge():
    text = RELEASE.read_text(encoding="utf-8")

    assert "types: [opened, synchronize, reopened, ready_for_review, closed]" in text
    assert "name: Release / Production Verification agent" in text
    assert "Pre-merge required-context contract gate" in text
    assert "Production verification remains post-merge." in text
    assert "github.event.action == 'closed'" in text
    assert "github.event.pull_request.merged == true" in text
    assert "uses: ./.github/actions/wow-chatgpt-agent" in text
    assert "can_execute=false" in text
