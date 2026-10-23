"""Regression tests: no hindsight attribution or wrong-line model wins."""
from __future__ import annotations

from copy import deepcopy

import pytest

from v17.postmortem_exact_thesis import match_exact_pregame_thesis


def records():
    common = {
        "sport": "MLB", "official_event_id": "mlb-2026-08-22-game-1",
        "participant": "Pitcher A", "market": "PITCHER_STRIKEOUTS",
        "period": "FULL_GAME", "direction": "MORE", "exact_line": 4.5,
        "settlement_basis": "OFFICIAL_MLB_STATS",
        "event_start_utc": "2026-08-22T20:10:00Z",
    }
    prediction = {
        **common, "prediction_id": "pred-123", "immutable_pregame": True,
        "model_timestamp": "2026-08-22T19:00:00+00:00",
        "created_at": "2026-08-22T19:01:00Z",
        "calibrated_probability": 0.68, "calibrated_lower_bound": 0.62,
    }
    settled = {**common, "official_result": "WIN"}
    return prediction, settled


def test_exact_scalar_match_and_no_mutation():
    prediction, settled = records()
    old = deepcopy(prediction), deepcopy(settled)
    result = match_exact_pregame_thesis(prediction, settled)
    assert result.status == "MATCHED_IMMUTABLE_PREGAME_PREDICTION"
    assert result.eligible_for_model_grading is True
    assert result.prediction_id == "pred-123"
    assert result.canonical_thesis_key is not None
    assert result.can_execute is False
    assert (prediction, settled) == old


@pytest.mark.parametrize("field,new", [
    ("sport", "NFL"), ("official_event_id", "mlb-2026-08-22-game-2"),
    ("participant", "Pitcher B"), ("market", "PITCHING_OUTS"),
    ("period", "FIRST_INNING"), ("direction", "LESS"),
    ("settlement_basis", "ALT_PROVIDER_SETTLEMENT"),
    ("exact_line", 3.5),
])
def test_materially_different_thesis_cannot_be_graded_as_win(field, new):
    prediction, settled = records()
    settled[field] = new
    result = match_exact_pregame_thesis(prediction, settled)
    assert result.eligible_for_model_grading is False
    assert result.prediction_id is None
    assert result.status == "EXACT_THESIS_IDENTITY_MISMATCH"


@pytest.mark.parametrize("field", ["official_event_id", "direction", "period", "settlement_basis", "exact_line"])
def test_missing_structured_identity_cannot_be_inferred_from_selection_text(field):
    prediction, settled = records()
    prediction[field] = None
    prediction["selection"] = "Pitcher A MORE 4.5 strikeouts"
    result = match_exact_pregame_thesis(prediction, settled)
    assert result.eligible_for_model_grading is False
    assert result.status == "EXACT_THESIS_EVIDENCE_INSUFFICIENT"


def test_duplicate_slips_one_model_thesis():
    prediction, settled = records()
    settled2 = {**settled, "slip_id": "other-card", "official_result": "LOSS"}
    first = match_exact_pregame_thesis(prediction, settled)
    second = match_exact_pregame_thesis(prediction, settled2)
    assert first.canonical_thesis_key == second.canonical_thesis_key


def test_line_numeric_forms_compare_as_the_exact_same_value():
    prediction, settled = records()
    prediction["exact_line"] = "4.50"
    assert match_exact_pregame_thesis(prediction, settled).eligible_for_model_grading


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "not-a-line"])
def test_non_numeric_and_nonfinite_line_is_not_attributed(bad):
    prediction, settled = records()
    settled["exact_line"] = bad
    assert not match_exact_pregame_thesis(prediction, settled).eligible_for_model_grading


@pytest.mark.parametrize("field", ["model_timestamp", "created_at"])
def test_after_start_prediction_cannot_be_retroactively_scored(field):
    prediction, settled = records()
    prediction[field] = "2026-08-22T20:11:00Z"
    result = match_exact_pregame_thesis(prediction, settled)
    assert result.status == "PREGAME_TIME_PROOF_INVALID"
    assert result.eligible_for_model_grading is False


def test_naive_timestamp_fails():
    prediction, settled = records()
    prediction["created_at"] = "2026-08-22T19:01:00"
    assert match_exact_pregame_thesis(prediction, settled).status == "PREGAME_TIME_PROOF_INVALID"


def test_event_start_change_blocks_attribution():
    prediction, settled = records()
    settled["event_start_utc"] = "2026-08-22T20:15:00Z"
    assert match_exact_pregame_thesis(prediction, settled).reason == "EVENT_START_IDENTITY_MISMATCH"


def test_missing_immutable_prediction_retains_outcome_only():
    prediction, settled = records()
    assert match_exact_pregame_thesis(None, settled).status == "MODEL_ATTRIBUTION_UNAVAILABLE"
    prediction["immutable_pregame"] = False
    assert not match_exact_pregame_thesis(prediction, settled).eligible_for_model_grading


def test_winner_line_is_absent_not_synthetic_zero():
    prediction, settled = records()
    for r in (prediction, settled):
        r.update({"market": "OUTRIGHT_WINNER", "direction": "WIN", "exact_line": None})
    assert match_exact_pregame_thesis(prediction, settled).eligible_for_model_grading
    settled["exact_line"] = 0
    assert not match_exact_pregame_thesis(prediction, settled).eligible_for_model_grading


def test_readonly_diagnostic_never_claims_terminal_authority():
    prediction, settled = records()
    outcome = match_exact_pregame_thesis(prediction, settled).as_dict()
    assert outcome["can_execute"] is False
    assert "terminal_label" not in outcome
