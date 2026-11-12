"""Contract regression for V25 repository agent instructions (#1777).

Static instruction coverage only: this does NOT prove a coding agent adopted
the instructions or that CI/production/release gates have passed.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_IDENTITIES = (
    "custom_gpt_identity=WOW_BETTING_ENGINE",
    "runtime_generation=V17_ACTIVE",
    "terminal_authority=V17_TERMINAL_REDUCER",
    "can_execute=false",
    "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true",
)
FINAL_DISPOSITIONS = (
    "FIXED_AND_VERIFIED",
    "PR_CREATED",
    "EXPERIMENT_CREATED",
    "DUPLICATE",
    "NOT_REPRODUCIBLE",
    "BLOCKED_WITH_EXACT_REASON",
    "DEFERRED_WITH_JUSTIFICATION",
)


def contract_violations(agents: str, claude: str) -> list[str]:
    """Fail closed if a mandatory instruction boundary is dropped."""
    missing = [f"identity:{item}" for item in REQUIRED_IDENTITIES if item not in agents]
    rules = {
        "class_a": "pre-authorized Class A reliability",
        "reproduction": "Reproduce the exact failure",
        "tests": "focused, negative, regression, integration and adjacent-lane tests",
        "retry": "up to three bounded evidence-driven attempts",
        "independent": "every applicable exact-SHA gate and authorization is independently established",
        "trust_root_hold": "agent instruction/trust-root",
        "class_b": "Class B probability-adjacent",
        "class_c": "Class C probability-producing",
        "same_identity": "same principal through a different tool",
        "protected_main": "write directly to protected main",
        "no_execute": "Never place bets or orders",
        "diagnosis_non_success": "`DIAGNOSED_NOT_FIXED` is non-success",
        "production": "live production SHA/acceptance",
        "reentry": "re-entry condition",
    }
    missing.extend(f"rule:{key}" for key, token in rules.items() if token not in agents)
    missing.extend(f"disposition:{item}" for item in FINAL_DISPOSITIONS if item not in agents)
    if "V25 operational overlay" not in claude:
        missing.append("claude:overlay")
    if "AGENTS.md" not in claude:
        missing.append("claude:shared_contract")
    if "supplements, never supersedes" not in claude:
        missing.append("claude:precedence")
    if "only after independently authorized exact-SHA protected gates" not in claude:
        missing.append("claude:independent_release")
    return missing


def test_v25_instructions_preserve_execution_and_governance() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert contract_violations(agents, claude) == []
    assert claude.index("V25 operational overlay") < claude.index("## 0. Current authority")


def test_v25_mutation_omitting_independent_release_fails() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    corrupted = agents.replace(
        "every applicable exact-SHA gate and authorization is independently established",
        "tests finished",
    )
    assert "rule:independent" in contract_violations(corrupted, claude)


def test_v25_mutation_dropping_execution_boundary_fails() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    corrupted = agents.replace("can_execute=false", "can_execute=true")
    assert "identity:can_execute=false" in contract_violations(corrupted, claude)


def test_v25_mutation_approving_report_only_fails() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    corrupted = agents.replace("`DIAGNOSED_NOT_FIXED` is non-success", "DIAGNOSIS_IS_SUCCESS")
    assert "rule:diagnosis_non_success" in contract_violations(corrupted, claude)


def test_v25_mutation_dropping_claude_review_hold_fails() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    corrupted = claude.replace(
        "only after independently authorized exact-SHA protected gates", "after green tests"
    )
    assert "claude:independent_release" in contract_violations(agents, corrupted)
