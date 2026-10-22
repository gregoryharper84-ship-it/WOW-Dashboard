from __future__ import annotations

from copy import deepcopy

import pytest

from v17.nfl_pickem_full_pool_equity import PoolEquityInputError
from v17.nfl_pickem_visible_pool_snapshot import summarize_visible_pool_snapshot


EVENTS = [
    {"official_event_id": "2026_07_NE_CHI", "home_team": "CHI", "away_team": "NE"},
    {"official_event_id": "2026_07_PIT_NO", "home_team": "NO", "away_team": "PIT"},
]
CUTOFF = "2026-10-23T00:15:00Z"
SNAPSHOT = {
    "audience": "ALL_VISIBLE_ENTRIES",
    "source_kind": "GOOGLE_DRIVE_COPY",
    "source_file_id": "live-sheet-1",
    "copy_file_id": "private-snapshot-2",
    "provider_created_at": "2026-10-22T15:27:25.950Z",
    "provider_modified_at": "2026-10-22T15:27:25.950Z",
    "available_to_user_at": "2026-10-22T15:28:00Z",
    "private_owner_only": True,
    "visible_row_count": 6,
    "event_counts": {
        "2026_07_NE_CHI": {"CHI": 4, "NE": 2},
        "2026_07_PIT_NO": {"NO": 2, "PIT": 4},
    },
}


def score(snapshot=SNAPSHOT, events=EVENTS, cutoff=CUTOFF):
    return summarize_visible_pool_snapshot(snapshot, events=events, earliest_kickoff_at=cutoff)


def test_counts_normalize_only_as_visible_sample_not_certified_opponents():
    original = deepcopy(SNAPSHOT)
    result = score()
    assert result == score()
    assert SNAPSHOT == original
    assert result["status"] == "PICKEM_VISIBLE_SAMPLE_RECORDED"
    assert result["event_count"] == 2
    assert result["visible_row_count"] == 6
    assert result["rows"][0]["observed_home_fraction"] == pytest.approx(4 / 6)
    assert result["rows"][1]["observed_home_fraction"] == pytest.approx(2 / 6)
    assert len(result["receipt_digest_sha256"]) == 64
    assert result["digest_is_provider_signature"] is False
    assert result["sample_audience"] == "ALL_VISIBLE_ENTRIES_NOT_OPPONENTS"
    assert result["user_entry_excluded"] is False
    assert result["individual_submission_times_verified"] is False
    assert result["pool_size_verified"] is False
    assert result["certified_prelock_opponent_forecast"] is False
    assert result["certified_first_place_equity"] is False
    assert result["can_execute"] is False
    assert result["production_pick_mutation_allowed"] is False
    assert result["sporting_probability_modified"] is False
    assert "entry_id" not in str(result)
    assert "participant_name" not in str(result)


@pytest.mark.parametrize("field,value,code", [
    ("audience", "OPPONENT_ENTRIES", "PICKEM_VISIBLE_AUDIENCE_INVALID"),
    ("source_kind", "LIVE_SHEET", "PICKEM_VISIBLE_SOURCE_KIND_INVALID"),
    ("private_owner_only", False, "PICKEM_VISIBLE_PRIVACY_UNVERIFIED"),
    ("source_file_id", "private-snapshot-2", "PICKEM_VISIBLE_COPY_NOT_INDEPENDENT"),
    ("copy_file_id", "", "PICKEM_VISIBLE_COPY_ID_REQUIRED"),
    ("provider_created_at", "2026-10-22T15:27:25", "PICKEM_VISIBLE_COPY_TIMESTAMP_INVALID"),
    ("provider_modified_at", "2026-10-22T15:28:25.950Z", "PICKEM_VISIBLE_COPY_MODIFIED"),
    ("available_to_user_at", "2026-10-22T15:20:00Z", "PICKEM_VISIBLE_NOT_BEFORE_KICKOFF"),
    ("available_to_user_at", "2026-10-23T00:15:00Z", "PICKEM_VISIBLE_NOT_BEFORE_KICKOFF"),
    ("visible_row_count", True, "PICKEM_VISIBLE_ROW_COUNT_INVALID"),
    ("visible_row_count", 0, "PICKEM_VISIBLE_ROW_COUNT_INVALID"),
    ("visible_row_count", 1001, "PICKEM_VISIBLE_ROW_COUNT_INVALID"),
    ("event_counts", {"2026_07_NE_CHI": {"CHI": 4, "NE": 2}},
     "PICKEM_VISIBLE_EVENT_SET_INVALID"),
    ("event_counts", {"2026_07_NE_CHI": {"CHI": 5, "NE": 2},
                       "2026_07_PIT_NO": {"NO": 2, "PIT": 4}},
     "PICKEM_VISIBLE_PARTIAL_OR_DUPLICATE_VOTES"),
    ("event_counts", {"2026_07_NE_CHI": {"CHI": True, "NE": 5},
                       "2026_07_PIT_NO": {"NO": 2, "PIT": 4}},
     "PICKEM_VISIBLE_TEAM_COUNTS_INVALID"),
    ("event_counts", {"2026_07_NE_CHI": {"CHI": 4, "Bears": 2},
                       "2026_07_PIT_NO": {"NO": 2, "PIT": 4}},
     "PICKEM_VISIBLE_TEAM_COUNTS_INVALID"),
])
def test_bad_or_leaky_snapshot_fails_closed(field, value, code):
    snapshot = deepcopy(SNAPSHOT)
    snapshot[field] = value
    with pytest.raises(PoolEquityInputError, match=code):
        score(snapshot)


def test_rejects_unverified_canonical_event_identity():
    changed = deepcopy(EVENTS)
    changed[1]["official_event_id"] = "2026_07_NE_CHI"
    with pytest.raises(PoolEquityInputError, match="PICKEM_VISIBLE_EVENT_ID_INVALID"):
        score(events=changed)


def test_rejects_provider_timestamp_after_first_kickoff():
    snapshot = deepcopy(SNAPSHOT)
    snapshot["provider_created_at"] = "2026-10-23T00:15:01Z"
    snapshot["provider_modified_at"] = snapshot["provider_created_at"]
    snapshot["available_to_user_at"] = snapshot["provider_created_at"]
    with pytest.raises(PoolEquityInputError, match="PICKEM_VISIBLE_NOT_BEFORE_KICKOFF"):
        score(snapshot)


def test_rejects_untrusted_extra_event_counts_even_when_plausible():
    snapshot = deepcopy(SNAPSHOT)
    snapshot["event_counts"]["fake-game"] = {"H": 5, "A": 1}
    with pytest.raises(PoolEquityInputError, match="PICKEM_VISIBLE_EVENT_SET_INVALID"):
        score(snapshot)


def test_aggregate_receipt_changes_when_counts_change():
    snapshot = deepcopy(SNAPSHOT)
    snapshot["event_counts"]["2026_07_NE_CHI"] = {"CHI": 3, "NE": 3}
    assert score(snapshot)["receipt_digest_sha256"] != score()["receipt_digest_sha256"]


def test_snapshot_is_not_directly_accepted_as_opponent_ownership_forecast():
    from v17.nfl_pickem_ownership_forecast import validate_prelock_ownership_forecast
    sample = score()
    with pytest.raises(PoolEquityInputError, match="PICKEM_OWNERSHIP_AUDIENCE_INVALID"):
        validate_prelock_ownership_forecast(sample, lock_at=CUTOFF, events=EVENTS)
