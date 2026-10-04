import pytest

from kalshi_weather_v2.probability_change_ledger import (
    AttributionDomain,
    ProbabilityAttributionComponent,
    ProbabilityChangeLedgerError,
    ProbabilityChangeRecord,
    build_probability_change_record,
)


def component(component_id="station", **overrides):
    values = dict(
        component_id=component_id,
        domain=AttributionDomain.STATION_TRAJECTORY,
        label="Station trajectory update",
        delta_probability=0.028,
        evidence_ids=("asos-kdfw-20261004-1600",),
        available_at="2026-10-04T16:01:00Z",
        method="POINT_IN_TIME_RECOMPUTE_V1",
    )
    values.update(overrides)
    return ProbabilityAttributionComponent(**values)


def record(**overrides):
    components = overrides.pop(
        "components",
        (
            component(delta_probability=0.028),
            component(
                "cloud",
                domain=AttributionDomain.WEATHER_EVIDENCE,
                label="Cloud clearing",
                delta_probability=0.014,
                evidence_ids=("nws-grid-20261004-1602",),
                available_at="2026-10-04T16:03:00Z",
            ),
            component(
                "calibration",
                domain=AttributionDomain.CALIBRATION,
                label="Calibration adjustment",
                delta_probability=0.001,
                evidence_ids=("calibration-profile-17",),
                available_at="2026-10-04T15:00:00Z",
            ),
        ),
    )
    values = dict(
        ticker="KXHIGHDFW-TEST",
        previous_prediction_id="prediction-before",
        current_prediction_id="prediction-after",
        before_decision_time="2026-10-04T15:55:00Z",
        after_decision_time="2026-10-04T16:05:00Z",
        p_yes_before=0.720,
        p_yes_after=0.763,
        components=components,
        market_context_snapshot_ids=("market-snapshot-1",),
    )
    values.update(overrides)
    return build_probability_change_record(**values)


def test_probability_change_reconciles_exact_attribution():
    item = record()
    assert item.total_delta == pytest.approx(0.043)
    assert item.attribution_total == pytest.approx(0.043)
    assert item.can_execute is False
    assert item.persistence_row()["p_yes_after"] == pytest.approx(0.763)


def test_market_state_cannot_be_weather_probability_attribution():
    with pytest.raises(ProbabilityChangeLedgerError, match="ATTRIBUTION_DOMAIN_PROHIBITED:MARKET_STATE"):
        component(domain=AttributionDomain.MARKET_STATE)


def test_execution_friction_cannot_be_weather_probability_attribution():
    with pytest.raises(ProbabilityChangeLedgerError, match="ATTRIBUTION_DOMAIN_PROHIBITED:EXECUTION_FRICTION"):
        component(domain=AttributionDomain.EXECUTION_FRICTION)


def test_market_price_flag_fails_closed():
    with pytest.raises(ProbabilityChangeLedgerError, match="MARKET_PRICE_WEATHER_INPUT_PROHIBITED"):
        component(market_price_used_as_weather_input=True)


def test_attribution_requires_evidence():
    with pytest.raises(ProbabilityChangeLedgerError, match="ATTRIBUTION_EVIDENCE_MISSING"):
        component(evidence_ids=())


def test_unexplained_probability_delta_fails_closed():
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_ATTRIBUTION_MISMATCH"):
        record(components=(component(delta_probability=0.010),))


def test_future_evidence_fails_point_in_time_reconciliation():
    with pytest.raises(ProbabilityChangeLedgerError, match="ATTRIBUTION_FUTURE_EVIDENCE"):
        record(components=(component(delta_probability=0.043, available_at="2026-10-04T16:06:00Z"),))


def test_probability_change_requires_strict_chronology():
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_TIME_ORDER_INVALID"):
        record(after_decision_time="2026-10-04T15:55:00Z")


@pytest.mark.parametrize("field,value", [("p_yes_before", 0.0), ("p_yes_before", 1.0), ("p_yes_after", 0.0), ("p_yes_after", 1.0)])
def test_probability_boundaries_are_strict(field, value):
    with pytest.raises(ProbabilityChangeLedgerError, match="INVALID"):
        record(**{field: value})


def test_component_identity_must_be_unique():
    duplicate = component("same", delta_probability=0.020)
    duplicate2 = component("same", delta_probability=0.023)
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_COMPONENT_ID_DUPLICATE"):
        record(components=(duplicate, duplicate2))


def test_identity_is_deterministic_and_component_order_independent():
    a = component("a", delta_probability=0.020)
    b = component("b", delta_probability=0.023)
    first = record(components=(a, b))
    second = record(components=(b, a))
    assert first.probability_change_id == second.probability_change_id


def test_market_context_is_recorded_but_not_attributed():
    item = record(market_context_snapshot_ids=("market-2", "market-1"))
    row = item.persistence_row()
    assert row["market_context_snapshot_ids"] == ["market-1", "market-2"]
    assert all(part["domain"] != "MARKET_STATE" for part in row["attribution_components"])


def test_direct_record_rejects_forged_identity():
    valid = record()
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_IDENTITY_MISMATCH"):
        ProbabilityChangeRecord(
            probability_change_id="forged",
            ticker=valid.ticker,
            previous_prediction_id=valid.previous_prediction_id,
            current_prediction_id=valid.current_prediction_id,
            before_decision_time=valid.before_decision_time,
            after_decision_time=valid.after_decision_time,
            p_yes_before=valid.p_yes_before,
            p_yes_after=valid.p_yes_after,
            components=valid.components,
            market_context_snapshot_ids=valid.market_context_snapshot_ids,
        )


def test_execution_capability_is_prohibited():
    valid = record()
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_EXECUTION_PROHIBITED"):
        ProbabilityChangeRecord(
            probability_change_id=valid.probability_change_id,
            ticker=valid.ticker,
            previous_prediction_id=valid.previous_prediction_id,
            current_prediction_id=valid.current_prediction_id,
            before_decision_time=valid.before_decision_time,
            after_decision_time=valid.after_decision_time,
            p_yes_before=valid.p_yes_before,
            p_yes_after=valid.p_yes_after,
            components=valid.components,
            market_context_snapshot_ids=valid.market_context_snapshot_ids,
            can_execute=True,
        )


def test_invalid_attribution_domain_fails_closed():
    with pytest.raises(ProbabilityChangeLedgerError, match="ATTRIBUTION_DOMAIN_INVALID"):
        component(domain="UNSUPPORTED_DOMAIN")


def test_reconciliation_tolerance_is_part_of_immutable_identity():
    default = record()
    looser = record(reconciliation_tolerance=1e-8)
    assert default.probability_change_id != looser.probability_change_id


@pytest.mark.parametrize(
    "overrides,error_code",
    [
        ({"component_id": ""}, "ATTRIBUTION_COMPONENT_ID_MISSING"),
        ({"label": ""}, "ATTRIBUTION_LABEL_MISSING"),
        ({"method": ""}, "ATTRIBUTION_METHOD_MISSING"),
        ({"evidence_ids": ("dup", "dup")}, "ATTRIBUTION_EVIDENCE_DUPLICATE"),
        ({"delta_probability": float("nan")}, "ATTRIBUTION_DELTA_INVALID"),
        ({"delta_probability": True}, "ATTRIBUTION_DELTA_INVALID"),
        ({"available_at": "not-a-time"}, "ATTRIBUTION_AVAILABLE_AT_INVALID"),
        ({"available_at": "2026-10-04T16:01:00"}, "ATTRIBUTION_AVAILABLE_AT_TIMEZONE_REQUIRED"),
        ({"can_execute": True}, "ATTRIBUTION_EXECUTION_PROHIBITED"),
    ],
)
def test_attribution_component_malformed_inputs_fail_closed(overrides, error_code):
    with pytest.raises(ProbabilityChangeLedgerError, match=error_code):
        component(**overrides)


def test_prediction_identity_conflict_fails_closed():
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_PREDICTION_IDENTITY_CONFLICT"):
        record(current_prediction_id="prediction-before")


def test_probability_change_requires_attribution_components():
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_ATTRIBUTION_MISSING"):
        record(components=())


@pytest.mark.parametrize(
    "market_ids,error_code",
    [
        (("",), "MARKET_CONTEXT_ID_INVALID"),
        (("same", "same"), "MARKET_CONTEXT_ID_DUPLICATE"),
    ],
)
def test_market_context_identity_validation(market_ids, error_code):
    with pytest.raises(ProbabilityChangeLedgerError, match=error_code):
        record(market_context_snapshot_ids=market_ids)


def test_negative_reconciliation_tolerance_fails_closed():
    with pytest.raises(ProbabilityChangeLedgerError, match="PROBABILITY_CHANGE_TOLERANCE_INVALID"):
        record(reconciliation_tolerance=-1e-9)


@pytest.mark.parametrize(
    "field,value,error_code",
    [
        ("before_decision_time", "not-a-time", "PROBABILITY_CHANGE_BEFORE_TIME_INVALID"),
        ("after_decision_time", "not-a-time", "PROBABILITY_CHANGE_AFTER_TIME_INVALID"),
        ("before_decision_time", "2026-10-04T15:55:00", "PROBABILITY_CHANGE_BEFORE_TIME_TIMEZONE_REQUIRED"),
        ("after_decision_time", "2026-10-04T16:05:00", "PROBABILITY_CHANGE_AFTER_TIME_TIMEZONE_REQUIRED"),
    ],
)
def test_probability_change_timestamp_validation(field, value, error_code):
    with pytest.raises(ProbabilityChangeLedgerError, match=error_code):
        record(**{field: value})


def test_evidence_union_is_sorted_and_deduplicated_across_components():
    a = component("a", delta_probability=0.020, evidence_ids=("e2", "e1"))
    b = component("b", delta_probability=0.023, evidence_ids=("e2", "e3"))
    item = record(components=(a, b))
    assert item.attribution_evidence_ids == ("e1", "e2", "e3")
