from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17.scout_board_materializer import _display_row
from v17.scout_governed_probability_lookup import lookup_governed_probability


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


def _candidate(**overrides):
    row = {
        "candidate_id": "scout-nfl-1",
        "sport_key": "americanfootball_nfl",
        "market_type": "TEAM_EVENT",
        "selection": None,
        "event_id": "espn-401872931",
        "canonical_event_id": None,
        "commence_time": datetime(2026, 9, 15, 0, 15, tzinfo=timezone.utc),
        "home_team": "Kansas City Chiefs",
        "away_team": "Denver Broncos",
        "controlling_specialist": "LLP_TEAM_BETTING_ENGINE",
        "research_status": "RESEARCH_INTEREST_LOW",
        "research_priority_score": 65.0,
        "thesis": "Fresh multi-book research evidence",
        "edge_classes": ["MARKET_DISAGREEMENT"],
        "contradictory_evidence": [],
        "red_team_flags": [],
        "can_execute": False,
    }
    row.update(overrides)
    return row


def test_nfl_publishable_specialist_probability_is_resolved_without_scout_probability_authority():
    cur = FakeCursor((
        "Denver Broncos",
        0.5796689600932923,
        0.523750876984715,
        True,
        "NFL_TEAM_EVENT_V1",
        datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc),
    ))
    result = lookup_governed_probability(cur, _candidate())
    assert result["governed_selection"] == "Denver Broncos"
    assert result["governed_probability"] == 0.5796689600932923
    assert result["calibrated_lower_bound"] == 0.523750876984715
    assert result["probability_publishable"] is True
    assert result["probability_source"] == "CONTROLLING_SPECIALIST"
    assert result["can_execute"] is False
    assert cur.calls[0][1][0] == "NFL:espn-401872931"


def test_board_row_keeps_legacy_scout_probability_null_and_formats_governed_score_beside_pick():
    cur = FakeCursor((
        "Denver Broncos",
        0.5796689600932923,
        0.523750876984715,
        True,
        "NFL_TEAM_EVENT_V1",
        datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc),
    ))
    result = _display_row(cur, _candidate())
    assert result["pick"] == "Denver Broncos"
    assert result["probability"] is None
    assert result["governed_probability_pct"] == 57.97
    assert result["calibrated_lower_bound_pct"] == 52.38
    assert result["probability_display"] == "57.97%"
    assert result["lower_bound_display"] == "52.38%"
    assert result["probability_source"] == "CONTROLLING_SPECIALIST"
    assert result["can_execute"] is False


def test_missing_publishable_specialist_result_displays_pending_not_market_probability():
    cur = FakeCursor(None)
    result = _display_row(cur, _candidate())
    assert result["probability"] is None
    assert result["governed_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_display"] == "—"
    assert result["specialist_status"] == "PENDING_SPECIALIST"
    assert result["probability_publishable"] is False


def test_unsupported_sport_fails_closed_as_model_unavailable():
    cur = FakeCursor(None)
    result = lookup_governed_probability(cur, _candidate(sport_key="basketball_nba"))
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert cur.calls == []


LOCK = datetime(2026, 9, 15, 0, 15, tzinfo=timezone.utc)


def _nfl_row(**overrides):
    row = {
        "selection": "Denver Broncos",
        "probability": 0.5796689600932923,
        "lower": 0.523750876984715,
        "publishable": True,
        "version": "NFL_TEAM_EVENT_V1",
        "created_at": datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return (
        row["selection"], row["probability"], row["lower"],
        row["publishable"], row["version"], row["created_at"],
    )


def _mlb_row(**overrides):
    row = {
        "selection": "Kansas City Chiefs",
        "probability": 0.61,
        "lower": 0.55,
        "publishable": True,
        "specialist": "MLB_MODEL_V1",
        "terminal_label": "FINAL_APPROVED",
        "created_at": datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return (
        row["selection"], row["probability"], row["lower"], row["publishable"],
        row["specialist"], row["terminal_label"], row["created_at"],
    )


def test_lower_bound_above_point_is_never_displayed():
    cur = FakeCursor(_nfl_row(lower=0.70))
    result = _display_row(cur, _candidate())
    assert result["governed_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_display"] == "—"
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"
    assert result["probability_publishable"] is False


@pytest.mark.parametrize("probability", [0.0, 1.0, float("nan"), "not-a-number"])
def test_probability_outside_strict_domain_is_never_displayed(probability):
    cur = FakeCursor(_nfl_row(probability=probability, lower=0.4))
    result = lookup_governed_probability(cur, _candidate())
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize("created_at", [LOCK, LOCK.replace(hour=1), None, "naive"])
def test_receipt_not_recorded_before_the_event_lock_is_never_displayed(created_at):
    cur = FakeCursor(_nfl_row(created_at=created_at))
    result = lookup_governed_probability(cur, _candidate())
    assert result["governed_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


def test_selection_outside_this_exact_event_is_never_displayed():
    cur = FakeCursor(_nfl_row(selection="Green Bay Packers"))
    result = lookup_governed_probability(cur, _candidate())
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


def test_valid_mlb_receipt_is_displayed_but_invalid_bounds_are_not():
    candidate = _candidate(sport_key="baseball_mlb")
    valid = lookup_governed_probability(FakeCursor(_mlb_row()), candidate)
    assert valid["governed_probability"] == 0.61
    assert valid["calibrated_lower_bound"] == 0.55
    assert valid["probability_publishable"] is True
    assert valid["can_execute"] is False

    inverted = lookup_governed_probability(FakeCursor(_mlb_row(lower=0.90)), candidate)
    assert inverted["governed_probability"] is None
    assert inverted["specialist_status"] == "MODEL_OUTPUT_INVALID"


def test_malformed_scout_governance_never_displays_probability():
    cur = FakeCursor(("Denver Broncos", 0.9, 0.8, True, "bad", datetime.now(timezone.utc)))
    result = lookup_governed_probability(cur, _candidate(can_execute=True))
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "SCOUT_GOVERNANCE_INVALID"
    assert cur.calls == []
