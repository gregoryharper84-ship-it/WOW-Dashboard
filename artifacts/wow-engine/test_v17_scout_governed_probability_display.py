"""Scout readback contract: display governed probability only from a receipt.

Scout itself never owns probability. These tests pin the readback to the
ledger's authoritative lock, exact Decimal bounds, the V17 terminal reducer
output and the exact event participants.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from v17.scout_board_materializer import _display_row
from v17.scout_governed_probability_lookup import lookup_governed_probability

AUTHORITATIVE_LOCK = datetime(2026, 9, 15, 0, 15, tzinfo=timezone.utc)
RECORDED_AT = datetime(2026, 9, 14, 20, 40, tzinfo=timezone.utc)
APPROVED_RECEIPT = {
    "status": "PASS",
    "terminal_label": "FINAL_APPROVED",
    "global_terminal_reducer": "V17_TERMINAL_REDUCER",
    "probability_publishable": True,
    "rank_eligible": True,
    "can_execute": False,
}


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
        "commence_time": AUTHORITATIVE_LOCK,
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


def _nfl_row(**overrides):
    """Governed NFL ledger row, in the exact select order."""
    row = {
        "selection": "Denver Broncos",
        "probability": Decimal("0.5796689600932923"),
        "lower": Decimal("0.523750876984715"),
        "upper": Decimal("0.640000000000000"),
        "publishable": True,
        "version": "NFL_TEAM_EVENT_V1",
        "created_at": RECORDED_AT,
        "event_start": AUTHORITATIVE_LOCK,
        "receipt": dict(APPROVED_RECEIPT),
    }
    row.update(overrides)
    return (
        row["selection"], row["probability"], row["lower"], row["upper"],
        row["publishable"], row["version"], row["created_at"],
        row["event_start"], row["receipt"],
    )


def _mlb_row(**overrides):
    row = {
        "selection": "Kansas City Chiefs",
        "probability": Decimal("0.61"),
        "lower": Decimal("0.55"),
        "upper": Decimal("0.65"),
        "publishable": True,
        "specialist": "MLB_MODEL_V1",
        "terminal_label": "FINAL_APPROVED",
        "created_at": RECORDED_AT,
        "event_start": AUTHORITATIVE_LOCK,
    }
    row.update(overrides)
    return (
        row["selection"], row["probability"], row["lower"], row["upper"],
        row["publishable"], row["specialist"], row["terminal_label"],
        row["created_at"], row["event_start"],
    )


def test_nfl_publishable_specialist_probability_is_resolved_without_scout_probability_authority():
    cur = FakeCursor(_nfl_row())
    result = lookup_governed_probability(cur, _candidate())
    assert result["governed_selection"] == "Denver Broncos"
    assert result["governed_probability"] == 0.5796689600932923
    assert result["calibrated_lower_bound"] == 0.523750876984715
    assert result["calibrated_upper_bound"] == 0.64
    assert result["probability_publishable"] is True
    assert result["probability_source"] == "CONTROLLING_SPECIALIST"
    assert result["terminal_label"] == "FINAL_APPROVED"
    assert result["can_execute"] is False
    assert cur.calls[0][1][0] == "NFL:espn-401872931"


def test_board_row_keeps_legacy_scout_probability_null_and_formats_governed_score_beside_pick():
    cur = FakeCursor(_nfl_row())
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
    result = _display_row(FakeCursor(None), _candidate())
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


def test_malformed_scout_governance_never_displays_probability():
    cur = FakeCursor(_nfl_row())
    result = lookup_governed_probability(cur, _candidate(can_execute=True))
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "SCOUT_GOVERNANCE_INVALID"
    assert cur.calls == []


# --- Finding 1: the authoritative event lock is the ledger's own start time ---

def test_authoritative_event_lock_comes_from_the_ledger_row():
    late_candidate = _candidate(commence_time=AUTHORITATIVE_LOCK.replace(hour=3))
    cur = FakeCursor(_nfl_row(created_at=AUTHORITATIVE_LOCK.replace(hour=1)))
    result = lookup_governed_probability(cur, late_candidate)
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize(
    "created_at", [AUTHORITATIVE_LOCK, AUTHORITATIVE_LOCK.replace(hour=1), None, "naive"]
)
def test_receipt_not_recorded_before_the_event_lock_is_never_displayed(created_at):
    result = lookup_governed_probability(FakeCursor(_nfl_row(created_at=created_at)), _candidate())
    assert result["governed_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


# --- Finding 2: exact Decimal probability comparison ---

def test_exact_decimal_comparison_rejects_float_masked_bound_inversion():
    # Distinct Decimals that collapse to one float must still be rejected.
    row = _nfl_row(
        lower=Decimal("0.30000000000000004"),
        probability=Decimal("0.30000000000000003"),
    )
    result = lookup_governed_probability(FakeCursor(row), _candidate())
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize(
    ("probability", "lower", "upper"),
    [
        # float() of this valid Decimal is exactly 1.0.
        (Decimal("0.9999999999999999999"), Decimal("0.90"), Decimal("0.9999999999999999999")),
        # float is below 1, but two-decimal display rounds to 100.00%.
        (Decimal("0.9999999999999999"), Decimal("0.90"), Decimal("0.9999999999999999")),
        # float() of this valid lower bound is exactly 0.0.
        (Decimal("0.50"), Decimal("1e-400"), Decimal("0.60")),
        # Displayable float, but rounds to a 0.00% lower bound.
        (Decimal("0.50"), Decimal("1e-9"), Decimal("0.60")),
    ],
)
def test_values_rounding_to_a_display_boundary_are_never_displayed(probability, lower, upper):
    row = _nfl_row(probability=probability, lower=lower, upper=upper)
    result = lookup_governed_probability(FakeCursor(row), _candidate())
    assert result["governed_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"
    display = _display_row(FakeCursor(row), _candidate())
    assert display["probability_display"] == "—"
    assert display["lower_bound_display"] == "—"


def test_small_but_displayable_lower_bound_is_still_shown():
    result = lookup_governed_probability(
        FakeCursor(
            _nfl_row(
                probability=Decimal("0.50"),
                lower=Decimal("0.0001"),
                upper=Decimal("0.60"),
            )
        ),
        _candidate(),
    )
    assert result["governed_probability"] == 0.5
    assert _display_row(FakeCursor(_nfl_row(
        probability=Decimal("0.50"), lower=Decimal("0.0001"), upper=Decimal("0.60")
    )), _candidate())["lower_bound_display"] == "0.01%"


def test_lower_bound_above_point_is_never_displayed():
    result = lookup_governed_probability(
        FakeCursor(_nfl_row(lower=Decimal("0.70"))), _candidate()
    )
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


def test_upper_bound_below_point_is_never_displayed():
    result = lookup_governed_probability(
        FakeCursor(_nfl_row(upper=Decimal("0.40"))), _candidate()
    )
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


@pytest.mark.parametrize("probability", [0.0, 1.0, float("nan"), "not-a-number"])
def test_probability_outside_strict_domain_is_never_displayed(probability):
    result = lookup_governed_probability(
        FakeCursor(_nfl_row(probability=probability)), _candidate()
    )
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


# --- Finding 3: timestamp overflow is a hold, never an exception ---

@pytest.mark.parametrize(
    "overrides",
    [
        {"created_at": "0001-01-01T00:00:00+14:00"},
        {"event_start": "0001-01-01T00:00:00+14:00"},
        {"created_at": "9999-12-31T23:59:59-14:00"},
        {"event_start": "9999-12-31T23:59:59-14:00"},
    ],
)
def test_timestamp_overflow_in_recording_or_lock_is_rejected_not_raised(overrides):
    result = lookup_governed_probability(
        FakeCursor(_nfl_row(**overrides)), _candidate()
    )
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


# --- Finding 4: NFL terminal label provenance ---

def test_nfl_terminal_label_comes_from_the_authoritative_reducer():
    valid = lookup_governed_probability(FakeCursor(_nfl_row()), _candidate())
    assert valid["terminal_label"] == "FINAL_APPROVED"
    assert valid["probability_publishable"] is True

    for receipt in (
        {**APPROVED_RECEIPT, "terminal_label": "MODEL_QUALIFIED_HOLD"},
        {**APPROVED_RECEIPT, "status": "HOLD"},
        {**APPROVED_RECEIPT, "global_terminal_reducer": None},
        {**APPROVED_RECEIPT, "can_execute": True},
        None,
        {},
    ):
        held = lookup_governed_probability(FakeCursor(_nfl_row(receipt=receipt)), _candidate())
        assert held["governed_probability"] is None
        assert held["terminal_label"] is None
        assert held["specialist_status"] == "MODEL_OUTPUT_INVALID"


def test_selection_outside_this_exact_event_is_never_displayed():
    result = lookup_governed_probability(
        FakeCursor(_nfl_row(selection="Green Bay Packers")), _candidate()
    )
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"


# --- Positive/negative control for the second ledger ---

def test_valid_mlb_ledger_receipt_still_displays_under_strict_contract():
    candidate = _candidate(sport_key="baseball_mlb")
    result = lookup_governed_probability(FakeCursor(_mlb_row()), candidate)
    assert result["governed_probability"] == 0.61
    assert result["calibrated_lower_bound"] == 0.55
    assert result["calibrated_upper_bound"] == 0.65
    assert result["terminal_label"] == "FINAL_APPROVED"
    assert result["can_execute"] is False

    for label in (None, "MODEL_QUALIFIED_HOLD", ""):
        fabricated = lookup_governed_probability(
            FakeCursor(_mlb_row(terminal_label=label)), candidate
        )
        assert fabricated["governed_probability"] is None
        assert fabricated["specialist_status"] == "MODEL_OUTPUT_INVALID"


def test_mlb_lower_bound_above_point_is_never_displayed():
    candidate = _candidate(sport_key="baseball_mlb")
    result = lookup_governed_probability(
        FakeCursor(_mlb_row(lower=Decimal("0.90"))), candidate
    )
    assert result["governed_probability"] is None
    assert result["specialist_status"] == "MODEL_OUTPUT_INVALID"
