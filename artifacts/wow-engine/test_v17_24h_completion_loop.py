from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
LOOP = ROOT / ".github/workflows/wow-v17-24h-engineering-closure-loop.yml"
EXPERIMENT = ROOT / ".github/workflows/wow-v17-model-improvement-experiment.yml"


def _text(path: Path) -> str:
    return path.read_text()


def test_24h_loop_runs_hourly_and_advances_real_work() -> None:
    text = _text(LOOP)
    assert 'cron: "47 * * * *"' in text
    assert "wow-v17-morning-green-continuation.yml" in text
    assert "wow-v17-release-resume-agent.yml" in text
    assert "wow-v17-chatgpt-engineering-worker.yml" in text
    assert "wow-v17-model-improvement-experiment.yml" in text
    assert "wow-v17-nightly-engineering-scan.yml" in text
    assert "finish existing work before starting new work" in text
    assert "can_execute: false" in text


def test_24h_loop_prioritizes_closure_before_new_improvement() -> None:
    text = _text(LOOP)
    repair_pr = text.index('action="RESUME_REPAIR_PR"')
    release = text.index('action="VERIFY_RELEASE"')
    repair = text.index('action="REPAIR"')
    experiment_wait = text.index('action="EXPERIMENT_WAIT"')
    improve = text.index('action="IMPROVE_MODEL"')
    assert repair_pr < release < repair < experiment_wait < improve
    assert 'utc_hour % 6' in text
    assert "single implementation lease" in text


def test_model_improvement_is_non_serving_and_path_guarded() -> None:
    text = _text(EXPERIMENT)
    assert "MODEL_IMPROVEMENT_EXPERIMENT_AGENT" in text
    assert "actual evidence-backed model-improvement work, not a narrative status report" in text
    assert "artifacts/wow-engine/v17/experiments/*" in text
    assert "Experiment escaped non-serving boundary" in text
    assert "Production-Behavior-Change: false" in text
    assert "Class-C-Promotion: false" in text
    assert "production_change: false" in text
    assert "can_execute=false" in text
    assert "OPENAI_API_KEY" in text
    assert "Claude" not in text
    assert "ANTHROPIC" not in text


def test_model_experiment_requires_tests_and_adjacent_regression() -> None:
    text = _text(EXPERIMENT)
    assert "python -m pytest -q artifacts/wow-engine/v17/experiments" in text
    assert "python -m pytest -q artifacts/wow-engine" in text
    assert 'gh pr merge "$pr_number" --repo "$GITHUB_REPOSITORY" --auto --merge' in text


def test_new_workflows_parse_as_yaml() -> None:
    assert yaml.safe_load(LOOP.read_text())["name"] == "wow-v17-24h-engineering-closure-loop"
    assert yaml.safe_load(EXPERIMENT.read_text())["name"] == "wow-v17-model-improvement-experiment"
