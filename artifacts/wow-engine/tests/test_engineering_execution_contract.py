"""Prevent primary/fallback agents from silently losing the execution contract."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKERS = (
    "wow-v17-chatgpt-engineering-worker.yml",
    "wow-v17-claude-engineering-worker.yml",
)

def test_all_engineering_worker_prompts_preserve_execution_and_role_boundaries():
    for name in WORKERS:
        workflow = yaml.load(
            (ROOT / ".github/workflows" / name).read_text(),
            Loader=yaml.BaseLoader,
        )
        prompts = [
            step["with"]["prompt"]
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if isinstance(step.get("with"), dict) and "prompt" in step["with"]
        ]
        assert prompts, f"No agent prompts discovered: {name}"
        for prompt in prompts:
            assert prompt.startswith("Read root AGENTS.md first"), name
            assert "within your assigned role" in prompt, name
            assert "Status polling is not repair evidence." in prompt, name
            assert "single-writer lease" in prompt, name
            assert "all protected release gates" in prompt, name

def test_execution_contract_preserves_runtime_and_probability_authority():
    contract = (ROOT / "AGENTS.md").read_text()
    for invariant in (
        "custom_gpt_identity=WOW_BETTING_ENGINE",
        "runtime_generation=V17_ACTIVE",
        "terminal_authority=V17_TERMINAL_REDUCER",
        "can_execute=false",
        "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true",
    ):
        assert invariant in contract
    assert "Do not replace missing sporting probabilities with LLM judgment or market odds." in contract

