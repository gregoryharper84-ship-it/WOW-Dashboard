"""Test the NFL model's actual all-game hit rate, never reconstructed picks."""
from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pickem_governed_accuracy_audit import (
    AccuracyAuditError, AUDIT_STATUS, audit_governed_pickem_week,
)


def _week(correct_count: int = 15, count: int = 16):
    manifest, picks, settlements = [], [], []
    for i in range(count):
        event = f"NFL_2026_W05_EVENT_{i:02d}"
        home, away = f"HOME{i}", f"AWAY{i}"
        manifest.append({
            "official_event_id": event, "home_team": home, "away_team": away,
            "season": 2026, "week": 5,
            "event_start_time_utc": "2026-10-11T17:00:00Z",
        })
        picks.append({
            "status": "PICKEM_READY", "official_event_id": event,
            "home_team": home, "away_team": away,
            "pool_pick": home, "opponent": away,
            "selected_probability": .65,
            "home_probability": .65, "away_probability": .35,
            "controlling_specialist": "wow.nfl-game-win-probability-expert",
            "source_terminal_label": "MODEL_QUALIFIED_HOLD" if i == 0 else "FINAL_APPROVED",
            "source_terminal_upgraded": False,
            "source_prediction_id": f"nfl-model-pregame-{i}",
            "source_snapshot_id": f"nfl-data-{i}",
            "immutable_model_timestamp": "2026-10-10T17:00:00Z",
            "can_execute": False,
        })
        settlements.append({
            "official_event_id": event,
            "winner": home if i < correct_count else away,
            "settlement_source": "fixture-official-final",
            "settlement_receipt_id": f"nfl-final-{i}",
            "settled_at": "2026-10-12T04:00:00Z",
        })
    return dict(
        season=2026, week=5, expected_game_count=count,
        manifest_receipt_id="frozen-manifest-2026-W5",
        manifest_frozen_at="2026-10-10T15:00:00Z",
        manifest=manifest, picks=picks, settlements=settlements,
    )


def test_15_of_16_meets_operator_goal_without_claiming_prediction_power():
    data = _week(15)
    result = audit_governed_pickem_week(**data)
    assert result["status"] == AUDIT_STATUS
    assert result["correct"] == 15
    assert result["incorrect"] == 1
    assert result["accuracy"] == pytest.approx(.9375)
    assert result["operator_target_met"] is True
    assert result["event_count"] == result["scored_count"] == 16
    assert result["unreconciled_count"] == 0
    assert len(result["games"]) == 16
    assert result["games"][0]["source_terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["production_probability_changed"] is False
    assert result["production_pick_changed"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert 0 < result["brier_home"] < 1
    assert result["log_loss"] > 0


@pytest.mark.parametrize(("hits", "expected"), [(16, True), (15, True), (14, False), (9, False)])
def test_forced_full_board_target_is_measurement_not_qualification(hits, expected):
    result = audit_governed_pickem_week(**_week(hits))
    assert result["correct"] == hits
    assert result["operator_target_met"] is expected
    assert result["terminal_authority"] == "V17_TERMINAL_REDUCER"


@pytest.mark.parametrize(("mutate", "blocker"), [
    (lambda x: x["picks"].pop(), "PICKEM_ACCURACY_PICK_EVENT_SET_MISMATCH"),
    (lambda x: x["settlements"].pop(), "PICKEM_ACCURACY_SETTLEMENT_EVENT_SET_MISMATCH"),
    (lambda x: x["picks"].append(deepcopy(x["picks"][0])), "PICKEM_ACCURACY_PICKS_DUPLICATE_EVENT"),
    (lambda x: x["picks"][0].update({"source_prediction_id": "nfl-model-pregame-1"}), "PICKEM_ACCURACY_DUPLICATE_PREDICTION_RECEIPT"),
    (lambda x: x["picks"][0].update({"immutable_model_timestamp": "2026-10-11T17:00:00Z"}), "PICKEM_ACCURACY_PREDICTION_NOT_PREGAME"),
    (lambda x: x["picks"][0].update({"controlling_specialist": "generic-llm"}), "PICKEM_ACCURACY_SPECIALIST_MISMATCH"),
    (lambda x: x["picks"][0].update({"status": "PICKEM_BLOCKED"}), "PICKEM_ACCURACY_INVALID_PICK_IDENTITY_OR_STATUS"),
    (lambda x: x["picks"][0].update({"source_terminal_label": "FINAL_REJECTED"}), "PICKEM_ACCURACY_SOURCE_TERMINAL_NOT_PROBABILITY_BEARING"),
    (lambda x: x["picks"][0].update({"source_terminal_upgraded": True}), "PICKEM_ACCURACY_SOURCE_TERMINAL_UPGRADED"),
    (lambda x: x["picks"][0].update({"can_execute": True}), "PICKEM_ACCURACY_EXECUTION_CONTRACT_INVALID"),
    (lambda x: x["picks"][0].update({"home_probability": .95}), "PICKEM_ACCURACY_TWO_SIDED_PROBABILITY_NOT_NORMALIZED"),
    (lambda x: x["picks"][0].update({"selected_probability": .35}), "PICKEM_ACCURACY_SELECTION_NOT_GOVERNED_MAX"),
    (lambda x: x["settlements"][0].update({"winner": "TIE"}), "PICKEM_ACCURACY_WINNER_INVALID_OR_TIE_NEEDS_RULE"),
    (lambda x: x["settlements"][0].update({"settlement_source": None}), "PICKEM_ACCURACY_SETTLEMENT_SOURCE_MISSING"),
    (lambda x: x["settlements"][0].update({"settled_at": "2026-10-11T16:00:00Z"}), "PICKEM_ACCURACY_PREMATURE_SETTLEMENT"),
    (lambda x: x.update({"manifest_frozen_at": "2026-10-12T17:00:00Z"}), "PICKEM_ACCURACY_MANIFEST_FROZEN_AFTER_KICKOFF"),
    (lambda x: x.update({"expected_game_count": 15}), "PICKEM_ACCURACY_SOURCE_GAME_COUNT_MISMATCH"),
])
def test_missing_stale_contradictory_and_unsafe_evidence_blocks_entire_week(mutate, blocker):
    args = _week(15)
    mutate(args)
    with pytest.raises(AccuracyAuditError, match=blocker):
        audit_governed_pickem_week(**args)


def test_short_bye_week_is_allowed_only_with_exact_schedule_count():
    data = _week(12, 13)
    result = audit_governed_pickem_week(**data)
    assert result["event_count"] == 13
    assert result["correct"] == 12
    assert result["operator_target_met"] is True
