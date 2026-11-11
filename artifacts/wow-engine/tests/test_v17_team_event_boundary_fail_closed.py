"""Fail-closed contract at the governed team-event batch boundary.

These checks are plumbing, not probability behavior: a package that is already
numerically unusable must not be promoted to COMPLETED, publishable or
rank-eligible, and a point/ranking value must never stand in for a calibrated
lower bound (ENGINE_ALIGNMENT_CONTRACT: probability_domain_strict,
point_estimate_is_not_lower_bound).
"""
import math

import pytest

from team_event_request_runtime import (
    TeamEventRequestRow,
    _completed,
    _completed_nfl,
    _completed_registered,
)

EVENT = {"home_team": "HOME", "away_team": "AWAY"}


def _row(sport="MLB", lane="OUTRIGHT_WIN_PROBABILITY"):
    return TeamEventRequestRow(
        research_run_id="boundary-contract-test",
        objective_lane=lane,
        sport=sport,
        league=sport,
        event_key=f"{sport}:EVT-1",
        event_state="PREGAME",
        event_date="2026-11-05",
        timezone="America/Chicago",
        price_required_for_objective=False,
        event_start_time_utc="2026-11-06T00:00:00Z",
        home_team="HOME",
        away_team="AWAY",
    )


def _mlb_package(**overrides):
    package = {
        "calibrated_home_probability": 0.62,
        "calibrated_away_probability": 0.38,
        "calibrated_home_lower_bound": 0.55,
        "calibrated_away_lower_bound": 0.31,
        "calibrated_home_upper_bound": 0.69,
        "calibrated_away_upper_bound": 0.45,
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
    }
    package.update(overrides)
    return package


def _registered_package(**overrides):
    package = {
        "calibrated_probability": 0.61,
        "calibrated_lower_bound": 0.54,
        "calibrated_upper_bound": 0.68,
        "selected_participant": "HOME",
        "probability_publishable": True,
        "rank_eligible": True,
        "can_execute": False,
    }
    package.update(overrides)
    return package


def _nfl_package(**overrides):
    package = {
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "terminal_label": "FINAL_APPROVED",
        "selected_participant": "HOME",
        "calibrated_selection_probability": 0.61,
        "rank_calibrated_lower_bound": 0.55,
        "calibrated_home_upper_bound": 0.69,
        "calibrated_away_upper_bound": 0.45,
        "probability_publishable": True,
        "can_execute": False,
    }
    package.update(overrides)
    return package


def test_valid_packages_still_complete_with_execution_disabled():
    mlb = _completed(_row(), EVENT, _mlb_package())
    registered = _completed_registered(_row("NHL"), _registered_package())
    nfl = _completed_nfl(_row("NFL"), _nfl_package())

    for outcome in (mlb, registered, nfl):
        assert outcome["terminal_status"] == "COMPLETED"
        assert outcome["probability_publishable"] is True
        assert outcome["objective_rank_eligible"] is True
        assert outcome["can_execute"] is False


def test_closed_unit_interval_endpoints_are_output_invalid():
    for endpoint in (0.0, 1.0):
        held = _completed(
            _row(), EVENT, _mlb_package(calibrated_home_probability=endpoint)
        )
        assert held["terminal_status"] == "HELD"
        assert held["code"] == "MODEL_OUTPUT_INVALID"
        assert "GOVERNED_PROBABILITY_FIELDS_INVALID" in held["blockers"]
        assert held["probability_publishable"] is False
        assert held["rank_eligible"] is False
        assert held["can_execute"] is False

        registered = _completed_registered(
            _row("NHL"), _registered_package(calibrated_probability=endpoint)
        )
        assert registered["terminal_status"] == "HELD"
        assert registered["code"] == "MODEL_OUTPUT_INVALID"
        assert registered["can_execute"] is False


def test_out_of_domain_bounds_are_output_invalid():
    for field in (
        "calibrated_home_lower_bound",
        "calibrated_home_upper_bound",
        "calibrated_away_lower_bound",
        "calibrated_away_upper_bound",
    ):
        held = _completed(_row(), EVENT, _mlb_package(**{field: -0.01}))
        assert held["terminal_status"] == "HELD", field
        assert held["code"] == "MODEL_OUTPUT_INVALID", field
        assert held["can_execute"] is False


def test_inconsistent_bounds_are_output_invalid():
    # A lower bound above its own point estimate is not a usable package.
    held = _completed(
        _row(), EVENT, _mlb_package(calibrated_home_lower_bound=0.70)
    )
    assert held["terminal_status"] == "HELD"
    assert held["code"] == "MODEL_OUTPUT_INVALID"
    assert "GOVERNED_PROBABILITY_BOUNDS_INCONSISTENT" in held["blockers"]
    assert held["probability_publishable"] is False
    assert held["rank_eligible"] is False

    registered = _completed_registered(
        _row("NHL"), _registered_package(calibrated_upper_bound=0.50)
    )
    assert registered["terminal_status"] == "HELD"
    assert "GOVERNED_PROBABILITY_BOUNDS_INCONSISTENT" in registered["blockers"]


def test_nfl_point_estimate_is_not_accepted_as_calibrated_lower_bound():
    package = _nfl_package()
    del package["rank_calibrated_lower_bound"]
    package["ranked_probability"] = 0.55

    held = _completed_nfl(_row("NFL"), package)
    assert held["terminal_status"] == "HELD"
    assert held["code"] == "MODEL_OUTPUT_INVALID"
    assert "NFL_GOVERNED_PROBABILITY_FIELDS_INVALID" in held["blockers"]
    assert held["probability_publishable"] is False
    assert held["rank_eligible"] is False
    assert held["can_execute"] is False


def test_nfl_explicit_selection_lower_bound_is_accepted():
    package = _nfl_package()
    del package["rank_calibrated_lower_bound"]
    package["calibrated_selection_lower_bound"] = 0.55

    outcome = _completed_nfl(_row("NFL"), package)
    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["calibrated_lower_bound"] == 0.55
    assert outcome["can_execute"] is False


PROBABILITY_FIELDS = (
    "calibrated_home_probability",
    "calibrated_away_probability",
    "calibrated_home_lower_bound",
    "calibrated_away_lower_bound",
    "calibrated_home_upper_bound",
    "calibrated_away_upper_bound",
)


@pytest.mark.parametrize("field", PROBABILITY_FIELDS)
@pytest.mark.parametrize(
    "invalid",
    [math.nan, math.inf, -math.inf, True, False, "0.5", None, -0.01, 1.01],
)
def test_non_numeric_non_finite_and_out_of_domain_values_are_held(field, invalid):
    held = _completed(_row(), EVENT, _mlb_package(**{field: invalid}))
    assert held["terminal_status"] == "HELD", field
    assert held["code"] == "MODEL_OUTPUT_INVALID", field
    assert held["probability_publishable"] is False
    assert held["rank_eligible"] is False
    assert held.get("objective_rank_eligible", False) is False
    assert held["can_execute"] is False


def test_registered_away_selection_publishes_the_away_side_pair():
    # Generic calibrated_* is home-oriented; an away selection must use the
    # away pair, matching the SQL/TS governed-selection lookups.
    package = _registered_package(
        selected_participant="AWAY",
        calibrated_away_probability=0.41,
        calibrated_away_lower_bound=0.34,
        calibrated_away_upper_bound=0.48,
    )
    outcome = _completed_registered(_row("NHL"), package)
    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["selected_team"] == "AWAY"
    assert outcome["calibrated_probability"] == 0.41
    assert outcome["calibrated_lower_bound"] == 0.34
    assert outcome["calibrated_upper_bound"] == 0.48
    assert outcome["can_execute"] is False


def test_registered_away_selection_without_away_pair_is_never_published():
    held = _completed_registered(
        _row("NHL"), _registered_package(selected_participant="AWAY")
    )
    assert held["terminal_status"] == "HELD"
    assert held["code"] == "MODEL_OUTPUT_INVALID"
    assert "TEAM_EVENT_SELECTED_SIDE_PROBABILITY_MISSING" in held["blockers"]
    assert held["probability_publishable"] is False
    assert held["can_execute"] is False


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, True, "0.6", None])
def test_registered_package_rejects_invalid_point_estimate(invalid):
    held = _completed_registered(
        _row("NHL"), _registered_package(calibrated_probability=invalid)
    )
    assert held["terminal_status"] == "HELD"
    assert held["code"] == "MODEL_OUTPUT_INVALID"
    assert held["can_execute"] is False


def test_nfl_explicit_rank_ineligibility_is_preserved():
    outcome = _completed_nfl(_row("NFL"), _nfl_package(rank_eligible=False))
    assert outcome["terminal_status"] == "COMPLETED"
    assert outcome["probability_rank_eligible"] is False
    assert outcome["objective_rank_eligible"] is False
    assert outcome["can_execute"] is False


def test_nfl_absent_rank_eligible_default_is_characterized_not_silently_changed():
    # Documented current contract: the NFL publication bridge always emits an
    # explicit rank_eligible (True or False), so this default is only reachable
    # by bypassing that bridge. It stays True until a governed ranking decision
    # changes it; this test exists to make any change deliberate.
    package = _nfl_package()
    package.pop("rank_eligible", None)
    outcome = _completed_nfl(_row("NFL"), package)
    assert outcome["probability_rank_eligible"] is True
    assert outcome["objective_rank_eligible"] is True


def test_nfl_out_of_domain_probability_is_output_invalid():
    held = _completed_nfl(
        _row("NFL"), _nfl_package(calibrated_selection_probability=1.0)
    )
    assert held["terminal_status"] == "HELD"
    assert held["code"] == "MODEL_OUTPUT_INVALID"
    assert "NFL_GOVERNED_PROBABILITY_DOMAIN_INVALID" in held["blockers"]
    assert held["can_execute"] is False
