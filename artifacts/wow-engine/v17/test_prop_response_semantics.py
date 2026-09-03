from types import SimpleNamespace

from v17.prop_response_semantics import (
    _dimensioned_reconciliation,
    _direction_assessment,
    _qualification_payload,
)


def _cal(p, lb, ub):
    return SimpleNamespace(
        calibrated_probability=p,
        lower_bound=lb,
        upper_bound=ub,
        calibration_status="PRECALIBRATION_SHRINKAGE",
    )


def test_both_directions_receive_explicit_model_assessments():
    more = _direction_assessment("MORE", 0.61, _cal(0.5923, 0.5861, 0.6500), True)
    less = _direction_assessment("LESS", 0.39, _cal(0.4077, 0.3500, 0.4139), True)
    assert more["model_qualified"] is True
    assert more["confidence_tier"] == "QUALIFIED"
    assert more["probability_rank_eligible"] is True
    assert less["model_qualified"] is False
    assert more["value_qualification_status"] == "PENDING_EXACT_PRICE"
    assert more["card_qualification_status"] == "NOT_EVALUATED"


def test_model_qualification_survives_missing_market_and_payout():
    row = SimpleNamespace(
        calibrated_probability=0.5923,
        calibrated_probability_lower_bound=0.5861,
        calibrated_probability_upper_bound=0.6500,
        calibration_status="PRECALIBRATION_SHRINKAGE",
        data_gaps=[],
        probability_publishable=True,
    )
    payload = _qualification_payload(
        row,
        {"status": "HOLD"},
        {"status": "HOLD"},
        {"status": "HOLD"},
    )
    assert payload["model_qualified"] is True
    assert payload["model_qualification_status"] == "MODEL_QUALIFIED"
    assert payload["probability_rank_eligible"] is True
    assert payload["value_qualification_status"] == "PENDING_EXACT_PRICE"
    assert payload["card_qualification_status"] == "NOT_EVALUATED"
    assert payload["terminal_label"] == "MODEL_QUALIFIED_HOLD"


def test_reconciliation_dimensions_are_not_presented_as_a_funnel():
    rows = [
        {
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "model_evaluated": True,
            "model_qualified": True,
            "probability_rank_eligible": True,
            "value_qualification_status": "PENDING_EXACT_PRICE",
            "result": {"prediction": {"calibrated_probability": 0.61, "calibrated_probability_lower_bound": 0.58, "calibrated_probability_upper_bound": 0.66}},
        },
        {
            "terminal_label": "MODEL_INPUTS_INSUFFICIENT",
            "verdict_class": "ACQUISITION_BLOCKED",
            "model_evaluated": False,
            "probability_rank_eligible": False,
        },
        {
            "terminal_label": "MODEL_UNAVAILABLE",
            "verdict_class": "CAPABILITY_BLOCKED",
            "model_evaluated": False,
            "probability_rank_eligible": False,
        },
    ]
    rec = _dimensioned_reconciliation(rows)
    assert rec["rows_in"] == 3
    assert rec["scoring_completed"] == 1
    assert rec["valid_probability_packages"] == 1
    assert rec["model_qualified_rows"] == 1
    assert rec["probability_rank_eligible_rows"] == 1
    assert rec["input_or_identity_failures"] == 1
    assert rec["model_capability_failures"] == 1
    assert rec["dimensions_are_orthogonal_not_a_funnel"] is True
