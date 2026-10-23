"""Static fail-closed tests for native Claude Code integration, without provider usage."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / '.claude/skills/wow-debug-dissolve-deploy/SKILL.md'
AGENTS = (
    ROOT / '.claude/agents/wow-root-cause-investigator.md',
    ROOT / '.claude/agents/wow-regression-auditor.md',
)
README = ROOT / '.claude/README.md'


def _metadata(path: Path) -> dict:
    content = path.read_text()
    assert content.startswith('---\n')
    sections = content.split('---', 2)
    assert len(sections) == 3
    metadata = yaml.safe_load(sections[1])
    assert isinstance(metadata, dict)
    assert metadata['name']
    assert metadata['description']
    assert sections[2].strip()
    return metadata


def test_project_skill_is_discoverable_and_scoped_to_existing_authority():
    assert SKILL.is_file()
    metadata = _metadata(SKILL)
    assert metadata['name'] == 'wow-debug-dissolve-deploy'
    body = SKILL.read_text()
    for required in (
        'CLAUDE.md', '.agents/skills/', 'existing PR branch',
        'OWNERSHIP_CONFLICT', 'V17_TERMINAL_REDUCER',
        'can_execute=false', 'independently verify',
    ):
        assert required in body
    assert 'Never claim live verification from local tests' in body


def test_native_agents_are_read_only_and_not_approval_principals():
    expected_names = {'wow-root-cause-investigator', 'wow-regression-auditor'}
    assert { _metadata(path)['name'] for path in AGENTS } == expected_names
    for path in AGENTS:
        metadata = _metadata(path)
        tools = {part.strip() for part in metadata['tools'].split(',')}
        assert tools == {'Read', 'Grep', 'Glob'}
        assert metadata['model'] == 'sonnet'
        body = path.read_text()
        assert 'CLAUDE.md' in body
        assert 'can_execute=false' in body or 'can_execute=false' in README.read_text()
    assert 'not an independent QA or Release App approval' in AGENTS[1].read_text()


def test_bootstrap_does_not_claim_live_recovery_or_change_provider_routing():
    body = README.read_text()
    for required in (
        'source-level bootstrap only', 'unverified',
        'Do not launch paid Claude jobs',
        'Do not launch paid Claude jobs, alter provider priority',
        'can_execute=false', 'V17_TERMINAL_REDUCER',
    ):
        assert required in body


def test_existing_referenced_skill_contracts_exist():
    for relative_path in (
        '.agents/skills/wow-engineering-research-triage-agent/SKILL.md',
        '.agents/skills/wow-engineering-qa-verification-agent/SKILL.md',
    ):
        assert (ROOT / relative_path).is_file()
