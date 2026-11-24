from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".agents/skills"
WORKER = ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml"


def test_implementation_agent_requires_reproduce_first_red_green_and_single_writer() -> None:
    text = (SKILLS / "wow-engineering-implementation-agent/SKILL.md").read_text()
    for requirement in (
        "exact error",
        "affected revision",
        "reproduction command",
        "failure boundary",
        "falsifiable root cause",
        "before editing",
        "pre-fix RED",
        "expected failure",
        "exit code",
        "minimal GREEN",
        "negative",
        "adjacent",
        "full regression",
        "single-writer lease",
        "secondary control plane",
    ):
        assert requirement in text


def test_independent_review_agent_challenges_evidence_and_never_self_approves() -> None:
    text = (SKILLS / "wow-engineering-independent-review-agent/SKILL.md").read_text()
    for requirement in (
        "original reproduction",
        "pre-fix RED",
        "exact-head GREEN",
        "race",
        "stale SHA",
        "security",
        "probability substitution",
        "root cause",
        "weak tests",
        "self-approve",
    ):
        assert requirement in text


def test_qa_agent_requires_fresh_exact_head_evidence_and_separate_acceptance() -> None:
    text = (SKILLS / "wow-engineering-qa-verification-agent/SKILL.md").read_text()
    for requirement in (
        "fresh",
        "command",
        "exit code",
        "failure count",
        "exact HEAD SHA",
        "original defect",
        "stale",
        "partial",
        "deploy/health",
        "model",
        "persistence",
        "Action",
        "failure-injection",
        "runtime compliance",
        "independent roles",
    ):
        assert requirement in text


def test_existing_chatgpt_worker_loads_all_three_engineering_skills() -> None:
    text = WORKER.read_text()
    for skill in (
        ".agents/skills/wow-engineering-implementation-agent/SKILL.md",
        ".agents/skills/wow-engineering-independent-review-agent/SKILL.md",
        ".agents/skills/wow-engineering-qa-verification-agent/SKILL.md",
    ):
        assert skill in text
