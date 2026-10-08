"""#1530: source receipts and settlement revisions, not postgame reconstructed picks."""
from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pickem_weekly_evidence import (
    BLOCKED,
    RECONCILED,
    WeeklyEvidenceError,
    reconcile_weekly_evidence,
)


def _evidence(count=16, hits=15):
    manifest = []
    picks = []
    finals = []
    for i in range(count):
        event_id = f"NFL_2026_W05_OFFICIAL_{i:02d}"
        home, away = f"HOME{i}", f"AWAY{i}"
        manifest.append({
            "official_event_id": event_id,
            "home_team": home, "away_team": away,
            "season": 2026, "week": 5,
            "schedule_status": "SCHEDULED",
            "event_start_time_utc": "2026-10-11T17:00:00Z",
        })
        picks.append({
            "official_event_id": event_id,
            "home_team": home, "away_team": away,
            "status": "PICKEM_READY",
            "pool_pick": home,
            "selected_probability": .65,
            "home_probability": .65,
            "away_probability": .35,
            "controlling_specialist": "wow.nfl-game-win-probability-expert",
            "source_terminal_label": "MODEL_QUALIFIED_HOLD",
            "source_terminal_upgraded": False,
            "source_prediction_id": f"pregame-model-{i}",
            "source_snapshot_id": f"pregame-snapshot-{i}",
            "immutable_model_timestamp": "2026-10-10T12:00:00Z",
            "latest_material_update_at": "2026-10-10T11:00:00Z",
            "can_execute": False,
        })
        finals.append({
            "official_event_id": event_id,
            "winner": home if i < hits else away,
            "settlement_source": "synthetic-fixture-no-real-source-proof",
            "settlement_receipt_id": f"settled-receipt-{i}",
            "settled_at": "2026-10-13T02:00:00Z",
            "official_final_status": "FINAL",
            "settlement_revision": 1,
        })
    return {
        "season": 2026,
        "week": 5,
        "official_manifest": {
            "season": 2026, "week": 5,
            "manifest_status": "FROZEN",
            "manifest_receipt_id": "fixture-frozen-manifest-05",
            "schedule_source_receipt_id": "fixture-schedule-source",
            "schedule_snapshot_id": "fixture-snapshot-05",
            "manifest_frozen_at": "2026-10-10T10:00:00Z",
            "expected_game_count": count,
            "events": manifest,
        },
        "immutable_predictions": picks,
        "official_settlement_history": finals,
    }


def _run(data):
    return reconcile_weekly_evidence(**data)


@pytest.mark.parametrize(("hits", "count"), [(16, 16), (15, 16), (9, 16), (12, 13)])
def test_all_game_evidence_is_reconciled_but_never_published(hits, count):
    got = _run(_evidence(count=count, hits=hits))
    assert got["status"] == RECONCILED
    assert got["expected_game_count"] == count
    assert got["manifest_event_count"] == count
    assert got["prediction_receipt_event_count"] == count
    assert got["settlement_event_count"] == count
    assert got["blockers"] == []
    assert got["report"]["correct"] == hits
    assert got["report"]["accuracy"] == pytest.approx(hits / count)
    assert len(got["report"]["games"]) == count
    assert got["candidate_receipt_id"].startswith("nfl-weekly-audit-")
    assert got["source_authenticity_verified"] is False
    assert got["durable_persistence_verified"] is False
    assert got["scheduled_delivery_verified"] is False
    assert got["publication_allowed"] is False
    assert got["can_execute"] is False


@pytest.mark.parametrize(("which", "missing_code"), [
    ("immutable_predictions", "PICKEM_WEEKLY_PREGAME_PREDICTION_MISSING"),
    ("official_settlement_history", "PICKEM_WEEKLY_OFFICIAL_FINAL_MISSING"),
])
def test_missing_one_event_blocks_entire_week_and_preserves_denominator(which, missing_code):
    data = _evidence()
    data[which].pop()
    result = _run(data)
    assert result["status"] == BLOCKED
    assert result["expected_game_count"] == 16
    assert result["report"] is None
    assert result["candidate_receipt_id"] is None
    assert missing_code in {b["code"] for b in result["blockers"]}
    assert result["publication_allowed"] is False


@pytest.mark.parametrize(("target", "code"), [
    ("immutable_predictions", "PICKEM_WEEKLY_UNSCHEDULED_PREDICTION"),
    ("official_settlement_history", "PICKEM_WEEKLY_UNSCHEDULED_SETTLEMENT"),
])
def test_unaccounted_extra_event_never_silently_skips_denominator(target, code):
    data = _evidence()
    other = deepcopy(data[target][0])
    other["official_event_id"] = "UNSCHEDULED_UNAUTHENTICATED_EVENT"
    if target == "official_settlement_history":
        other["settlement_receipt_id"] = "extra-settlement-receipt"
    data[target].append(other)
    got = _run(data)
    assert got["status"] == BLOCKED
    assert code in {b["code"] for b in got["blockers"]}


@pytest.mark.parametrize(("status", "blocker"), [
    ("POSTPONED", "PICKEM_WEEKLY_SCHEDULE_POSTPONED_NEEDS_REVISION"),
    ("CANCELLED", "PICKEM_WEEKLY_SCHEDULE_CANCELLED_NEEDS_REVISION"),
    ("UNKNOWN", "PICKEM_WEEKLY_SCHEDULE_STATUS_UNVERIFIED"),
])
def test_unresolved_schedule_revision_holds_full_report(status, blocker):
    data = _evidence()
    data["official_manifest"]["events"][0]["schedule_status"] = status
    got = _run(data)
    assert got["status"] == BLOCKED
    assert got["report"] is None
    assert blocker in {b["code"] for b in got["blockers"]}


def test_manifest_expected_count_mismatch_fails_closed():
    data = _evidence()
    data["official_manifest"]["expected_game_count"] = 15
    got = _run(data)
    assert got["status"] == BLOCKED
    assert "PICKEM_WEEKLY_MANIFEST_COUNT_MISMATCH" in {
        b["code"] for b in got["blockers"]
    }


@pytest.mark.parametrize(("which", "error"), [
    ("official_manifest", "PICKEM_WEEKLY_MANIFEST_DUPLICATE_EVENT"),
    ("immutable_predictions", "PICKEM_WEEKLY_PREDICTIONS_DUPLICATE_EVENT"),
])
def test_duplicate_canonical_event_identity_is_a_typed_error(which, error):
    data = _evidence()
    field = "events" if which == "official_manifest" else None
    rows = data[which][field] if field else data[which]
    rows.append(deepcopy(rows[0]))
    with pytest.raises(WeeklyEvidenceError, match=error):
        _run(data)


@pytest.mark.parametrize(("mutation", "error"), [
    (lambda d: d["official_manifest"].update({"manifest_status": "MUTABLE"}),
     "PICKEM_WEEKLY_MANIFEST_NOT_FROZEN"),
    (lambda d: d["official_manifest"].pop("schedule_source_receipt_id"),
     "PICKEM_WEEKLY_SCHEDULE_PROVENANCE_MISSING"),
    (lambda d: d["official_manifest"].update({"manifest_frozen_at": "2026-10-10T10:00:00"}),
     "PICKEM_WEEKLY_MANIFEST_FROZEN_AT_INVALID"),
    (lambda d: d["official_settlement_history"][0].update({"settlement_revision": 0}),
     "PICKEM_WEEKLY_SETTLEMENT_REVISION_INVALID"),
    (lambda d: d["official_settlement_history"][0].update({"settlement_revision": False}),
     "PICKEM_WEEKLY_SETTLEMENT_REVISION_INVALID"),
    (lambda d: d["official_settlement_history"][0].update({"settlement_receipt_id": "settled-receipt-1"}),
     "PICKEM_WEEKLY_SETTLEMENT_RECEIPT_REUSED"),
])
def test_malformed_source_or_revision_data_fails_closed(mutation, error):
    data = _evidence()
    mutation(data)
    with pytest.raises(WeeklyEvidenceError, match=error):
        _run(data)


def test_same_revision_is_disallowed_even_if_receipt_is_distinct():
    data = _evidence()
    duplicate = deepcopy(data["official_settlement_history"][0])
    duplicate["settlement_receipt_id"] = "distinct-receipt"
    data["official_settlement_history"].append(duplicate)
    with pytest.raises(WeeklyEvidenceError, match="PICKEM_WEEKLY_SETTLEMENT_REVISION_DUPLICATE"):
        _run(data)


def test_revision_gap_blocks_old_final_from_surviving():
    data = _evidence()
    revision = deepcopy(data["official_settlement_history"][0])
    revision.update({
        "settlement_revision": 3,
        "settlement_receipt_id": "revision-three",
        "settled_at": "2026-10-13T03:00:00Z",
    })
    data["official_settlement_history"].append(revision)
    with pytest.raises(WeeklyEvidenceError, match="PICKEM_WEEKLY_SETTLEMENT_REVISION_GAP"):
        _run(data)


def test_final_retracted_by_new_revision_is_not_silently_rescored():
    data = _evidence()
    revision = deepcopy(data["official_settlement_history"][0])
    revision.update({
        "settlement_revision": 2,
        "settlement_receipt_id": "revoked-receipt",
        "settled_at": "2026-10-13T04:00:00Z",
        "official_final_status": "UNDER_REVIEW",
    })
    data["official_settlement_history"].append(revision)
    got = _run(data)
    assert got["status"] == BLOCKED
    assert "PICKEM_WEEKLY_OFFICIAL_FINAL_NOT_VERIFIED" in {
        b["code"] for b in got["blockers"]
    }


def test_revised_official_winner_is_graded_only_from_latest_final_revision():
    data = _evidence()
    baseline = _run(data)
    revision = deepcopy(data["official_settlement_history"][0])
    revision.update({
        "settlement_revision": 2,
        "settlement_receipt_id": "independent-correction-02",
        "settled_at": "2026-10-13T04:00:00Z",
        "winner": data["official_manifest"]["events"][0]["away_team"],
    })
    data["official_settlement_history"].append(revision)
    got = _run(data)
    assert got["status"] == RECONCILED
    assert got["report"]["correct"] == baseline["report"]["correct"] - 1
    assert got["settlement_revision_counts"][revision["official_event_id"]] == 2
    assert got["candidate_receipt_id"] != baseline["candidate_receipt_id"]
    assert got["publication_allowed"] is False


def test_reorder_of_identical_verified_receipts_does_not_create_duplicate_candidate():
    data = _evidence()
    a = _run(data)["candidate_receipt_id"]
    data["official_manifest"]["events"].reverse()
    data["immutable_predictions"].reverse()
    data["official_settlement_history"].reverse()
    assert _run(data)["candidate_receipt_id"] == a


def test_reused_source_model_receipt_with_changed_probability_cannot_dedupe():
    data = _evidence()
    first = _run(data)["candidate_receipt_id"]
    data["immutable_predictions"][0].update({
        "selected_probability": .7, "home_probability": .7, "away_probability": .3,
    })
    got = _run(data)
    assert got["status"] == RECONCILED
    assert got["candidate_receipt_id"] != first
    assert got["publication_allowed"] is False


@pytest.mark.parametrize(("modify", "code"), [
    (lambda d: d["immutable_predictions"][0].update({"immutable_model_timestamp": "2026-10-11T17:00:00Z"}),
     "PICKEM_ACCURACY_PREDICTION_NOT_PREGAME"),
    (lambda d: d["immutable_predictions"][0].update({"latest_material_update_at": "2026-10-10T13:00:00Z"}),
     "PICKEM_ACCURACY_PREDICTION_STALE"),
    (lambda d: d["immutable_predictions"][0].update({"controlling_specialist": "generic-LLM"}),
     "PICKEM_ACCURACY_SPECIALIST_MISMATCH"),
    (lambda d: d["immutable_predictions"][0].update({"status": "MODEL_BLOCKED"}),
     "PICKEM_ACCURACY_INVALID_PICK_IDENTITY_OR_STATUS"),
    (lambda d: d["immutable_predictions"][0].update({"source_terminal_upgraded": True}),
     "PICKEM_ACCURACY_SOURCE_TERMINAL_UPGRADED"),
    (lambda d: d["immutable_predictions"][0].update({"can_execute": True}),
     "PICKEM_ACCURACY_EXECUTION_CONTRACT_INVALID"),
    (lambda d: d["official_settlement_history"][0].update({"winner": "UNVERIFIED"}),
     "PICKEM_ACCURACY_WINNER_INVALID_OR_TIE_NEEDS_RULE"),
    (lambda d: d["official_manifest"]["events"][0].update({"event_start_time_utc": "2026-10-10T09:00:00Z"}),
     "PICKEM_ACCURACY_MANIFEST_FROZEN_AFTER_KICKOFF"),
])
def test_canonical_audit_rejects_unsafe_or_hindsight_receipts(modify, code):
    data = _evidence()
    modify(data)
    got = _run(data)
    assert got["status"] == BLOCKED
    assert got["publication_allowed"] is False
    assert got["report"] is None
    assert code in {b["code"] for b in got["blockers"]}
