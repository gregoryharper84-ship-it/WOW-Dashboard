"""Regression coverage for Flask startup/model-market objective separation."""
from __future__ import annotations

import ast
from pathlib import Path


PRE_START = Path(__file__).resolve().parents[1] / "pre_start.py"


def _literal_assignment(name: str):
    tree = ast.parse(PRE_START.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"assignment {name} not found")


def test_odds_key_is_not_a_process_start_requirement():
    required = _literal_assignment("REQUIRED_ENV_VARS")
    optional_market = _literal_assignment("OPTIONAL_MARKET_ENV_VARS")

    assert "DATABASE_URL" in required
    assert "SCORING_API_KEY" in required
    assert "ODDS_API_KEY" not in required
    assert "ODDS_API_KEY" in optional_market


def test_missing_market_credentials_are_declared_fail_closed_hold_not_startup_fatal():
    source = PRE_START.read_text(encoding="utf-8")
    assert "market lane must fail closed/HOLD" in source
    assert "FATAL: missing env var" in source
