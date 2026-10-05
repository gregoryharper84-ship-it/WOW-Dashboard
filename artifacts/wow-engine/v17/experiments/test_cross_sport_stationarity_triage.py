from __future__ import annotations

import pytest

from v17.experiments.cross_sport_stationarity_triage import triage


def _variant(*, brier: float, log_loss: float, screen: bool, delta_ece: float = 0.0):
    return {
        "calibrated_brier": brier,
        "calibrated_log_loss": log_loss,
        "delta_calibrated_brier": brier - 0.25,
        "delta_calibrated_log_loss": log_loss - 0.69,
        "delta_ece": delta_ece,
        "research_screen_pass": screen,
    }


def _lane(league: str, *, severe: bool = True, replay_status: str = "REPRODUCED"):
    geometry = {
        "features": [
            {
                "feature": "home_games_prior",
                "severe": severe,
            }
        ]
    }
    replay = {
        "status": replay_status,
        "incumbent": {
            "calibrated_brier": 0.25,
            "calibrated_log_loss": 0.69,
            "research_screen_pass": False,
        },
        "variants": {
            "drop": _variant(brier=0.24, log_loss=0.68, screen=True),
            "log1p": _variant(brier=0.245, log_loss=0.685, screen=False),
            "stationary": _variant(brier=0.23, log_loss=0.67, screen=True),
        },
    }
    if replay_status != "REPRODUCED":
        replay = {
            "status": replay_status,
            "blocker": "D1_REPLAY_DATASET_HASH_MISMATCH",
        }
    return {"league": league, "geometry": geometry, "replay": replay}


def _report():
    return {
        "can_execute": False,
        "probability_publishable": False,
        "automatic_promotion": False,
        "sports": {
            "MLB": {"lanes": [_lane("MLB")]},
            "NCAAB": {"lanes": [_lane("NCAAB")]},
            "SOCCER": {
                "lanes": [
                    _lane("BUNDESLIGA"),
                    _lane("EPL"),
                    _lane("LALIGA"),
                    _lane("SERIE_A"),
                    _lane("LIGUE_1"),
                ]
            },
        },
    }


def test_triage_rejects_executable_input():
    report = _report()
    report["can_execute"] = True
    with pytest.raises(RuntimeError, match="INPUT_CAN_EXECUTE_INVALID"):
        triage(report)


def test_triage_limits_scope_to_evidence_backed_severe_wave():
    result = triage(_report())
    keys = {(row["sport"], row["league"]) for row in result["lanes"]}
    assert keys == {
        ("MLB", "MLB"),
        ("NCAAB", "NCAAB"),
        ("SOCCER", "BUNDESLIGA"),
        ("SOCCER", "EPL"),
        ("SOCCER", "LALIGA"),
        ("SOCCER", "SERIE_A"),
    }
    assert ("SOCCER", "LIGUE_1") not in keys
    assert result["can_execute"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False


def test_research_leader_requires_screen_pass_and_nonworse_proper_scores():
    result = triage(_report())
    mlb = next(
        row for row in result["lanes"]
        if row["sport"] == "MLB" and row["league"] == "MLB"
    )
    assert mlb["research_leader"] == "stationary"
    by_mode = {row["mode"]: row for row in mlb["variants"]}
    assert by_mode["drop"]["advances_to_deep_replay"] is True
    assert by_mode["log1p"]["advances_to_deep_replay"] is False
    assert by_mode["stationary"]["advances_to_deep_replay"] is True
    assert mlb["deep_replay_required"] is True


def test_replay_blocker_is_preserved_exactly():
    report = _report()
    report["sports"]["MLB"]["lanes"] = [
        _lane("MLB", replay_status="BLOCKED_WITH_EXACT_REASON")
    ]
    result = triage(report)
    mlb = next(
        row for row in result["lanes"]
        if row["sport"] == "MLB" and row["league"] == "MLB"
    )
    assert mlb["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert mlb["blocker"] == "D1_REPLAY_DATASET_HASH_MISMATCH"
