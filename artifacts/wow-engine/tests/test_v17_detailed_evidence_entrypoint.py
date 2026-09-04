from __future__ import annotations

from pathlib import Path

from v17.detailed_evidence_install import DetailedRawPropEvidence


def test_v17_detailed_evidence_keeps_canonical_render_entrypoint_and_wiring():
    render = Path("../../render.yaml").read_text()
    daily_runtime = Path("v17/daily_snapshot_runtime.py").read_text()

    assert "uvicorn api_ncaaf_acceptance:app" in render
    assert "install_v17_detailed_evidence" in daily_runtime
    assert "install_v17_detailed_evidence(app, auth_dependency=auth_dependency, market_api=market_api)" in daily_runtime
    assert "detailed_evidence" in DetailedRawPropEvidence.model_fields


def test_v17_detailed_evidence_contract_preserves_execution_and_probability_authority():
    installer = Path("v17/detailed_evidence_install.py").read_text()

    assert '"market_evidence_separate": True' in installer
    assert '"probability_substitution_allowed": False' in installer
    assert '"global_terminal_authority": "V17_TERMINAL_REDUCER"' in installer
    assert '"can_execute": False' in installer
    assert '"numerical_authority": "CONTROLLING_SPECIALIST_ADAPTER_ONLY"' in installer
