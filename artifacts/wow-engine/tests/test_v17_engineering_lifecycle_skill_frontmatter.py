"""Regression contract for the Codex engineering lifecycle skill loader."""
from pathlib import Path


def test_lifecycle_closure_skill_has_valid_frontmatter():
    root = Path(__file__).resolve().parents[3]
    skill = root / ".agents/skills/wow-engineering-lifecycle-closure-cell/SKILL.md"
    source = skill.read_text(encoding="utf-8")
    assert source.startswith("---\n")
    lines = source.splitlines()
    assert "---" in lines[1:]
    end = lines[1:].index("---") + 1
    header = lines[1:end]
    assert "name: wow-engineering-lifecycle-closure-cell" in header
    assert any(line.startswith("description: ") and line[len("description: "):].strip() for line in header)
    assert "can_execute=false" in source
