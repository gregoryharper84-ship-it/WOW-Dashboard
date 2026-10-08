from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pickem_pool_equity_replay import (
    REPLAY_BLOCKED,
    REPLAY_EVIDENCE_INSUFFICIENT,
    REPLAY_RESEARCH_COMPLETE,
    ReplayInputError,
    replay_one_week,
    replay_pool_weeks,
)


def _week(week_id: str = "2026-W4", fold: str = "DISCOVERY") -> dict:
    event = f"{week_id}:A@H"
    return {
        "week_id": week_id,
        "fold": fold,
        "manifest_id": f"manifest-{week_id}",
        "lock_at": "2026-10-01T00:00:00Z",
        "settled_at": "2026-10-07T00:00:00Z",
        "settlement_source": "fixture-certified-final-score",
        "pool_size": 3,
        "governed_picks": [
            {
                "status": "PICKEM_READY",
                "official_event_id": event,
                "home_team": "H",
                "away_team": "A",
                "pool_pick": "H",
                "opponent": "A",
                "selected_probability": .53,
                "home_probability": .53,
                "away_probability": .47,
                "pickem_review_class": "TOSS_UP_REVIEW",
                "source_prediction_id": f"model-{week_id}",
                "source_snapshot_id": f"source-{week_id}",
                "immutable_model_timestamp": "2026-09-30T10:00:00Z",
                "controlling_specialist": "wow.nfl-game-win-probability-expert",
                "can_execute": False,
            }
        ],
        "ownership_snapshots": {
            event: {
                "shares": {"H": .8, "A": .2},
                "snapshot_id": f"ownership-{week_id}",
                "source": "pregame-opponent-poll",
                "audience": "OPPONENT_ENTRIES",
                "observed_at": "2026-09-30T12:00:00Z",
            },
        },
        "opponent_entries": [
            {
                "entry_id": "other1",
                "receipt_id": f"entry1-{week_id}",
                "submitted_at": "2026-09-30T18:00:00Z",
                "picks": {event: "H"},
            },
            {
                "entry_id": "other2",
                "receipt_id": f"entry2-{week_id}",
                "submitted_at": "2026-09-30T18:00:00Z",
                "picks": {event: "H"},
            },
        ],
        "settled_winners": {event: "A"},
    }


def test_shadow_scores_better_on_pregame_frozen_minor_side_without_probability_mutation():
    week = _week()
    out = replay_one_week(week)
    assert out["baseline"]["correct"] == 0
    assert out["shadow"]["correct"] == 1
    assert out["baseline"]["first_or_tied"] is True  # 0-0 tie with both opponents
    assert out["baseline"]["equal_tie_share_proxy"] == pytest.approx(1 / 3)
    assert out["shadow"]["sole_first"] is True
    assert out["shadow"]["equal_tie_share_proxy"] == pytest.approx(1)
    assert out["changed_pick_count"] == 1
    assert out["expected_correct_sacrifice"] == pytest.approx(.06)
    assert out["can_execute"] is False
    assert out["automatic_promotion"] is False
    assert week["governed_picks"][0]["pool_pick"] == "H"


def test_discovery_only_is_evidence_insufficient_and_holdout_is_separate():
    out = replay_pool_weeks([_week()])
    assert out["status"] == REPLAY_EVIDENCE_INSUFFICIENT
    assert out["discovery"]["weeks"] == 1
    assert out["holdout"]["weeks"] == 0
    assert out["automatic_promotion"] is False

    a = _week()
    b = _week("2026-W5", "HOLDOUT")
    out = replay_pool_weeks([a, b])
    assert out["status"] == REPLAY_RESEARCH_COMPLETE
    assert out["discovery"]["weeks"] == 1
    assert out["holdout"]["weeks"] == 1
    assert out["sporting_probability_modified"] is False
    assert out["production_pick_mutation_allowed"] is False
    assert out["automatic_promotion"] is False
    assert "SMALL_SAMPLES_MUST_NOT_BE_TREATED_AS_CERTIFICATION" in out["evidence_limits"]


@pytest.mark.parametrize(("field", "replacement", "expected_code"), [
    ("ownership_observed_at", "2026-10-02T00:00:00Z", "PICKEM_REPLAY_POST_LOCK_OWNERSHIP"),
    ("model_timestamp", "2026-10-02T00:00:00Z", "PICKEM_REPLAY_MODEL_RECEIPT_AFTER_LOCK"),
    ("opponent_submitted_at", "2026-10-02T00:00:00Z", "PICKEM_REPLAY_POST_LOCK_OPPONENT_CARD"),
    ("bad_share", 0.3, "PICKEM_REPLAY_OWNERSHIP_NOT_NORMALIZED"),
    ("wrong_model", "another-model", "PICKEM_REPLAY_SPECIALIST_OWNERSHIP_MISMATCH"),
    ("wrong_pool_size", 5, "PICKEM_REPLAY_POOL_SIZE_MISMATCH"),
    ("late_settlement", "2026-09-29T00:00:00Z", "PICKEM_REPLAY_SETTLEMENT_PRECEDES_LOCK"),
    ("untrusted_audience", "ALL_ENTRIES", "PICKEM_REPLAY_OWNERSHIP_AUDIENCE_INVALID"),
    ("ungoverned_selected", .47, "PICKEM_REPLAY_BASELINE_NOT_GOVERNED_MAX"),
])
def test_leakage_identity_and_normalization_fail_closed(field, replacement, expected_code):
    week = _week()
    event = next(iter(week["ownership_snapshots"]))
    if field == "ownership_observed_at":
        week["ownership_snapshots"][event]["observed_at"] = replacement
    elif field == "model_timestamp":
        week["governed_picks"][0]["immutable_model_timestamp"] = replacement
    elif field == "opponent_submitted_at":
        week["opponent_entries"][0]["submitted_at"] = replacement
    elif field == "bad_share":
        week["ownership_snapshots"][event]["shares"]["A"] = replacement
    elif field == "wrong_model":
        week["governed_picks"][0]["controlling_specialist"] = replacement
    elif field == "wrong_pool_size":
        week["pool_size"] = replacement
    elif field == "late_settlement":
        week["settled_at"] = replacement
    elif field == "untrusted_audience":
        week["ownership_snapshots"][event]["audience"] = replacement
    elif field == "ungoverned_selected":
        week["governed_picks"][0]["selected_probability"] = replacement
    with pytest.raises(ReplayInputError, match=expected_code):
        replay_one_week(week)


def test_extra_or_missing_rows_and_winner_mismatch_are_typed():
    a = _week()
    e = next(iter(a["settled_winners"]))
    a["settled_winners"][e] = "C"
    with pytest.raises(ReplayInputError, match="PICKEM_REPLAY_SETTLED_SIDE_INVALID"):
        replay_one_week(a)

    b = _week()
    b["opponent_entries"][0]["picks"] = {}
    with pytest.raises(ReplayInputError, match="PICKEM_REPLAY_OPPONENT_CARD_EVENT_SET_MISMATCH"):
        replay_one_week(b)

    c = _week()
    c["governed_picks"].append(deepcopy(c["governed_picks"][0]))
    with pytest.raises(ReplayInputError, match="PICKEM_REPLAY_DUPLICATE_EVENT"):
        replay_one_week(c)


def test_bad_week_blocks_entire_replay_instead_of_silently_dropping():
    a, b = _week(), _week("2026-W5", "HOLDOUT")
    b["ownership_snapshots"][next(iter(b["ownership_snapshots"]))]["observed_at"] = "2026-10-04T00:00:00Z"
    out = replay_pool_weeks([a, b])
    assert out["status"] == REPLAY_BLOCKED
    assert out["accepted_weeks"] == 0
    assert out["rows"] == []
    assert out["blockers"][0]["code"] == "PICKEM_REPLAY_POST_LOCK_OWNERSHIP"


def test_duplicate_week_ids_are_rejected():
    out = replay_pool_weeks([_week(), _week()])
    assert out["status"] == REPLAY_BLOCKED
    assert out["blockers"][0]["code"] == "PICKEM_REPLAY_DUPLICATE_WEEK"


def test_missing_inputs_have_no_promoted_or_fabricated_results():
    assert replay_pool_weeks([])["status"] == REPLAY_BLOCKED
    assert replay_pool_weeks([])["can_execute"] is False
