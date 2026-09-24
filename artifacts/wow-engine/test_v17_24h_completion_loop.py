from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
LOOP = ROOT / ".github/workflows/wow-v17-24h-engineering-closure-loop.yml"
EXPERIMENT = ROOT / ".github/workflows/wow-v17-model-improvement-experiment.yml"
PRODUCT = ROOT / ".github/workflows/wow-v17-golden-product-acceptance.yml"
IMPACT = ROOT / ".github/workflows/wow-v17-change-impact-gate.yml"


def _text(path: Path) -> str:
    return path.read_text()


def test_24h_loop_runs_hourly_and_advances_real_work() -> None:
    text = _text(LOOP)
    assert 'cron: "47 * * * *"' in text
    assert "push:" in text
    assert "branches: [main]" in text
    assert "wow-v17-morning-green-continuation.yml" in text
    assert "wow-v17-release-resume-agent.yml" in text
    assert "wow-v17-chatgpt-engineering-worker.yml" in text
    assert "wow-v17-golden-product-acceptance.yml" in text
    assert "wow-v17-model-improvement-experiment.yml" in text
    assert "wow-v17-nightly-engineering-scan.yml" in text
    assert "finish existing work before starting new work" in text
    assert "can_execute: false" in text


def test_24h_loop_prioritizes_product_reliability_before_model_improvement() -> None:
    text = _text(LOOP)
    repair_pr = text.index('action="RESUME_REPAIR_PR"')
    release = text.index('action="VERIFY_RELEASE"')
    repair = text.index('action="REPAIR"')
    product = text.index('action="VERIFY_PRODUCT"')
    experiment = text.index('action="RESUME_EXPERIMENT_PR"')
    improve = text.index('action="IMPROVE_MODEL"')
    assert repair_pr < release < repair < product < experiment < improve
    assert 'product_health" != "PASS"' in text
    assert "no discretionary model research while golden user journey is not PASS" in text
    assert 'utc_hour % 6' in text
    assert "single implementation lease" in text


def test_continuation_selector_never_hands_off_draft_prs() -> None:
    text = _text(LOOP)
    # Both autonomous repair and experiment selectors must exclude drafts before dispatch.
    assert text.count("select(.draft == false)") >= 2
    assert 'contains("Morning-Green-Autonomous: true")' in text
    assert 'contains("Model-Experiment-Autonomous: true")' in text


def test_open_experiment_pr_is_actively_advanced_only_after_product_health_pass() -> None:
    text = _text(LOOP)
    assert "Resume governed model-experiment PR" in text
    assert 'elif [ -n "$experiment_pr" ]; then' in text
    assert text.index('product_health" != "PASS"') < text.index('elif [ -n "$experiment_pr" ]; then')
    assert 'gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --auto --merge' in text
    assert 'repair_pr="$PR_NUMBER"' in text
    assert "Failed experiment CI routed to bounded ChatGPT experiment repair." in text
    assert "Experiment PR escaped non-serving boundary" in text


def test_golden_product_acceptance_never_infers_pass_from_backend_health() -> None:
    text = _text(PRODUCT)
    assert "Independent golden product acceptance" in text
    assert "backend/CI success alone can never produce USER_JOURNEY_HEALTH=PASS" in text
    assert "engineering_effectiveness.py health" in text
    assert "WOW_BETTING_ENGINE" in text
    assert "scoreWowPickRequest" in text
    assert "can_execute: false" in text


def test_change_impact_gate_runs_adjacent_contract_regressions() -> None:
    text = _text(IMPACT)
    assert "engineering_effectiveness.py impact" in text
    assert "WORKFLOW_HANDOFF_ACCEPTANCE" in text
    assert "GPT_EDITOR_SYNC_ACCEPTANCE" in text
    assert "FULL_SLATE_PRODUCTION_ACCEPTANCE" in text
    assert "EXACT_SHA_RENDER_VERIFICATION" in text
    assert "GOLDEN_BACKEND_ACCEPTANCE" in text
    assert "Repository CI does not satisfy" in text


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


def test_model_experiment_requires_tests_regression_and_repair_mode() -> None:
    text = _text(EXPERIMENT)
    assert "repair_pr:" in text
    assert 'echo "mode=repair"' in text
    assert "Experiment repair run produced no corrective change." in text
    assert "python -m pytest -q artifacts/wow-engine/v17/experiments" in text
    assert "python -m pytest -q artifacts/wow-engine" in text
    assert 'gh pr merge "$pr_number" --repo "$GITHUB_REPOSITORY" --auto --merge' in text
    assert 'gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --auto --merge' in text


def test_new_workflows_parse_as_yaml() -> None:
    assert yaml.safe_load(LOOP.read_text())["name"] == "wow-v17-24h-engineering-closure-loop"
    assert yaml.safe_load(EXPERIMENT.read_text())["name"] == "wow-v17-model-improvement-experiment"
    assert yaml.safe_load(PRODUCT.read_text())["name"] == "wow-v17-golden-product-acceptance"
    assert yaml.safe_load(IMPACT.read_text())["name"] == "wow-v17-change-impact-gate"
