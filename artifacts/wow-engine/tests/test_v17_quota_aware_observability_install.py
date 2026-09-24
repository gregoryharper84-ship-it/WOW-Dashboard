from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY = (ROOT / "v17_observability.py").read_text()


def test_quota_aware_discovery_installs_after_cross_sport_resilience():
    import_marker = "from v17.quota_aware_degraded_discovery import ("
    resilience_call = "install_cross_sport_resilience()"
    quota_call = "install_quota_aware_degraded_discovery()"

    assert import_marker in OBSERVABILITY
    assert resilience_call in OBSERVABILITY
    assert quota_call in OBSERVABILITY
    assert OBSERVABILITY.index(resilience_call) < OBSERVABILITY.index(quota_call)


def test_llp_editor_contract_requires_full_slate_acquisition_truth():
    instructions = (ROOT.parent.parent / "LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md").read_text()
    assert "FULL-SLATE DISCOVERY / RECONCILIATION" in instructions
    assert "Discover every configured sport/regime before model filtering" in instructions
    assert "Provider/auth/quota/market failures are acquisition failures, never MODEL_UNAVAILABLE" in instructions
    assert "DISCOVERY_OR_ACQUISITION_INCOMPLETE" in instructions
    assert "BOARD_COVERAGE_STATUS" in instructions


def test_llp_editor_contract_remains_under_8000_characters():
    instructions = (ROOT.parent.parent / "LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md").read_text()
    pasteable = instructions.split("```", 2)[1]
    assert len(pasteable) < 8000
