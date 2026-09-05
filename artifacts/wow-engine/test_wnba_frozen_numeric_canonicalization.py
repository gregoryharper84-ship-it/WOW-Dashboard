from __future__ import annotations

import pytest

from scripts.train_wnba_props_frozen import (
    CANONICAL_FLOAT_DECIMALS,
    canonicalize_artifact,
)


def _artifact(coef: float, metric: float) -> dict:
    return {
        "artifact_payload": {
            "coef": [coef],
            "intercept": 0.02259852023685281,
            "stat_type": "POINTS",
        },
        "validation_metrics": {
            "validation_status": "PASS",
            "candidate_metric": metric,
        },
        "can_execute": False,
        "probability_publishable": False,
        "active": False,
        "promoted": False,
    }


def test_sub_precision_solver_jitter_has_identical_canonical_artifact() -> None:
    left = canonicalize_artifact(
        _artifact(0.09130956635112032, 3.5696848872757703)
    )
    right = canonicalize_artifact(
        _artifact(0.09130956635112056, 3.569684887275771)
    )

    assert CANONICAL_FLOAT_DECIMALS == 12
    assert left["artifact_payload"] == right["artifact_payload"]
    assert left["validation_metrics"] == right["validation_metrics"]
    assert left["artifact_checksum"] == right["artifact_checksum"]
    assert left["numeric_canonicalization_decimals"] == 12
    assert left["can_execute"] is False
    assert left["probability_publishable"] is False


def test_nonfinite_fitted_value_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="WNBA_NONFINITE_FLOAT_CANNOT_BE_CANONICALIZED"):
        canonicalize_artifact(_artifact(float("inf"), 1.0))
