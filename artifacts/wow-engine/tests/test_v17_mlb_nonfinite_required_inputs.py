"""MLB prospective scorer must reject nonfinite numeric inputs before simulation."""
import math
import pytest

from mlb_event_specialist_v16 import ProspectiveModelUnavailable, _required_numeric_inputs
from v17.mlb_prospective_failure_taxonomy import (
    MODEL_INPUTS_INSUFFICIENT, classify_mlb_prospective_failure,
)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-Infinity"])
def test_required_model_input_rejects_nonfinite_values(bad):
    with pytest.raises(
        ProspectiveModelUnavailable,
        match="prospective_required_numeric_input_invalid:score.starting_pitcher_era",
    ) as caught:
        _required_numeric_inputs(
            {"starting_pitcher_era": bad}, ("starting_pitcher_era",), source="score"
        )
    result = classify_mlb_prospective_failure(caught.value)
    assert result.code == MODEL_INPUTS_INSUFFICIENT
    assert result.can_execute is False


def test_finite_negative_stat_and_ordinary_values_remain_valid():
    result = _required_numeric_inputs(
        {"point_diff": "-0.5", "runs": 4}, ("point_diff", "runs"), source="score"
    )
    assert result == {"point_diff": -0.5, "runs": 4.0}


def test_multiple_nonfinite_inputs_use_sorted_typed_failure():
    with pytest.raises(
        ProspectiveModelUnavailable,
        match="prospective_required_numeric_input_invalid:score.a,score.b",
    ):
        _required_numeric_inputs({"b": math.inf, "a": math.nan}, ("b", "a"), source="score")
