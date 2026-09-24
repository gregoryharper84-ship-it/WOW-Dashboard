from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
IMPACT = ROOT / ".github/workflows/wow-v17-change-impact-gate.yml"
PRODUCT = ROOT / ".github/workflows/wow-v17-golden-product-acceptance.yml"
SCORECARD = ROOT / ".github/workflows/wow-v17-engineering-effectiveness-scorecard.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_effectiveness_workflows_parse() -> None:
    assert yaml.safe_load(IMPACT.read_text())["name"] == "wow-v17-change-impact-gate"
    assert yaml.safe_load(PRODUCT.read_text())["name"] == "wow-v17-golden-product-acceptance"
    assert yaml.safe_load(SCORECARD.read_text())["name"] == "wow-v17-engineering-effectiveness-scorecard"


def test_scorecard_closes_only_explicit_supersession_after_merged_replacement() -> None:
    text = _text(SCORECARD)
    assert "explicit_superseded_open_prs" in text
    assert "Superseded-By:" in text
    assert "replacement is not merged; leaving open" in text
    assert 'gh pr close "$pr_number"' in text
    assert "can_execute: false" in text


def test_product_acceptance_is_independent_of_backend_green() -> None:
    text = _text(PRODUCT)
    assert "backend/CI success alone can never produce USER_JOURNEY_HEALTH=PASS" in text
    assert "repository_declared_user_journey" in text
    assert "WOW_BETTING_ENGINE" in text
    assert "scoreWowPickRequest" in text


def test_change_impact_gate_reports_remote_gates_as_not_satisfied_by_ci() -> None:
    text = _text(IMPACT)
    assert "Repository CI does not satisfy" in text
    assert "full-slate production acceptance" in text
    assert "exact merged-SHA Render verification" in text
    assert "golden backend/user-journey acceptance" in text
