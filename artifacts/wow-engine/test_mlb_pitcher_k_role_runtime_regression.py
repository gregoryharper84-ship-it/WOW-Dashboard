from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import prop_auto_hydration as hydration
from mlb_pitcher_k_risk_guard import TAG_LOW_LINE_MORE_TAIL_CONTRADICTION
from prop_terminal_reducer_v2 import reduce_prop_terminal
from qualification_policy_v2 import classify_prop_probability


def test_auto_hydration_fails_closed_when_current_usage_is_opener_shaped(monkeypatch):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)

    monkeypatch.setattr(hydration, "_resolve_player_id", lambda *args, **kwargs: (123, "Regression Pitcher"))
    monkeypatch.setattr(
        hydration,
        "_schedule_context",
        lambda *args, **kwargs: {
            "starter_status": "STARTER_PROBABLE_OFFICIAL_SCHEDULE",
            "team": "CWS",
            "opponent": "CLE",
            "venue": "Test Park",
            "official_game_pk": 999,
            "official_game_date": event_start.isoformat(),
            "schedule_status": "Scheduled",
            "side": "AWAY",
        },
    )
    monkeypatch.setattr(
        hydration,
        "_game_log",
        lambda *args, **kwargs: (
            [4.0] * 10,
            [{"season": 2026, "bf": 22, "outs": 15, "so": 4}] * 10,
            {
                "status": "STARTER_MODEL_INCOMPATIBLE",
                "appearances_found": 6,
                "start_share": 1 / 6,
                "short_appearance_share": 1.0,
                "mean_batters_faced": 6.5,
            },
        ),
    )

    with pytest.raises(hydration.PropAutoHydrationError) as exc_info:
        hydration.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Regression Pitcher",
            stat_type="PITCHER_STRIKEOUTS",
            event_start_time=event_start.isoformat(),
            now=now,
        )

    assert exc_info.value.code == "MLB_STARTER_ROLE_WORKLOAD_MISMATCH"
    assert exc_info.value.detail["required_action"].startswith("use a certified opener/bulk-role model")


def test_starter_role_workload_mismatch_reduces_to_inputs_insufficient_not_model_unavailable():
    decision = reduce_prop_terminal(
        proposed_label="MODEL_UNAVAILABLE",
        blockers=["MLB_STARTER_ROLE_WORKLOAD_MISMATCH"],
        model_evaluated=False,
    )
    assert decision.terminal_label == "MODEL_INPUTS_INSUFFICIENT"
    assert decision.verdict_class == "ACQUISITION_BLOCKED"
    assert decision.model_evaluated is False


def test_low_k_tail_flag_caps_high_probability_without_mutating_probability_package():
    decision = classify_prop_probability(
        calibrated_probability=0.69,
        calibrated_lower_bound=0.62,
        calibrated_upper_bound=0.75,
        calibration_status="PLATT_TIME_SPLIT_V1",
        blockers=[],
        risk_flags=[TAG_LOW_LINE_MORE_TAIL_CONTRADICTION],
        probability_publishable=True,
        model_quality_status="PASS",
        input_complete=True,
    )

    assert decision.terminal_label == "RESEARCH_INTEREST"
    assert decision.confidence_tier == "RISK_CEILING"
    assert decision.rank_eligible is False
    assert decision.model_supported is True
    assert decision.model_qualified is False
    assert decision.downstream_money_evaluation_allowed is False
    assert any(
        reason == f"QUALIFICATION_CEILING={TAG_LOW_LINE_MORE_TAIL_CONTRADICTION}"
        for reason in decision.qualification_reasons
    )


def test_same_high_probability_remains_high_without_tail_contradiction():
    decision = classify_prop_probability(
        calibrated_probability=0.69,
        calibrated_lower_bound=0.62,
        calibrated_upper_bound=0.75,
        calibration_status="PLATT_TIME_SPLIT_V1",
        blockers=[],
        risk_flags=[],
        probability_publishable=True,
        model_quality_status="PASS",
        input_complete=True,
    )
    assert decision.terminal_label == "MODEL_QUALIFIED_HOLD"
    assert decision.confidence_tier == "HIGH"
    assert decision.rank_eligible is True
