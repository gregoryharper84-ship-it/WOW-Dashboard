import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
LOOP = ROOT / ".github/workflows/wow-v17-24h-engineering-closure-loop.yml"
EXPERIMENT = ROOT / ".github/workflows/wow-v17-model-improvement-experiment.yml"
PRODUCT = ROOT / ".github/workflows/wow-v17-golden-product-acceptance.yml"
IMPACT = ROOT / ".github/workflows/wow-v17-change-impact-gate.yml"


def _text(path: Path) -> str:
    return path.read_text()


def _assert_experiment_auto_merge_contract(text: str) -> None:
    pattern = re.compile(
        r'gh\s+pr\s+merge\s+"\$PR_NUMBER"\s+'
        r'--repo\s+"\$GITHUB_REPOSITORY"\s+'
        r'--auto\s+--merge(?:\s+\|\|\s+true)?'
    )
    assert pattern.search(text), "experiment PR must remain on protected GitHub auto-merge"


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
    assert 'elif [ "$product_health" != "PASS" ]; then' in text
    assert 'Golden user journey is not PASS; run independent product acceptance before discretionary model research.' in text
    assert "single implementation lease" in text


def test_continuation_selector_never_hands_off_draft_prs() -> None:
    text = _text(LOOP)
    assert text.count("select(.draft == false)") >= 2
    assert 'contains("Morning-Green-Autonomous: true")' in text
    assert 'contains("Model-Experiment-Autonomous: true")' in text


def test_open_experiment_pr_is_actively_advanced_only_after_product_health_pass() -> None:
    text = _text(LOOP)
    assert "Resume governed model-experiment PR" in text
    assert 'elif [ -n "$experiment_pr" ]; then' in text
    assert text.index('product_health" != "PASS"') < text.index('elif [ -n "$experiment_pr" ]; then')
    _assert_experiment_auto_merge_contract(text)
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
    _assert_experiment_auto_merge_contract(_text(LOOP))


def test_new_workflows_parse_as_yaml() -> None:
    assert yaml.safe_load(LOOP.read_text())["name"] == "wow-v17-24h-engineering-closure-loop"
    assert yaml.safe_load(EXPERIMENT.read_text())["name"] == "wow-v17-model-improvement-experiment"
    assert yaml.safe_load(PRODUCT.read_text())["name"] == "wow-v17-golden-product-acceptance"
    assert yaml.safe_load(IMPACT.read_text())["name"] == "wow-v17-change-impact-gate"


def test_closure_controller_has_hard_wip_and_golden_journeys() -> None:
    team = _text(ROOT / "artifacts/wow-engine/v17/engineering_agent_team.py")
    assert 'TEAM_VERSION = "3.0"' in team
    assert "MAX_ACTIVE_PRODUCT_RECOVERY = 1" in team
    assert "MAX_ACTIVE_SUPPORTING_INVESTIGATION = 1" in team
    assert "ALL_SPORTS_PROPS" in team
    assert "ALL_SPORTS_ML_WINNERS" in team
    assert "ALL_SPORTS_UPSETS" in team
    assert "validate_closure_record" in team
    assert "closure_wip" in team


def test_failure_router_preserves_typed_failure_ownership() -> None:
    team = _text(ROOT / "artifacts/wow-engine/v17/engineering_agent_team.py")
    for failure in (
        "DISCOVERY_FAILURE", "PROVIDER_FAILURE", "CANONICAL_IDENTITY_FAILURE",
        "HYDRATION_FAILURE", "MODEL_INPUTS_INSUFFICIENT", "MODEL_UNAVAILABLE",
        "SCORER_FAILURE", "ACTION_TRANSPORT_FAILURE", "PERSISTENCE_FAILURE",
    ):
        assert failure in team
    assert 'route_failure("ACTION_TRANSPORT_FAILURE") == "transport"' in team
    assert 'route_failure("MODEL_UNAVAILABLE") == "model-capability"' in team


def test_capability_matrix_is_explicit_and_fail_closed() -> None:
    team = _text(ROOT / "artifacts/wow-engine/v17/engineering_agent_team.py")
    for dimension in (
        "discovery_supported", "canonicalization_supported", "hydration_supported",
        "fitted_specialist_registered", "artifact_certified", "calibration_valid",
        "production_enabled",
    ):
        assert dimension in team
    assert "production_enabled requires every upstream capability" in team
