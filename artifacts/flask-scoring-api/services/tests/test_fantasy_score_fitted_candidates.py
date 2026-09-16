from __future__ import annotations

import pytest

from services.fantasy_score_fitted_candidates import (
    CAN_EXECUTE,
    MIN_STANDARD_SIMULATIONS,
    MLB_HITTER,
    MLB_PITCHER,
    NBA,
    WNBA,
    FantasyScoreCandidateError,
    candidate_parity_manifest,
    fit_candidate,
    simulate_exact_line,
)


def _rows(lane: str):
    rows = []
    for event in range(1, 13):
        for player_idx in range(2):
            base = {
                "event_date": f"2026-01-{event:02d}",
                "event_id": f"e{event}",
                "player_id": f"p{player_idx}",
                "position": "G" if player_idx == 0 else "F",
            }
            if lane in {NBA, WNBA}:
                base.update({
                    "points": 14 + event + player_idx,
                    "rebounds": 5 + player_idx,
                    "assists": 4 + (event % 3),
                    "steals": event % 2,
                    "blocks": player_idx,
                    "turnovers": 2 + (event % 2),
                })
            elif lane == MLB_HITTER:
                base.update({
                    "singles": 1 + (event % 2),
                    "doubles": event % 2,
                    "triples": 0,
                    "home_runs": 1 if event % 4 == 0 else 0,
                    "runs": 1,
                    "rbi": event % 3,
                    "walks": 1,
                    "hbp": 0,
                    "stolen_bases": 1 if event % 5 == 0 else 0,
                })
            else:
                base.update({
                    "wins": 1 if event % 2 == 0 else 0,
                    "quality_starts": 1 if event % 3 != 0 else 0,
                    "strikeouts": 4 + event % 5,
                    "outs_recorded": 15 + event % 5,
                    "earned_runs": event % 4,
                })
            rows.append(base)
    return rows


def _profile(lane: str):
    if lane in {NBA, WNBA}:
        weights = {
            "points": 1.0,
            "rebounds": 1.2,
            "assists": 1.5,
            "steals": 3.0,
            "blocks": 3.0,
            "turnovers": -1.0,
        }
    elif lane == MLB_HITTER:
        weights = {
            "singles": 3.0,
            "doubles": 5.0,
            "triples": 8.0,
            "home_runs": 10.0,
            "runs": 2.0,
            "rbi": 2.0,
            "walks": 2.0,
            "hbp": 2.0,
            "stolen_bases": 5.0,
        }
    else:
        weights = {
            "wins": 6.0,
            "quality_starts": 4.0,
            "strikeouts": 3.0,
            "outs_recorded": 1.0,
            "earned_runs": -3.0,
        }
    return {"profile_id": f"TEST_{lane}_PROFILE", "verified": True, "weights": weights}


def _context(lane: str):
    keys = {
        NBA: ("minutes_model", "pace_model", "usage_model"),
        WNBA: ("minutes_model", "pace_model", "usage_model"),
        MLB_HITTER: ("plate_appearance_model", "lineup_role_model", "opponent_pitching_model"),
        MLB_PITCHER: ("workload_model", "opponent_model", "bullpen_hook_model"),
    }[lane]
    return {"certified": True, **{key: {"status": "READY"} for key in keys}}


def _regimes(lane: str):
    return [{
        "name": "NORMAL_ROLE",
        "probability": 1.0,
        "component_multipliers": {},
        "certified": True,
    }]


@pytest.mark.parametrize("lane", [NBA, WNBA, MLB_HITTER, MLB_PITCHER])
def test_every_non_nfl_fantasy_lane_reaches_same_candidate_lifecycle_ceiling(lane):
    artifact = fit_candidate(_rows(lane), lane=lane, source_id=f"fixture:{lane}")
    metadata = artifact.as_metadata()

    assert metadata["certification_status"] == "CANDIDATE_ONLY"
    assert metadata["calibration_status"] == "BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT"
    assert metadata["probability_publishable"] is False
    assert metadata["rank_eligible"] is False
    assert metadata["can_execute"] is False
    assert artifact.split.train_end <= artifact.split.calibration_end <= artifact.split.test_end
    assert artifact.split.test


@pytest.mark.parametrize("lane", [NBA, WNBA, MLB_HITTER, MLB_PITCHER])
def test_candidate_scoring_is_exact_profile_bound_and_never_publishable(lane):
    artifact = fit_candidate(_rows(lane), lane=lane, source_id=f"fixture:{lane}")
    cohort = "G" if lane in {NBA, WNBA} else lane
    result = simulate_exact_line(
        artifact,
        player_id="p0",
        cohort=cohort,
        exact_line=25.5,
        side="MORE",
        scoring_profile=_profile(lane),
        simulation_count=MIN_STANDARD_SIMULATIONS,
        seed=11,
        opportunity_context=_context(lane),
        regimes=_regimes(lane),
    )

    assert 0 <= result["raw_candidate_probability"] <= 1
    assert abs(result["P(MORE)"] + result["P(LESS)"] + result["P(PUSH)"] - 1.0) < 1e-8
    assert result["calibrated_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert result["terminal_status"] == "CALIBRATION_BLOCKED_NO_PUBLISH"
    assert "BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT" in result["blockers"]
    assert "FITTED_MODEL_ARTIFACT_NOT_PROMOTED" in result["blockers"]


def test_candidate_requires_nfl_level_50k_simulation_floor():
    artifact = fit_candidate(_rows(NBA), lane=NBA, source_id="fixture:nba")
    with pytest.raises(FantasyScoreCandidateError, match="at least 50000"):
        simulate_exact_line(
            artifact,
            player_id="p0",
            cohort="G",
            exact_line=30.5,
            side="LESS",
            scoring_profile=_profile(NBA),
            simulation_count=49_999,
            opportunity_context=_context(NBA),
            regimes=_regimes(NBA),
        )


def test_scoring_profile_must_be_verified_exact_identity():
    artifact = fit_candidate(_rows(MLB_HITTER), lane=MLB_HITTER, source_id="fixture:mlb-hitter")
    profile = _profile(MLB_HITTER)
    profile["verified"] = False
    with pytest.raises(FantasyScoreCandidateError, match="explicitly verified"):
        simulate_exact_line(
            artifact,
            player_id="p0",
            cohort=MLB_HITTER,
            exact_line=7.5,
            side="MORE",
            scoring_profile=profile,
            opportunity_context=_context(MLB_HITTER),
            regimes=_regimes(MLB_HITTER),
        )


def test_parity_manifest_is_fail_closed_for_every_added_lane():
    manifest = candidate_parity_manifest()
    assert manifest["lifecycle_target"] == "NFL_DFS_FITTED_SIMULATOR_CANDIDATE_PARITY"
    assert manifest["all_candidate_lanes_fail_closed"] is True
    assert manifest["can_execute"] is False
    assert {row["lane"] for row in manifest["lanes"]} == {NBA, WNBA, MLB_HITTER, MLB_PITCHER}
    for row in manifest["lanes"]:
        assert row["minimum_simulations"] == 50_000
        assert row["chronological_whole_event_split"] is True
        assert row["untouched_test_required"] is True
        assert row["exact_scoring_profile_required"] is True
        assert row["failure_regime_mixture_supported"] is True
        assert row["certification_status"] == "CANDIDATE_ONLY"
        assert row["probability_publishable"] is False
        assert row["rank_eligible"] is False
        assert row["can_execute"] is False


def test_module_can_execute_is_unconditionally_false():
    assert CAN_EXECUTE is False
