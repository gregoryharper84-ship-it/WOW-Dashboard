import pytest

from services.nfl_dfs_fitted_simulator import (
    CandidateFitError,
    chronological_split,
    fit_candidate,
    simulate_exact_line,
)


SCORING = {
    "scoring_profile_id": "TEST_PPR",
    "passing_yards_points": 0.04,
    "passing_td_points": 4.0,
    "interception_points": -1.0,
    "rushing_yards_points": 0.1,
    "rushing_td_points": 6.0,
    "receiving_yards_points": 0.1,
    "reception_points": 1.0,
    "receiving_td_points": 6.0,
    "fumble_lost_points": -2.0,
    "two_point_conversion_points": 2.0,
}


def _row(event, player="wr-a", yards=50.0, receptions=5.0, td=0.0):
    return {
        "event_id": f"evt-{event}",
        "event_date": f"2026-01-{event:02d}",
        "season": 2025,
        "week_number": event,
        "player_id": player,
        "player": player,
        "position": "WR",
        "team": "AAA",
        "opponent": "BBB",
        "receiving_yards": yards,
        "receptions": receptions,
        "receiving_td": td,
        "passing_yards": 0,
        "passing_td": 0,
        "interceptions": 0,
        "rushing_yards": 0,
        "rushing_td": 0,
        "fumbles_lost": 0,
        "two_point_conversions": 0,
    }


def _history():
    return [
        _row(i, yards=40 + i * 4, receptions=3 + (i % 4), td=1 if i % 3 == 0 else 0)
        for i in range(1, 13)
    ]


def test_chronological_split_keeps_whole_events_disjoint():
    rows = _history() + [_row(4, player="wr-b", yards=71)]
    split = chronological_split(rows)
    train_ids = {r["event_id"] for r in split.train}
    cal_ids = {r["event_id"] for r in split.calibration}
    test_ids = {r["event_id"] for r in split.test}

    assert train_ids.isdisjoint(cal_ids)
    assert train_ids.isdisjoint(test_ids)
    assert cal_ids.isdisjoint(test_ids)
    assert split.train_end <= split.calibration_end <= split.test_end
    # Both players in event 4 must land in the same split.
    event4_rows = [r for r in rows if r["event_id"] == "evt-4"]
    destinations = [
        sum(r in partition for partition in (split.train, split.calibration, split.test))
        for r in event4_rows
    ]
    assert destinations == [1, 1]


def test_fit_uses_only_prior_events_for_same_event_residuals():
    rows = [_row(1, yards=10)]
    rows.extend([
        _row(2, player="wr-a", yards=20),
        _row(2, player="wr-b", yards=1000),
    ])
    rows.extend(_row(i, yards=20 + i) for i in range(3, 11))

    artifact = fit_candidate(rows, source_id="synthetic")
    residuals = artifact.residual_vectors["WR"]

    # Event 2 baselines both use event 1 only. wr-b cannot see wr-a's event-2 result.
    assert residuals[0]["receiving_yards"] == pytest.approx(10.0)
    assert residuals[1]["receiving_yards"] == pytest.approx(990.0)


def test_candidate_artifact_is_immutable_fail_closed_metadata():
    artifact = fit_candidate(_history(), source_id="nflverse:test-snapshot")
    metadata = artifact.as_metadata()

    assert len(metadata["source_sha256"]) == 64
    assert metadata["certification_status"] == "CANDIDATE_ONLY"
    assert metadata["calibration_status"] == "BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT"
    assert metadata["probability_publishable"] is False
    assert metadata["rank_eligible"] is False
    assert metadata["can_execute"] is False


def test_simulation_rejects_less_than_contract_minimum_draws():
    artifact = fit_candidate(_history(), source_id="synthetic")
    with pytest.raises(CandidateFitError, match="at least 50000"):
        simulate_exact_line(
            artifact,
            player_id="wr-a",
            position="WR",
            exact_line=12.5,
            side="MORE",
            scoring_profile=SCORING,
            simulation_count=49_999,
        )


def test_raw_distribution_never_becomes_governed_probability_without_promotion():
    artifact = fit_candidate(_history(), source_id="synthetic")
    result = simulate_exact_line(
        artifact,
        player_id="wr-a",
        position="WR",
        exact_line=12.5,
        side="MORE",
        scoring_profile=SCORING,
        simulation_count=50_000,
        seed=20260908,
        opportunity_context={
            "certified": True,
            "team_play_distribution": {"mean": 64},
            "game_state_model": {"ready": True},
            "player_opportunity_model": {
                "component_means": {
                    "receiving_yards": 60,
                    "receptions": 5,
                    "receiving_td": 0.4,
                }
            },
        },
        regimes=[
            {
                "name": "NORMAL_ROLE",
                "probability": 0.90,
                "component_multipliers": {},
                "certified": True,
            },
            {
                "name": "LIMITED_ROLE",
                "probability": 0.10,
                "component_multipliers": {
                    "receiving_yards": 0.55,
                    "receptions": 0.60,
                    "receiving_td": 0.55,
                },
                "certified": True,
            },
        ],
    )

    assert result["simulation_count"] == 50_000
    assert result["P(MORE)"] + result["P(LESS)"] + result["P(PUSH)"] == pytest.approx(1.0)
    assert 0.0 <= result["raw_candidate_probability"] <= 1.0
    assert result["calibrated_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
    assert result["terminal_status"] == "CALIBRATION_BLOCKED_NO_PUBLISH"
    assert "BLOCKED_NO_CERTIFIED_EXACT_LINE_CALIBRATION_ARTIFACT" in result["blockers"]
    assert "FITTED_MODEL_ARTIFACT_NOT_PROMOTED" in result["blockers"]


def test_uncertified_regime_package_is_explicit_blocker():
    artifact = fit_candidate(_history(), source_id="synthetic")
    result = simulate_exact_line(
        artifact,
        player_id="wr-a",
        position="WR",
        exact_line=12.5,
        side="LESS",
        scoring_profile=SCORING,
        simulation_count=50_000,
        seed=9,
    )
    assert result["terminal_status"] == "MODEL_INPUTS_INSUFFICIENT"
    assert "FAILURE_REGIME_PACKAGE_NOT_CERTIFIED" in result["blockers"]
    assert "NFL_FULL_MODEL_CONTEXT_NOT_CERTIFIED" in result["blockers"]


def test_regime_probabilities_must_sum_to_one():
    artifact = fit_candidate(_history(), source_id="synthetic")
    with pytest.raises(CandidateFitError, match="sum to 1.0"):
        simulate_exact_line(
            artifact,
            player_id="wr-a",
            position="WR",
            exact_line=12.5,
            side="MORE",
            scoring_profile=SCORING,
            simulation_count=50_000,
            regimes=[
                {"name": "NORMAL_ROLE", "probability": 0.8, "certified": True},
                {"name": "LIMITED_ROLE", "probability": 0.1, "certified": True},
            ],
        )
