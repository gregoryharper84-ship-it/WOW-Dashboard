from __future__ import annotations

import pytest

from v17.full_model_card_admission import (
    admit_full_model_candidate,
    admit_full_model_card,
)


def _candidate(candidate_id: str, *, lane: str = "TEAM_EVENT_ML", sport: str = "NCAAF", **extra):
    row = {
        "candidate_id": candidate_id,
        "lane": lane,
        "sport": sport,
        "controlling_specialist": "wow.test.specialist",
    }
    row.update(extra)
    return row


def _ready(**extra):
    state = {
        "route_supported": True,
        "lane_status": "FULL_MODEL_GOVERNED",
        "probability_publishable": True,
        "controlling_specialist": "wow.test.specialist",
    }
    state.update(extra)
    return state


def _scored(**extra):
    payload = {
        "model_probability": 0.72,
        "unconditional_probability": 0.70,
        "calibrated_probability": 0.68,
        "calibrated_lower_bound": 0.64,
        "calibrated_upper_bound": 0.72,
        "rank_eligible": True,
        "model_qualified": True,
        "probability_publishable": True,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "market_status": "NO_MARKET",
    }
    payload.update(extra)
    return payload


def test_ncaaf_test_only_external_998_cannot_enter_full_model_card():
    row = _candidate("byu-utah-tech", external_probability=0.998)
    readiness = _ready(
        route_supported=False,
        lane_status="NCAAF_TEST_ONLY",
        probability_publishable=False,
        ncaaf_controlling_model="MODEL_UNAVAILABLE",
        blockers=[
            "CFBD_API_KEY_MISSING",
            "NCAAF_HISTORICAL_SOURCE_SNAPSHOTS_EMPTY",
            "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "NCAAF_CERTIFIED_CALIBRATOR_NOT_FOUND",
        ],
    )

    decision = admit_full_model_candidate(row, readiness=readiness, scorer_result=None)

    assert decision.external_research_present is True
    assert decision.admitted_to_probability_card is False
    assert decision.terminal_label == "MODEL_UNAVAILABLE"
    assert decision.governed_probability_package == {}
    assert "NCAAF_TEST_ONLY" in decision.blockers
    assert "ROUTE_CAPABILITY_UNAVAILABLE" in decision.blockers
    assert decision.can_execute is False


def test_unsupported_mls_external_projection_cannot_be_relabelled_governed():
    row = _candidate(
        "inter-miami-atlanta",
        sport="SOCCER",
        external_probability=0.773,
        market_probability=0.71,
    )
    readiness = _ready(route_supported=False, model_capability="MODEL_UNAVAILABLE")

    decision = admit_full_model_candidate(row, readiness=readiness, scorer_result=None)

    assert decision.admitted_to_probability_card is False
    assert decision.governed_probability_package == {}
    assert decision.sporting_probability_preserved is False
    assert decision.terminal_label == "MODEL_UNAVAILABLE"


def test_external_probability_never_populates_governed_probability_fields():
    row = _candidate(
        "external-only",
        sport="SOCCER",
        external_probability=0.91,
        external_lower_bound=0.87,
        no_vig_probability=0.88,
    )

    decision = admit_full_model_candidate(row, readiness=_ready(), scorer_result=None)

    assert decision.external_research_present is True
    assert decision.governed_probability_package == {}
    assert decision.admitted_to_probability_card is False
    assert decision.typed_failure == "LIVE_GPT_ACTION_INVOCATION_BLOCKED"


def test_valid_backend_package_is_required_for_probability_card_admission():
    row = _candidate("mlb-event", sport="MLB")

    decision = admit_full_model_candidate(row, readiness=_ready(), scorer_result=_scored())

    assert decision.admitted_to_probability_card is True
    assert decision.governed_probability_package["calibrated_lower_bound"] == pytest.approx(0.64)
    assert decision.governed_probability_package["rank_eligible"] is True
    assert decision.can_execute is False


def test_rank_ineligible_sporting_probability_is_preserved_but_not_admitted():
    row = _candidate("prop-calibration-held", lane="PROP", sport="MLB")
    scorer = _scored(
        calibrated_probability=0.74,
        calibrated_lower_bound=0.69,
        calibrated_upper_bound=0.78,
        rank_eligible=False,
        model_qualified=False,
        terminal_label="MODEL_QUALIFIED_HOLD",
        market_status="NO_MARKET",
    )

    decision = admit_full_model_candidate(row, readiness=_ready(), scorer_result=scorer)

    assert decision.sporting_probability_preserved is True
    assert decision.admitted_to_probability_card is False
    assert decision.governed_probability_package["calibrated_probability"] == pytest.approx(0.74)
    assert "RANK_ELIGIBILITY_BLOCKED" in decision.blockers


def test_missing_market_does_not_erase_valid_sporting_probability_or_rank_admission():
    row = _candidate("mlb-no-market", sport="MLB")
    scorer = _scored(market_status="NO_MARKET")

    decision = admit_full_model_candidate(row, readiness=_ready(), scorer_result=scorer)

    assert decision.sporting_probability_preserved is True
    assert decision.admitted_to_probability_card is True
    assert decision.market_status == "NO_MARKET"
    assert decision.governed_probability_package["calibrated_lower_bound"] == pytest.approx(0.64)


def test_scorer_failure_is_preserved_and_not_rewritten_as_model_unavailable():
    row = _candidate("scorer-failed", lane="PROP", sport="MLB")
    scorer = {
        "terminal_label": "MODEL_SCORER_FAILED",
        "failure_class": "MODEL_SCORER_FAILED",
        "blockers": ["SCORER_TIMEOUT"],
    }

    decision = admit_full_model_candidate(row, readiness=_ready(), scorer_result=scorer)

    assert decision.admitted_to_probability_card is False
    assert decision.typed_failure == "MODEL_SCORER_FAILED"
    assert decision.terminal_label == "MODEL_SCORER_FAILED"
    assert decision.terminal_label != "MODEL_UNAVAILABLE"
    assert "SCORER_TIMEOUT" in decision.blockers


def test_prop_forward_cohort_rank_hold_does_not_let_external_hit_rate_into_card():
    row = _candidate(
        "gray-k-3.5",
        lane="PROP",
        sport="MLB",
        external_probability=0.80,
        recent_hit_rate=0.90,
    )
    readiness = _ready(
        lane_status="PHASE_A_FORWARD_COHORT_BUILDING",
        probability_publishable=False,
        blockers=["PROP_CALIBRATION_HEALTH:PHASE_A_FORWARD_COHORT_BUILDING"],
    )
    scorer = _scored(
        calibrated_probability=0.78,
        calibrated_lower_bound=None,
        calibrated_upper_bound=None,
        rank_eligible=False,
        model_qualified=False,
        probability_publishable=False,
        terminal_label="MODEL_QUALIFIED_HOLD",
    )

    decision = admit_full_model_candidate(row, readiness=readiness, scorer_result=scorer)

    assert decision.sporting_probability_preserved is True
    assert decision.admitted_to_probability_card is False
    assert decision.governed_probability_package["model_probability"] == pytest.approx(0.72)
    assert decision.governed_probability_package["model_probability"] != pytest.approx(0.80)
    assert "CALIBRATED_LOWER_BOUND_UNAVAILABLE" in decision.blockers
    assert "PROBABILITY_PUBLICATION_BLOCKED" in decision.blockers


def test_probability_publishable_false_blocks_admission_but_preserves_sporting_probability():
    row = _candidate("publish-held", sport="MLB")
    scorer = _scored(
        probability_publishable=False,
        rank_eligible=True,
        calibrated_probability=0.71,
        calibrated_lower_bound=0.66,
    )

    decision = admit_full_model_candidate(row, readiness=_ready(), scorer_result=scorer)

    assert decision.sporting_probability_preserved is True
    assert decision.admitted_to_probability_card is False
    assert "PROBABILITY_PUBLICATION_BLOCKED" in decision.blockers


def test_calibrated_only_payload_without_sporting_probability_is_rejected():
    row = _candidate("calibrated-only", sport="MLB")
    scorer = _scored(
        model_probability=None,
        unconditional_probability=None,
        calibrated_probability=0.72,
        calibrated_lower_bound=0.67,
        calibrated_upper_bound=0.76,
        probability_publishable=True,
        rank_eligible=True,
    )

    decision = admit_full_model_candidate(row, readiness=_ready(), scorer_result=scorer)

    assert decision.admitted_to_probability_card is False
    assert "MODEL_PROBABILITY_UNAVAILABLE" in decision.blockers


def test_readiness_publication_hold_blocks_even_if_row_payload_claims_rank_eligible():
    row = _candidate("readiness-held", sport="MLB")
    readiness = _ready(probability_publishable=False, blockers=["CALIBRATION_HEALTH_HOLD"])

    decision = admit_full_model_candidate(row, readiness=readiness, scorer_result=_scored())

    assert decision.sporting_probability_preserved is True
    assert decision.admitted_to_probability_card is False
    assert "CALIBRATION_HEALTH_HOLD" in decision.blockers
    assert "PROBABILITY_PUBLICATION_BLOCKED" in decision.blockers


def test_batch_reconciles_every_candidate_exactly_once():
    rows = [
        _candidate("a", sport="MLB"),
        _candidate("b", sport="MLB"),
        _candidate("c", sport="SOCCER", external_probability=0.90),
    ]
    batch = admit_full_model_card(
        rows,
        readiness_by_candidate={
            "a": _ready(),
            "b": _ready(),
            "c": _ready(route_supported=False, model_capability="MODEL_UNAVAILABLE"),
        },
        scorer_results_by_candidate={
            "a": _scored(),
            "b": _scored(rank_eligible=False),
        },
    )

    assert batch.rows_in == 3
    assert batch.rows_admitted == 1
    assert batch.rows_excluded == 2
    assert batch.rows_in == batch.rows_admitted + batch.rows_excluded
    assert batch.admitted_candidate_ids == ("a",)
    assert set(batch.excluded_candidate_ids) == {"b", "c"}
    assert batch.can_execute is False


def test_batch_rejects_duplicate_candidate_ids():
    rows = [_candidate("dup", sport="MLB"), _candidate("dup", sport="MLB")]

    with pytest.raises(ValueError, match="DUPLICATE_CANDIDATE_ID"):
        admit_full_model_card(
            rows,
            readiness_by_candidate={"dup": _ready()},
            scorer_results_by_candidate={"dup": _scored()},
        )
