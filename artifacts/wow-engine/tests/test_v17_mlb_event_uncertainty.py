import numpy as np
import pytest

from v17.mlb_event_uncertainty import (
    cohort_reliability_interval,
    event_uncertainty_from_bootstrap,
)


def test_cohort_reliability_is_typed_separately_from_event_uncertainty():
    cohort = cohort_reliability_interval(
        method="LOCAL_DECILE_WILSON_95",
        cohort_n=69,
        lower=0.39,
        upper=0.62,
        calibration_start="2024-08-10",
        calibration_end="2024-09-30",
    )
    reps = np.linspace(0.48, 0.66, 500)
    event = event_uncertainty_from_bootstrap(reps, point_probability=0.57)
    assert cohort.semantic_type == "HISTORICAL_COHORT_RELIABILITY_INTERVAL"
    assert event.semantic_type == "EVENT_SPECIFIC_MODEL_UNCERTAINTY"
    assert cohort.semantic_type != event.semantic_type
    assert cohort.can_execute is False and event.can_execute is False


def test_event_uncertainty_requires_real_bootstrap_depth():
    with pytest.raises(ValueError, match="SAMPLE_INSUFFICIENT"):
        event_uncertainty_from_bootstrap([0.5] * 20, point_probability=0.5)
