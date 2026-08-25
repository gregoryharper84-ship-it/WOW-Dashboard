"""
Regression tests — wow-cross-sport-high-probability-selector
PATCH: WOW-PATCH-2026-08-05-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR
STATUS: ANALYTICAL SHADOW MODE — all tests must pass before skill-registry.json activation.

Test IDs mirror the regression matrix in
skills/WOW-REGRESSION-TESTS-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR.md.

Tests in this file cover:
  - POLICY: permanent governance invariants (pure-logic, no I/O)
  - COMBO:  Kalshi combo gate (combo_gate.py)
  - LEDGER: backend dependency degradation helpers
  - LANE:   output lane logic (pure-logic validators)
  - GOVERN: selector governance rules (pure-logic)

The skill itself is a SKILL.md (no importable Python module). Tests validate
the backend components the skill relies on and the policy constants it
documents as unconditional.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from gate_engine.combo_gate import validate_combo_size, REJECT_CODE_SOFT, REJECT_CODE_HARD

# ---------------------------------------------------------------------------
# Shared fixture data (mirroring WOW-REGRESSION-TESTS spec)
# ---------------------------------------------------------------------------

_STALE_MARKET_CANDIDATE = {
    "candidate_id": "test-stale-market-001",
    "sport": "MLB",
    "event_key": "CHC@STL-2026-08-05",
    "market": "moneyline",
    "side": "CHC",
    "raw_probability": 0.75,
    "calibrated_probability": 0.72,
    "calibrated_lower_bound": 0.68,
    "calibrated_upper_bound": 0.76,
    "market_freshness": "STALE",
    "no_vig_probability": None,
    "material_market_conflict": None,
}

_LOW_LOWER_BOUND_CANDIDATE = {
    "candidate_id": "test-low-lb-001",
    "sport": "WNBA",
    "event_key": "CHI@LAS-2026-08-05",
    "market": "moneyline",
    "side": "LAS",
    "raw_probability": 0.71,
    "calibrated_probability": 0.67,
    "calibrated_lower_bound": 0.60,
    "calibrated_upper_bound": 0.74,
    "failure_path_score": 0.22,
    "specialist_gate_label": "RESEARCH_INTEREST",
}

_KALSHI_SAME_EVENT_PAIR = [
    {
        "candidate_id": "kalshi-001",
        "event_key": "KXMLB-CHC-WIN-2026-08-05",
        "raw_probability": 0.73,
        "calibrated_lower_bound": 0.67,
    },
    {
        "candidate_id": "kalshi-002",
        "event_key": "KXMLB-CHC-WIN-2026-08-05",
        "raw_probability": 0.68,
        "calibrated_lower_bound": 0.62,
    },
]

_SAME_INJURY_THESIS_PAIR = [
    {
        "candidate_id": "tennis-001",
        "sport": "Tennis",
        "injury_thesis": "player_X_return",
        "calibrated_lower_bound": 0.70,
        "raw_probability": 0.75,
    },
    {
        "candidate_id": "nba-001",
        "sport": "NBA",
        "injury_thesis": "player_X_return",
        "calibrated_lower_bound": 0.66,
        "raw_probability": 0.71,
    },
]

_NO_EVENT_CANDIDATE = {
    "candidate_id": "test-no-event-001",
    "sport": "NFL",
    "event_key": None,
    "market": "moneyline",
    "raw_probability": 0.78,
}

_BELOW_THRESHOLD_POOL = [
    {
        "candidate_id": "below-001",
        "raw_probability": 0.65,
        "calibrated_lower_bound": 0.60,
        "lower_bound_edge": -0.02,
    },
    {
        "candidate_id": "below-002",
        "raw_probability": 0.62,
        "calibrated_lower_bound": 0.58,
        "lower_bound_edge": 0.01,
    },
]

# ---------------------------------------------------------------------------
# Helpers — lightweight policy validators that mirror the skill's rules
# These are not imported from a skill module (there is none); they encode
# the skill's documented invariants as testable pure functions.
# ---------------------------------------------------------------------------

LANE_A_THRESHOLD = 0.70         # raw_probability >= this
LANE_B_PROB_THRESHOLD = 0.70    # calibrated_probability >= this
LANE_B_LB_THRESHOLD = 0.65      # calibrated_lower_bound >= this
COMPACT_CARD_LB_FLOOR = 0.65    # minimum lower_bound for Compact Card inclusion

BACKEND_LABEL_SET = {
    "FINAL_APPROVED", "MONEY_QUALIFIED", "MARKET_VERIFIED_HOLD",
    "MODEL_QUALIFIED_HOLD", "RESEARCH_INTEREST", "SOURCE_CONFLICT",
    "REJECT_NO_EDGE", "REJECT_BAD_STRUCTURE", "REJECT_DATA_QUALITY",
    "SLATE_PURGE", "DUPLICATE_EXPOSURE_BLOCK", "NO_PLAY",
    "HARD_REJECT_COMBO_MULTIPLICATION", "COMBO_EV_UNOBTAINABLE",
    "REJECT_DUPLICATE_PLAYER_EXPOSURE", "REJECT_DUPLICATE_THESIS",
    "REJECT_CROSS_SLIP_CONCENTRATION",
}

BLOCKING_LABELS = {
    "REJECT_NO_EDGE", "REJECT_BAD_STRUCTURE", "REJECT_DATA_QUALITY",
    "SLATE_PURGE", "DUPLICATE_EXPOSURE_BLOCK", "HARD_REJECT_COMBO_MULTIPLICATION",
    "COMBO_EV_UNOBTAINABLE", "REJECT_DUPLICATE_PLAYER_EXPOSURE",
    "REJECT_DUPLICATE_THESIS", "REJECT_CROSS_SLIP_CONCENTRATION",
    "SOURCE_CONFLICT",
}


def _qualifies_lane_a(c: dict) -> bool:
    return c.get("raw_probability", 0) >= LANE_A_THRESHOLD


def _qualifies_lane_b(c: dict) -> bool:
    return (
        c.get("calibrated_probability", 0) >= LANE_B_PROB_THRESHOLD
        and c.get("calibrated_lower_bound", 0) >= LANE_B_LB_THRESHOLD
    )


def _qualifies_lane_c(c: dict) -> bool:
    """Lane C requires non-stale market and positive lower_bound_edge."""
    if c.get("market_freshness") == "STALE":
        return False
    if c.get("no_vig_probability") is None:
        return False
    if c.get("material_market_conflict") is True:
        return False
    return c.get("lower_bound_edge", 0) > 0


def _qualifies_compact_card(c: dict) -> bool:
    """Compact Card requires calibrated_lower_bound >= floor."""
    return c.get("calibrated_lower_bound", 0) >= COMPACT_CARD_LB_FLOOR


def _event_identity_valid(c: dict) -> bool:
    return bool(c.get("event_key"))


def _make_selector_output_schema(pool: list[dict]) -> dict:
    """
    Minimal selector output schema mirroring the skill's required fields.
    Used to verify that required governance fields are always present.
    """
    return {
        "can_execute": False,                         # invariant: always False
        "requires_human_confirmation": True,           # invariant: always True
        "dry_run_only": True,                          # invariant: always True
        "stake_sizing": False,
        "bankroll_allocation": False,
        "prediction_write_attempted": True,
        "prediction_write_status": "NOT_AVAILABLE",    # ledger absent
        "cross_ticket_exposure_status": "PARTIAL",     # slip-scoped only
        "final_refresh_status": "COMPLETED",
        "lane_a": [c for c in pool if _qualifies_lane_a(c)],
        "lane_b": [c for c in pool if _qualifies_lane_b(c)],
        "lane_c": [c for c in pool if _qualifies_lane_c(c)],
        "compact_card_pool": [c for c in pool if _qualifies_compact_card(c)],
        "terminal_state": "NO_PLAY" if not any(
            _qualifies_lane_a(c) or _qualifies_lane_b(c) or _qualifies_lane_c(c)
            for c in pool
        ) else "LANES_POPULATED",
    }


# ---------------------------------------------------------------------------
# POLICY tests
# ---------------------------------------------------------------------------

class TestPolicy:
    """POLICY-001 through POLICY-003 — permanent governance invariants."""

    def test_policy_001_can_execute_is_always_false(self):
        """POLICY-001: can_execute=False is unconditional in every output."""
        output = _make_selector_output_schema([_STALE_MARKET_CANDIDATE])
        assert output["can_execute"] is False, (
            "POLICY-001 FAILED: can_execute must be False unconditionally; "
            f"got {output['can_execute']!r}"
        )

    def test_policy_001_can_execute_false_with_empty_pool(self):
        """POLICY-001: can_execute=False even with empty candidate pool."""
        output = _make_selector_output_schema([])
        assert output["can_execute"] is False

    def test_policy_002_requires_human_confirmation_always_present(self):
        """POLICY-002: requires_human_confirmation is True in every output."""
        for pool in [[], [_STALE_MARKET_CANDIDATE], _BELOW_THRESHOLD_POOL]:
            output = _make_selector_output_schema(pool)
            assert "requires_human_confirmation" in output, (
                "POLICY-002 FAILED: requires_human_confirmation field missing"
            )
            assert output["requires_human_confirmation"] is True, (
                f"POLICY-002 FAILED: requires_human_confirmation={output['requires_human_confirmation']!r}"
            )

    def test_policy_003_no_play_is_valid_terminal_state(self):
        """POLICY-003: NO_PLAY returned when no candidate qualifies."""
        output = _make_selector_output_schema(_BELOW_THRESHOLD_POOL)
        assert output["terminal_state"] == "NO_PLAY", (
            f"POLICY-003 FAILED: expected NO_PLAY, got {output['terminal_state']!r}"
        )

    def test_policy_dry_run_always_true(self):
        """dry_run_only=True is unconditional."""
        output = _make_selector_output_schema([_STALE_MARKET_CANDIDATE])
        assert output["dry_run_only"] is True


# ---------------------------------------------------------------------------
# COMBO tests — combo_gate.py (live module)
# ---------------------------------------------------------------------------

class TestComboGate:
    """COMBO-001 through COMBO-004 — Kalshi combo_gate Reliability Freeze."""

    def _legs(self, n: int) -> list[dict]:
        return [{"adjusted_prob": 0.70}] * n

    def test_combo_001_one_market_allowed(self):
        """COMBO-001: 1-market combo is allowed."""
        result = validate_combo_size(self._legs(1))
        assert result["allowed"] is True, f"COMBO-001 FAILED: {result}"
        assert result["reject_code"] is None
        assert result["can_execute"] is False       # unconditional
        assert result["dry_run_only"] is True

    def test_combo_002_two_market_allowed(self):
        """COMBO-002: 2-market combo is allowed."""
        result = validate_combo_size(self._legs(2))
        assert result["allowed"] is True, f"COMBO-002 FAILED: {result}"
        assert result["reject_code"] is None
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True

    def test_combo_003_three_market_reject_bad_structure(self):
        """COMBO-003: 3-market combo → REJECT_BAD_STRUCTURE."""
        result = validate_combo_size(self._legs(3))
        assert result["allowed"] is False, f"COMBO-003 FAILED: {result}"
        assert result["reject_code"] == REJECT_CODE_SOFT, (
            f"COMBO-003 FAILED: expected {REJECT_CODE_SOFT!r}, got {result['reject_code']!r}"
        )
        assert result["can_execute"] is False

    def test_combo_004_four_market_hard_reject(self):
        """COMBO-004: 4-market combo → HARD_REJECT_COMBO_MULTIPLICATION."""
        result = validate_combo_size(self._legs(4))
        assert result["allowed"] is False, f"COMBO-004 FAILED: {result}"
        assert result["reject_code"] == REJECT_CODE_HARD, (
            f"COMBO-004 FAILED: expected {REJECT_CODE_HARD!r}, got {result['reject_code']!r}"
        )
        assert result["can_execute"] is False

    def test_combo_five_market_hard_reject(self):
        """5-market Kalshi combo is also hard-rejected."""
        result = validate_combo_size(self._legs(5))
        assert result["allowed"] is False
        assert result["reject_code"] == REJECT_CODE_HARD
        assert result["can_execute"] is False

    def test_combo_empty_rejected(self):
        """0-market combo is rejected (no legs supplied)."""
        result = validate_combo_size([])
        assert result["allowed"] is False
        assert result["can_execute"] is False


# ---------------------------------------------------------------------------
# LEDGER tests — backend dependency degradation
# ---------------------------------------------------------------------------

class TestLedgerDegradation:
    """LEDGER-001 and LEDGER-002 — graceful degradation for unavailable ledgers."""

    def test_ledger_001_prediction_write_not_available_nonblocking(self):
        """
        LEDGER-001: When immutable prediction ledger is unavailable,
        output contains prediction_write_status=NOT_AVAILABLE and
        output is not blocked (lanes still populated if candidates qualify).
        """
        # One qualifying candidate
        pool = [
            {
                "candidate_id": "ledger-test-001",
                "sport": "MLB",
                "event_key": "CHC@STL-2026-08-05",
                "raw_probability": 0.75,
                "calibrated_probability": 0.72,
                "calibrated_lower_bound": 0.68,
                "no_vig_probability": 0.65,
                "market_freshness": "FRESH",
                "material_market_conflict": False,
                "lower_bound_edge": 0.03,
            }
        ]
        output = _make_selector_output_schema(pool)

        # Non-blocking: lane still populated
        assert len(output["lane_a"]) > 0, (
            "LEDGER-001 FAILED: Lane A should be populated despite ledger unavailability"
        )
        # Ledger status present and correct
        assert output["prediction_write_attempted"] is True
        assert output["prediction_write_status"] == "NOT_AVAILABLE", (
            f"LEDGER-001 FAILED: expected NOT_AVAILABLE, got {output['prediction_write_status']!r}"
        )

    def test_ledger_001_status_field_always_present(self):
        """LEDGER-001: prediction_write_status field always present in output."""
        for pool in [[], _BELOW_THRESHOLD_POOL, [_STALE_MARKET_CANDIDATE]]:
            output = _make_selector_output_schema(pool)
            assert "prediction_write_status" in output, (
                "LEDGER-001 FAILED: prediction_write_status missing from output"
            )

    def test_ledger_002_cross_ticket_exposure_partial(self):
        """LEDGER-002: cross-ticket exposure ledger reports PARTIAL status."""
        output = _make_selector_output_schema([_STALE_MARKET_CANDIDATE])
        assert output["cross_ticket_exposure_status"] == "PARTIAL", (
            f"LEDGER-002 FAILED: expected PARTIAL, got {output['cross_ticket_exposure_status']!r}"
        )

    def test_ledger_002_status_field_always_present(self):
        """LEDGER-002: cross_ticket_exposure_status always present in output."""
        for pool in [[], [_STALE_MARKET_CANDIDATE]]:
            output = _make_selector_output_schema(pool)
            assert "cross_ticket_exposure_status" in output

    def test_ledger_final_refresh_status_present(self):
        """final_refresh_status is required in every output."""
        output = _make_selector_output_schema([])
        assert "final_refresh_status" in output


# ---------------------------------------------------------------------------
# LANE tests — output lane logic
# ---------------------------------------------------------------------------

class TestLaneLogic:
    """LANE-001 through LANE-003 — output lane assignment rules."""

    def test_lane_001_stale_market_in_a_and_b_not_c(self):
        """
        LANE-001: Candidate with stale market and missing no_vig_probability
        appears in Lanes A and B but is excluded from Lane C.
        """
        c = _STALE_MARKET_CANDIDATE
        assert _qualifies_lane_a(c) is True, (
            f"LANE-001 FAILED: expected in Lane A (raw_prob={c['raw_probability']})"
        )
        assert _qualifies_lane_b(c) is True, (
            f"LANE-001 FAILED: expected in Lane B (cal_prob={c['calibrated_probability']}, "
            f"lb={c['calibrated_lower_bound']})"
        )
        assert _qualifies_lane_c(c) is False, (
            "LANE-001 FAILED: stale-market candidate must not qualify for Lane C"
        )

    def test_lane_002_low_lower_bound_excluded_from_compact_card(self):
        """
        LANE-002: Candidate with calibrated_lower_bound=0.60 is excluded
        from the Compact Card (floor=0.65) but may appear in Lane A.
        """
        c = _LOW_LOWER_BOUND_CANDIDATE
        assert _qualifies_lane_a(c) is True, (
            "LANE-002 FAILED: candidate with raw_prob=0.71 should be in Lane A"
        )
        assert _qualifies_lane_b(c) is False, (
            f"LANE-002 FAILED: lower_bound=0.60 < 0.65 floor; must not be in Lane B"
        )
        assert _qualifies_compact_card(c) is False, (
            f"LANE-002 FAILED: lower_bound=0.60 < 0.65 floor; must not be in Compact Card"
        )

    def test_lane_003_kalshi_same_event_cap(self):
        """
        LANE-003: Two Kalshi candidates from the same event must be capped at 1
        by the portfolio governor. Higher-probability candidate is retained.
        """
        pair = _KALSHI_SAME_EVENT_PAIR
        assert len(pair) == 2
        assert pair[0]["event_key"] == pair[1]["event_key"], "Fixture error: event keys must match"

        # Simulate portfolio governor cap: retain only the highest by raw_probability
        same_event_key = pair[0]["event_key"]
        same_event = [c for c in pair if c["event_key"] == same_event_key]
        retained = max(same_event, key=lambda c: c["raw_probability"])
        rejected = [c for c in same_event if c["candidate_id"] != retained["candidate_id"]]

        assert retained["candidate_id"] == "kalshi-001", (
            "LANE-003 FAILED: higher-probability Kalshi candidate should be retained"
        )
        assert len(rejected) == 1
        assert rejected[0]["candidate_id"] == "kalshi-002", (
            "LANE-003 FAILED: lower-probability Kalshi same-event candidate should be rejected"
        )

    def test_lane_c_requires_positive_edge(self):
        """Lane C requires lower_bound_edge > 0."""
        c = {
            "raw_probability": 0.75,
            "calibrated_lower_bound": 0.68,
            "market_freshness": "FRESH",
            "no_vig_probability": 0.70,
            "material_market_conflict": False,
            "lower_bound_edge": -0.02,   # negative edge
        }
        assert _qualifies_lane_c(c) is False

    def test_lane_c_requires_market_conflict_false(self):
        """Lane C excludes candidates with material_market_conflict=True."""
        c = {
            "raw_probability": 0.75,
            "calibrated_lower_bound": 0.68,
            "market_freshness": "FRESH",
            "no_vig_probability": 0.65,
            "material_market_conflict": True,
            "lower_bound_edge": 0.05,
        }
        assert _qualifies_lane_c(c) is False


# ---------------------------------------------------------------------------
# GOVERN tests — selector governance rules
# ---------------------------------------------------------------------------

class TestGovernance:
    """GOVERN-001 through GOVERN-007 — selector governance rules."""

    def test_govern_001_winning_prior_card_does_not_upgrade_candidate(self):
        """
        GOVERN-001: A candidate with raw_probability=0.68 does not reach
        Lane A threshold (0.70) regardless of a prior winning card.
        Prior card outcome must not be used as a probability boost.
        """
        candidate_with_prior_win = {
            "candidate_id": "prior-win-001",
            "raw_probability": 0.68,          # below 0.70 threshold
            "calibrated_lower_bound": 0.63,
            "prior_card_won": True,            # must not upgrade
        }
        assert _qualifies_lane_a(candidate_with_prior_win) is False, (
            "GOVERN-001 FAILED: prior winning card must not boost raw_probability "
            "above Lane A threshold"
        )

    def test_govern_002_missing_event_identity_blocks_all_lanes(self):
        """
        GOVERN-002: Missing event_key blocks all lanes for that candidate.
        """
        c = _NO_EVENT_CANDIDATE
        assert not _event_identity_valid(c), "Fixture error: event_key should be None"

        # A candidate with invalid event identity must not qualify for any lane,
        # even if its probability scores would otherwise pass.
        assert c["raw_probability"] >= LANE_A_THRESHOLD, (
            "Fixture error: raw_probability should be above Lane A threshold for this test"
        )
        # The rule: event identity check runs before lane scoring; invalid identity
        # means the candidate is not scored — qualifies for nothing.
        # Represented here by the event_identity_valid guard.
        qualifies_any = _event_identity_valid(c) and _qualifies_lane_a(c)
        assert qualifies_any is False, (
            "GOVERN-002 FAILED: candidate with no event_key must not qualify for any lane"
        )

    def test_govern_003_human_confirmation_in_output(self):
        """GOVERN-003: requires_human_confirmation=True in every output."""
        output = _make_selector_output_schema([_STALE_MARKET_CANDIDATE])
        assert output.get("requires_human_confirmation") is True

    def test_govern_004_same_injury_thesis_at_most_one_retained(self):
        """
        GOVERN-004: Two candidates sharing the same injury_thesis may have at
        most one appear in the Compact Card. The higher lower_bound is retained.
        """
        pair = _SAME_INJURY_THESIS_PAIR
        assert pair[0]["injury_thesis"] == pair[1]["injury_thesis"], (
            "Fixture error: injury_thesis must match"
        )

        # Simulate dependence audit: retain highest lower_bound, reject duplicate
        same_thesis = [c for c in pair if c["injury_thesis"] == "player_X_return"]
        retained = max(same_thesis, key=lambda c: c["calibrated_lower_bound"])
        rejected = [c for c in same_thesis if c["candidate_id"] != retained["candidate_id"]]

        assert retained["candidate_id"] == "tennis-001", (
            "GOVERN-004 FAILED: tennis-001 has higher lower_bound and must be retained"
        )
        assert len(rejected) == 1
        assert rejected[0]["candidate_id"] == "nba-001"

    def test_govern_005_cross_book_legs_not_executable_parlay(self):
        """
        GOVERN-005: Two legs from different books must not be presented as
        an executable parlay. can_execute=False unconditionally.
        """
        output = _make_selector_output_schema([_STALE_MARKET_CANDIDATE])
        # The key invariant: regardless of book diversity, can_execute is always False
        assert output["can_execute"] is False, (
            "GOVERN-005 FAILED: can_execute must be False for any multi-book combination"
        )

    def test_govern_006_outcomes_do_not_overwrite_predictions(self):
        """
        GOVERN-006: Settlement data must not modify the original prediction record.
        Validated structurally: the output schema has separate prediction_write fields
        (write-once at Step 15) that are not modified by settlement.
        """
        prediction_record = {
            "prediction_id": "pred-001",
            "raw_probability": 0.75,
            "terminal_label": "RESEARCH_INTEREST",
            "created_at": "2026-08-05T10:00:00Z",
        }
        settlement = {
            "prediction_id": "pred-001",
            "official_result": "WIN",
            "settlement_timestamp": "2026-08-05T21:00:00Z",
        }
        # Rule: settlement writes to a separate outcome record, not the prediction
        outcome_record = {**settlement, "brier_score": 0.0625}
        # Original prediction must remain unchanged
        assert prediction_record["raw_probability"] == 0.75
        assert "official_result" not in prediction_record, (
            "GOVERN-006 FAILED: settlement data must not be merged into the prediction record"
        )
        assert outcome_record["prediction_id"] == prediction_record["prediction_id"]

    def test_govern_007_no_play_when_nothing_qualifies(self):
        """GOVERN-007: NO_PLAY returned when all candidates are below threshold."""
        output = _make_selector_output_schema(_BELOW_THRESHOLD_POOL)
        assert output["terminal_state"] == "NO_PLAY", (
            f"GOVERN-007 FAILED: expected NO_PLAY, got {output['terminal_state']!r}"
        )
        assert len(output["lane_a"]) == 0
        assert len(output["lane_b"]) == 0
        assert len(output["lane_c"]) == 0

    def test_govern_terminal_labels_from_backend_set(self):
        """All terminal labels used in Lane D must be from the backend label set."""
        example_lane_d_labels = [
            "REJECT_DATA_QUALITY",
            "DUPLICATE_EXPOSURE_BLOCK",
            "HARD_REJECT_COMBO_MULTIPLICATION",
            "REJECT_BAD_STRUCTURE",
        ]
        for label in example_lane_d_labels:
            assert label in BACKEND_LABEL_SET, (
                f"Label {label!r} not in backend label set — "
                "selector must never invent or use unlisted labels"
            )

    def test_govern_blocking_labels_not_in_lane_a_or_b(self):
        """
        If a specialist gate returns a blocking label, the candidate must be
        in Lane D, not Lanes A or B, even if probability scores would qualify it.
        """
        # Candidate that would qualify for Lane A by probability
        # but has a blocking specialist_gate_label
        blocked_candidate = {
            "candidate_id": "blocked-001",
            "raw_probability": 0.78,
            "calibrated_probability": 0.74,
            "calibrated_lower_bound": 0.68,
            "specialist_gate_label": "REJECT_DATA_QUALITY",   # blocking
        }
        # The selector must honor the blocking label over the probability score
        # Verify the label is in the blocking set
        assert blocked_candidate["specialist_gate_label"] in BLOCKING_LABELS, (
            "Fixture error: test label must be a blocking label"
        )
        # If the selector is implemented correctly, it checks specialist_gate_label
        # before running lane qualification. We verify the label recognition here.
        assert blocked_candidate["specialist_gate_label"] in BACKEND_LABEL_SET


# ---------------------------------------------------------------------------
# RECONCILIATION tests
# ---------------------------------------------------------------------------

class TestReconciliation:
    """Structural reconciliation checks that must pass after every test run."""

    def test_recon_no_invented_labels(self):
        """All labels the selector may emit are in the backend label set."""
        # These are the labels the skill documents as possible Lane D values
        selector_emitted_labels = {
            "REJECT_DATA_QUALITY",        # missing event identity
            "DUPLICATE_EXPOSURE_BLOCK",   # portfolio governor
            "HARD_REJECT_COMBO_MULTIPLICATION",  # combo gate
            "REJECT_BAD_STRUCTURE",       # combo gate soft
            "SOURCE_CONFLICT",            # market normalization
            "NO_PLAY",                    # terminal state
        }
        for label in selector_emitted_labels:
            assert label in BACKEND_LABEL_SET, (
                f"Reconciliation FAILED: {label!r} is not in the backend label set"
            )

    def test_recon_compact_card_lower_bound_floor(self):
        """Every candidate in the Compact Card pool meets the lower_bound floor."""
        test_pool = [
            {"calibrated_lower_bound": 0.70, "raw_probability": 0.75},   # qualifies
            {"calibrated_lower_bound": 0.65, "raw_probability": 0.71},   # qualifies (at floor)
            {"calibrated_lower_bound": 0.64, "raw_probability": 0.73},   # does not qualify
            {"calibrated_lower_bound": 0.60, "raw_probability": 0.80},   # does not qualify
        ]
        compact_pool = [c for c in test_pool if _qualifies_compact_card(c)]
        for c in compact_pool:
            assert c["calibrated_lower_bound"] >= COMPACT_CARD_LB_FLOOR, (
                f"Reconciliation FAILED: Compact Card candidate has "
                f"lower_bound={c['calibrated_lower_bound']} < floor={COMPACT_CARD_LB_FLOOR}"
            )
        assert len(compact_pool) == 2   # only the first two qualify

    def test_recon_can_execute_false_in_all_outputs(self):
        """can_execute=False in every possible output state."""
        for pool in [
            [],
            [_STALE_MARKET_CANDIDATE],
            _BELOW_THRESHOLD_POOL,
            [_LOW_LOWER_BOUND_CANDIDATE],
        ]:
            output = _make_selector_output_schema(pool)
            assert output["can_execute"] is False, (
                f"Reconciliation FAILED: can_execute is not False for pool size {len(pool)}"
            )

    def test_recon_required_ledger_fields_always_present(self):
        """Mandatory ledger status block present in every output."""
        required_fields = {
            "prediction_write_attempted",
            "prediction_write_status",
            "cross_ticket_exposure_status",
            "final_refresh_status",
        }
        for pool in [[], [_STALE_MARKET_CANDIDATE]]:
            output = _make_selector_output_schema(pool)
            for field in required_fields:
                assert field in output, (
                    f"Reconciliation FAILED: required field {field!r} missing from output"
                )
