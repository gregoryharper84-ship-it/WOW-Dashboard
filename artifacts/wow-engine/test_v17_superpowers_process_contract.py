from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = Path(".agents/skills")

@pytest.mark.parametrize("skill,heading", [
    ("wow-engineering-implementation-agent",
     "## Reproduce-first RED/GREEN protocol"),
    ("wow-engineering-independent-review-agent",
     "## Independent test-evidence challenge"),
    ("wow-engineering-qa-verification-agent",
     "## Fresh exact-head verification"),
])
def test_superpowers_protocol(skill, heading):
    path = ROOT / SKILLS / skill / "SKILL.md"
    assert heading in path.read_text()

def test_worker_references_existing_skills():
    workflow = (
        ROOT / ".github/workflows/"
        "wow-v17-chatgpt-engineering-worker.yml"
    ).read_text()

    for skill in [
        "wow-engineering-implementation-agent",
        "wow-engineering-independent-review-agent",
        "wow-engineering-qa-verification-agent",
    ]:
        assert skill in workflow
