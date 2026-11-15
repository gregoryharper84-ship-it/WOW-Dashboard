"""Regression: owner-ratified minimum release policy must not drift into added mandatory gates.

This tests source policy consistency, not live GitHub admin state. Verify ruleset #24907422 separately.
"""
from pathlib import Path

from v17.agent_identity_policy import (
    REQUIRED_REGRESSION_CHECKS,
    QA_CHECK,
    RELEASE_CHECK,
)

ROOT = Path(__file__).resolve().parents[3]


def test_owner_minimum_release_policy_names_exact_three_native_contexts():
    expected = {
        "WOW required-three regression",
        "WOW governed probability backend",
        "WOW additional required regression",
    }
    assert set(REQUIRED_REGRESSION_CHECKS) == expected
    assert len(REQUIRED_REGRESSION_CHECKS) == 3
    document = (ROOT / "docs/WOW_STABILITY_FIRST_MINIMUM_GOVERNANCE.md").read_text()
    assert all(check in document for check in expected)
    assert "App ID 15368" in document


def test_simplified_release_keeps_optional_checks_honest_and_model_safety():
    document = (ROOT / "docs/WOW_STABILITY_FIRST_MINIMUM_GOVERNANCE.md").read_text()
    design = (ROOT / "docs/wow/engineering/WOW_V28_SINGLE_PATH_AUTONOMOUS_RELEASE.md").read_text()
    for name in (QA_CHECK, RELEASE_CHECK):
        assert name in document
    assert "diagnostic" in document
    assert "exactly the three required" in document
    assert "no longer required" in document
    for literal in ("can_execute=false", "V17_TERMINAL_REDUCER", "calibrated lower bound"):
        assert literal in document
    assert "one authenticated owner-authorized protected merge" in design
    assert "live ruleset reconciled to three checks" in design
    assert "including protected/security-adjacent Class P" in design
    assert "issue #1830" in document
    assert "without an independent premerge QA/SIRT reviewer" in document
