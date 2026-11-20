"""Unit tests for read-only certification evidence evaluator."""
import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "v17" / "certification_evidence_readonly.py"
spec = importlib.util.spec_from_file_location("certification_evidence_readonly", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def row():
    return dict(prediction_id="p1", event_id="e1", market_key="home",
                model_artifact_sha="fitted", calibrator_sha="cal", predicted_at="2026-10-01T10:00:00Z",
                feature_cutoff_at="2026-10-01T09:00:00Z", event_start_at="2026-10-01T12:00:00Z",
                source_manifest_sha="src", outcome_source_sha="settled", probability=0.6, outcome=1)

def run(rows, state="SHADOW", gates=None, market="ML"):
    return mod.evaluate({"routes": [{"sport":"NFL", "market":market, "state":state,
                                     "predictions":rows, "gates":gates or {}}]})["routes"][0]

def test_valid_grade_not_published():
    result = run([row()])
    assert result["graded_count"] == 1
    assert abs(result["brier"] - 0.16) < 1e-10
    assert result["gate_evidence"] == [False,False,False]
    assert result["verified_state"] == "SHADOW"

def test_missing_provenance_fail_closed():
    r = row()
    del r["model_artifact_sha"]
    result = run([r], "PRODUCTION_VERIFIED")
    assert result["graded_count"] == 0
    assert result["verified_state"] == "CERTIFICATION_HOLD"
    assert "MISSING_IMMUTABLE_PROVENANCE" in result["errors"]

def test_duplicate_does_not_inflate_grades():
    result = run([row(), row()])
    assert result["graded_count"] == 1
    assert "MISSING_OR_DUPLICATE_PREDICTION_IDENTITY" in result["errors"]

def test_no_lookahead():
    r = row()
    r["feature_cutoff_at"] = "2026-10-01T13:00:00Z"
    assert run([r])["graded_count"] == 0

def test_spread_requires_line_and_settlement():
    assert "SPREAD_LINE_OR_SETTLEMENT_MISSING" in run([row()], market="SPREAD")["errors"]

def test_prop_requires_contract():
    assert "PROP_CONTRACT_MISSING" in run([row()], market="PROP")["errors"]

def test_unverified_action_blocks_publication():
    gates = {k:True for k in ("statistical_validation","fitted_artifact_certified",
             "calibrator_certified","frozen_replay_verified","specialist_certified",
             "terminal_reducer_verified","deployed_sha_verified","independent_release_verified")}
    result = run([row()], "PRODUCTION_VERIFIED", gates)
    assert result["gate_evidence"] == [False,False,False]
    assert result["verified_state"] == "CERTIFICATION_HOLD"

def test_read_only_contract():
    output = mod.evaluate({"routes":[]})
    assert output["can_execute"] is False
    assert output["terminal_authority"] == "V17_TERMINAL_REDUCER"

def test_all_self_asserted_flags_cannot_certify():
    flags = {k: True for k in ("statistical_validation", "fitted_artifact_certified",
             "calibrator_certified", "frozen_replay_verified", "specialist_certified",
             "genuine_gpt_action_verified", "terminal_reducer_verified",
             "deployed_sha_verified", "independent_release_verified")}
    result = run([row()], "PRODUCTION_VERIFIED", flags)
    assert result["gate_evidence"] == [False, False, False]
    assert result["verified_state"] == "CERTIFICATION_HOLD"
    assert "UNSUPPORTED_PRODUCTION_CLAIM" in result["errors"]
