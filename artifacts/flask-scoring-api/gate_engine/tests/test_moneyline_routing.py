"""
test_moneyline_routing.py
WOW-PATCH-2026-08-07-OUTRIGHT-MONEYLINE-ROUTING

Deployment-blocking regression suite.  Every test below must pass before
the moneyline routing patch is considered shippable.

Coverage
--------
 1. MLB full-game moneyline → OUTRIGHT_WINNER, no prop L10 call
 2. WNBA moneyline requires no prop role ledger
 3. ATP match winner requires no MORE/LESS
 4. MMA bout winner requires no prop_type
 5. Soccer 1X2 preserves draw — binary conversion prohibited
 6. Accidental moneyline-to-prop route → RUN_INVALID_ROUTE_CONFIGURATION (not NO_PLAY)
 7. Publishable probability includes raw, calibrated, lower/upper bounds, audit
 8. Sportsbook odds cannot substitute when sport model unavailable
 9. Material starter/lineup change → STALE_MODEL_INVALIDATED
10. Same event on 3 sportsbooks → modeled once (not 3 candidates)
11. Boston Red Sox compatibility proof
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _mlb_outright_row(**overrides) -> dict:
    row = {
        "sport":       "MLB",
        "team":        "Boston Red Sox",
        "opponent":    "New York Yankees",
        "market_type": "h2h",
        "event_id":    "mlb-2026-08-07-BOS-NYY",
        "slate_date":  "2026-08-07",
        "board_source": "DraftKings",
    }
    row.update(overrides)
    return row


def _wnba_outright_row(**overrides) -> dict:
    row = {
        "sport":       "WNBA",
        "team":        "Las Vegas Aces",
        "opponent":    "New York Liberty",
        "market_type": "moneyline",
        "event_id":    "wnba-2026-08-07-LVA-NYL",
        "slate_date":  "2026-08-07",
    }
    row.update(overrides)
    return row


def _atp_outright_row(**overrides) -> dict:
    row = {
        "sport":       "ATP",
        "team":        "Carlos Alcaraz",
        "opponent":    "Jannik Sinner",
        "market_type": "match_winner",
        "event_id":    "atp-2026-08-07-ALC-SIN",
        "slate_date":  "2026-08-07",
    }
    row.update(overrides)
    return row


def _mma_outright_row(**overrides) -> dict:
    row = {
        "sport":       "MMA",
        "team":        "Jon Jones",
        "opponent":    "Stipe Miocic",
        "market_type": "bout_winner",
        "event_id":    "mma-2026-08-07-JON-STI",
        "slate_date":  "2026-08-07",
    }
    row.update(overrides)
    return row


def _soccer_1x2_row(**overrides) -> dict:
    row = {
        "sport":       "SOCCER",
        "team":        "Manchester City",
        "opponent":    "Arsenal",
        "market_type": "1x2",
        "outcome":     "draw",
        "event_id":    "soccer-2026-08-07-MCI-ARS",
        "slate_date":  "2026-08-07",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Test 1: MLB full-game moneyline → OUTRIGHT_WINNER, no prop L10 call
# ---------------------------------------------------------------------------

class TestMLBMoneylineClassification:
    def test_h2h_classifies_as_outright_winner(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _mlb_outright_row()
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_moneyline_key_also_classifies(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _mlb_outright_row(market_type="moneyline")
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_game_winner_key_classifies(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _mlb_outright_row(market_type="game_winner")
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_classify_row_stamps_objective(self):
        from gate_engine.market_family import classify_row, Objective
        row = _mlb_outright_row()
        classify_row(row)
        assert row["objective"] == Objective.OUTRIGHT_WIN_PROBABILITY_ONLY

    def test_classify_row_stamps_controlling_skill(self):
        from gate_engine.market_family import classify_row
        row = _mlb_outright_row()
        classify_row(row)
        assert row["controlling_skill_id"] == "wow.llp-moneyline-probability-expert"

    def test_classify_row_stamps_moneyline_v1_contract(self):
        from gate_engine.market_family import classify_row, InputContract
        row = _mlb_outright_row()
        classify_row(row)
        assert row["input_contract_version"] == InputContract.MONEYLINE_V1

    def test_outright_row_has_no_prop_l10_requirement(self):
        """OUTRIGHT_WINNER rows do NOT require l5_l10_ledger gate."""
        from gate_engine.route_registry import (
            MARKET_FAMILY_REQUIRED_GATES,
            UNIVERSAL_REQUIRED_GATES,
        )
        outright_gates = MARKET_FAMILY_REQUIRED_GATES.get("OUTRIGHT_WINNER", frozenset())
        assert "l5_l10_ledger" not in outright_gates
        assert "l5_l10_ledger" in UNIVERSAL_REQUIRED_GATES  # confirm it's in prop gates

    def test_outright_row_has_no_ev_gate_requirement(self):
        from gate_engine.route_registry import MARKET_FAMILY_REQUIRED_GATES
        outright_gates = MARKET_FAMILY_REQUIRED_GATES.get("OUTRIGHT_WINNER", frozenset())
        assert "ev_gate" not in outright_gates

    def test_route_id_contains_sport_and_market(self):
        from gate_engine.market_family import classify_row
        row = _mlb_outright_row()
        classify_row(row)
        assert "OUTRIGHT_WINNER" in row["route_id"]
        assert "MLB" in row["route_id"]


# ---------------------------------------------------------------------------
# Test 2: WNBA moneyline requires no prop role ledger
# ---------------------------------------------------------------------------

class TestWNBAMoneylineNoRoleLedger:
    def test_wnba_moneyline_classifies_as_outright(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _wnba_outright_row()
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_wnba_outright_no_player_role_required(self):
        """MONEYLINE_V1 contract must not require player_role field."""
        from gate_engine.market_family import MONEYLINE_V1_REQUIRED_FIELDS
        assert "player_role" not in MONEYLINE_V1_REQUIRED_FIELDS

    def test_wnba_outright_prohibits_player_role(self):
        """player_role is a PROHIBITED field in MONEYLINE_V1."""
        from gate_engine.market_family import MONEYLINE_V1_PROHIBITED_FIELDS
        assert "player_role" in MONEYLINE_V1_PROHIBITED_FIELDS

    def test_wnba_outright_with_player_role_fails_contract(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _wnba_outright_row(player_role="STARTER")
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert any("PROHIBITED_FIELD_PRESENT:player_role" in v for v in violations)

    def test_wnba_valid_contract_passes(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _wnba_outright_row()
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert violations == [], f"Expected no violations, got: {violations}"


# ---------------------------------------------------------------------------
# Test 3: ATP match winner requires no MORE/LESS
# ---------------------------------------------------------------------------

class TestATPMatchWinnerNoMoreLess:
    def test_atp_match_winner_classifies_as_outright(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _atp_outright_row()
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_moneyline_v1_prohibits_direction(self):
        """direction (MORE/LESS) is a PROHIBITED field in MONEYLINE_V1."""
        from gate_engine.market_family import MONEYLINE_V1_PROHIBITED_FIELDS
        assert "direction" in MONEYLINE_V1_PROHIBITED_FIELDS

    def test_atp_row_with_direction_fails_contract(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _atp_outright_row(direction="MORE")
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert any("PROHIBITED_FIELD_PRESENT:direction" in v for v in violations)

    def test_atp_row_with_line_fails_contract(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _atp_outright_row(line=1.5)
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert any("PROHIBITED_FIELD_PRESENT:line" in v for v in violations)

    def test_atp_valid_contract_passes(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _atp_outright_row()
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert violations == [], f"Unexpected violations: {violations}"

    def test_atp_model_is_active(self):
        from gate_engine.moneyline_probability import get_model_for_sport, ModelStatus
        model = get_model_for_sport("ATP")
        assert model["status"] == ModelStatus.ACTIVE


# ---------------------------------------------------------------------------
# Test 4: MMA bout winner requires no prop_type
# ---------------------------------------------------------------------------

class TestMMABoutWinnerNoPropType:
    def test_mma_bout_winner_classifies_as_outright(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _mma_outright_row()
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_moneyline_v1_prohibits_prop_type(self):
        from gate_engine.market_family import MONEYLINE_V1_PROHIBITED_FIELDS
        assert "prop_type" in MONEYLINE_V1_PROHIBITED_FIELDS

    def test_moneyline_v1_prohibits_stat_key(self):
        from gate_engine.market_family import MONEYLINE_V1_PROHIBITED_FIELDS
        assert "stat_key" in MONEYLINE_V1_PROHIBITED_FIELDS

    def test_mma_row_with_prop_type_fails_contract(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _mma_outright_row(prop_type="knockouts")
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert any("PROHIBITED_FIELD_PRESENT:prop_type" in v for v in violations)

    def test_mma_valid_contract_passes(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _mma_outright_row()
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert violations == [], f"Unexpected violations: {violations}"

    def test_mma_model_is_active(self):
        from gate_engine.moneyline_probability import get_model_for_sport, ModelStatus
        model = get_model_for_sport("MMA")
        assert model["status"] == ModelStatus.ACTIVE

    def test_fight_winner_key_classifies(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _mma_outright_row(market_type="fight_winner")
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER


# ---------------------------------------------------------------------------
# Test 5: Soccer 1X2 preserves draw — binary conversion prohibited
# ---------------------------------------------------------------------------

class TestSoccer1X2DrawPreservation:
    def test_1x2_classifies_as_outright_winner(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = _soccer_1x2_row()
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_draw_outcome_is_preserved(self):
        """draw must not be collapsed into MORE or LESS."""
        from gate_engine.moneyline_probability import compute_1x2_three_state
        result = compute_1x2_three_state(0.45, 0.25, 0.30)
        assert result["type"] == "THREE_STATE_1X2"
        assert result["p_draw"] > 0
        assert result["binary_conversion_prohibited"] is True
        assert result["draw_is_distinct_outcome"] is True

    def test_draw_probability_nonzero_in_result(self):
        from gate_engine.moneyline_probability import compute_1x2_three_state
        result = compute_1x2_three_state(0.40, 0.28, 0.32)
        assert result["p_draw"] > 0

    def test_binary_direction_on_1x2_row_fails_contract(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _soccer_1x2_row(direction="MORE")
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert any("BINARY_CONVERSION_PROHIBITED" in v for v in violations)

    def test_missing_outcome_fails_contract(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _soccer_1x2_row()
        row.pop("outcome", None)
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert any("SOCCER_1X2_MISSING_OUTCOME_FIELD" in v for v in violations)

    def test_valid_draw_outcome_passes_contract(self):
        from gate_engine.market_family import validate_moneyline_v1_contract, classify_row
        row = _soccer_1x2_row(outcome="draw")
        classify_row(row)
        violations = validate_moneyline_v1_contract(row)
        assert violations == [], f"Unexpected violations: {violations}"

    def test_home_outcome_accepted(self):
        from gate_engine.moneyline_probability import validate_soccer_1x2_outcome
        row = _soccer_1x2_row(outcome="home")
        assert validate_soccer_1x2_outcome(row) == []

    def test_away_outcome_accepted(self):
        from gate_engine.moneyline_probability import validate_soccer_1x2_outcome
        row = _soccer_1x2_row(outcome="away")
        assert validate_soccer_1x2_outcome(row) == []

    def test_1x2_probabilities_sum_to_one(self):
        from gate_engine.moneyline_probability import compute_1x2_three_state
        result = compute_1x2_three_state(0.50, 0.25, 0.25)
        total = result["p_home"] + result["p_draw"] + result["p_away"]
        assert abs(total - 1.0) < 0.01

    def test_1x2_rejects_probabilities_that_dont_sum_to_one(self):
        from gate_engine.moneyline_probability import compute_1x2_three_state
        with pytest.raises(ValueError, match="do not sum to 1.0"):
            compute_1x2_three_state(0.9, 0.9, 0.9)


# ---------------------------------------------------------------------------
# Test 6: Route guard and mixed-batch partitioning
# WOW-PATCH-2026-08-07-BATCH-PARTITION-FIX
#
# Old behaviour: guard_route_config() rejected OUTRIGHT + PROP mixed batches
# wholesale (HTTP 409), collaterally blocking valid prop rows.
#
# New behaviour: guard_route_config() only rejects an explicit body-level
# contract mismatch (caller declared PLAYER_PROP but rows are OUTRIGHT_WINNER).
# Mixed batches are partitioned by partition_and_validate_outright_rows(),
# which validates each lane independently and returns per-row failure records
# for invalid moneyline rows — valid prop rows are completely unaffected.
# ---------------------------------------------------------------------------

class TestMixedRoutingGuard:
    # ── guard_route_config() behaviour ──────────────────────────────────────

    def test_mixed_outright_and_player_prop_guard_returns_none(self):
        """
        Fix #3: guard_route_config() must NOT reject mixed batches wholesale.
        A request containing both OUTRIGHT_WINNER and PLAYER_PROP rows should
        return None (no error) — partitioning handles per-lane validation.
        """
        from gate_engine.market_family import classify_row, guard_route_config, MarketFamily
        outright = _mlb_outright_row()
        player_prop = {
            "sport": "MLB", "player": "Mookie Betts",
            "prop_type": "hits", "line": 1.5, "direction": "MORE",
        }
        classify_row(outright)
        classify_row(player_prop)
        assert outright["market_family"]     == MarketFamily.OUTRIGHT_WINNER
        assert player_prop["market_family"]  == MarketFamily.PLAYER_PROP

        err = guard_route_config([outright, player_prop])
        assert err is None, (
            f"guard_route_config must return None for mixed batches (partitioning "
            f"handles per-lane isolation), got: {err}"
        )

    def test_guard_does_not_reject_mixed_batch_with_no_body_contract(self):
        """No body contract declared → guard must pass the mixed batch through."""
        from gate_engine.market_family import classify_row, guard_route_config
        outright = _mlb_outright_row()
        prop = {"sport": "MLB", "player": "X", "prop_type": "hits",
                "line": 1.5, "direction": "MORE"}
        classify_row(outright)
        classify_row(prop)
        err = guard_route_config([outright, prop])
        assert err is None

    def test_body_contract_player_prop_with_outright_rows_returns_error(self):
        """
        Rule 2 (only remaining batch-wide guard): caller explicitly declares
        PLAYER_PROP but rows classify as OUTRIGHT_WINNER → HTTP 409.
        This is an unambiguous intent mismatch the caller must fix.
        """
        from gate_engine.market_family import classify_row, guard_route_config, InputContract
        outright = _mlb_outright_row()
        classify_row(outright)
        err = guard_route_config([outright], body_input_contract=InputContract.PLAYER_PROP)
        assert err is not None
        assert err["code"]           == "RUN_INVALID_ROUTE_CONFIGURATION"
        assert err["primary_blocker"] == "MONEYLINE_ROUTED_TO_PROP_CONTRACT"
        assert err["can_execute"]     is False
        assert err["candidate_evaluation_completed"] is False

    def test_body_contract_error_is_not_no_play(self):
        """A routing configuration error must never resolve to NO_PLAY."""
        from gate_engine.market_family import classify_row, guard_route_config, InputContract
        outright = _mlb_outright_row()
        classify_row(outright)
        err = guard_route_config([outright], body_input_contract=InputContract.PLAYER_PROP)
        assert err is not None
        assert err.get("terminal_disposition") != "NO_PLAY"
        assert err.get("terminal_label")       != "NO_PLAY"

    def test_clean_outright_only_batch_passes_guard(self):
        from gate_engine.market_family import classify_row, guard_route_config
        rows = [_mlb_outright_row(), _atp_outright_row()]
        for r in rows:
            classify_row(r)
        err = guard_route_config(rows)
        assert err is None, f"Expected no error, got: {err}"

    def test_clean_prop_only_batch_passes_guard(self):
        from gate_engine.market_family import classify_row, guard_route_config
        rows = [
            {"sport": "MLB", "player": "A", "prop_type": "hits", "line": 1.5, "direction": "MORE"},
            {"sport": "NBA", "player": "B", "prop_type": "points", "line": 22.5, "direction": "MORE"},
        ]
        for r in rows:
            classify_row(r)
        err = guard_route_config(rows)
        assert err is None

    def test_guard_passes_outright_with_contract_violation_in_mixed_batch(self):
        """
        A moneyline row that has a contract violation (e.g. prohibited 'line' field)
        should NOT cause guard_route_config to reject the whole batch.
        Partition handles the per-row failure.
        """
        from gate_engine.market_family import classify_row, guard_route_config
        # outright row with a prohibited field
        bad_outright = _mlb_outright_row(line=1.5)   # 'line' is prohibited in MONEYLINE_V1
        prop         = {"sport": "MLB", "player": "Mookie Betts",
                        "prop_type": "hits", "line": 1.5, "direction": "MORE"}
        classify_row(bad_outright)
        classify_row(prop)
        err = guard_route_config([bad_outright, prop])
        assert err is None   # guard passes; partition captures the per-row failure


# ---------------------------------------------------------------------------
# Test 6b: partition_and_validate_outright_rows — per-row isolation
# ---------------------------------------------------------------------------

class TestMixedBatchPartitioning:
    """
    Verifies Fix #3: partition_and_validate_outright_rows() isolates
    moneyline lane failures to the specific row that failed.
    """

    def _valid_outright(self, **kw):
        from gate_engine.market_family import classify_row
        row = _mlb_outright_row(**kw)
        classify_row(row)
        return row

    def _invalid_outright_missing_event_id(self, **kw):
        """Valid structure except missing required event_id."""
        from gate_engine.market_family import classify_row
        row = _mlb_outright_row(**kw)
        row.pop("event_id", None)
        classify_row(row)
        return row

    def _invalid_outright_prohibited_field(self, **kw):
        """Valid structure except has prohibited 'line' field."""
        from gate_engine.market_family import classify_row
        row = _mlb_outright_row(line=1.5, **kw)
        classify_row(row)
        return row

    # ── partition() basics ───────────────────────────────────────────────

    def test_partition_returns_valid_and_invalid_keys(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        result = partition_and_validate_outright_rows([])
        assert "valid"   in result
        assert "invalid" in result

    def test_all_valid_rows_in_valid_partition(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        rows   = [self._valid_outright(), self._valid_outright(opponent="Houston Astros")]
        result = partition_and_validate_outright_rows(rows)
        assert len(result["valid"])   == 2
        assert len(result["invalid"]) == 0

    def test_invalid_row_goes_to_invalid_partition(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        row    = self._invalid_outright_missing_event_id()
        result = partition_and_validate_outright_rows([row])
        assert len(result["valid"])   == 0
        assert len(result["invalid"]) == 1

    def test_invalid_entry_has_row_and_violations_keys(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        row    = self._invalid_outright_missing_event_id()
        result = partition_and_validate_outright_rows([row])
        entry  = result["invalid"][0]
        assert "row"        in entry
        assert "violations" in entry

    def test_violations_list_is_nonempty_for_invalid_row(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        row    = self._invalid_outright_missing_event_id()
        result = partition_and_validate_outright_rows([row])
        assert len(result["invalid"][0]["violations"]) > 0

    def test_missing_required_field_violation_is_specific(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        row    = self._invalid_outright_missing_event_id()
        result = partition_and_validate_outright_rows([row])
        violations = result["invalid"][0]["violations"]
        assert any("MISSING_REQUIRED_FIELD:event_id" in v for v in violations), (
            f"Expected specific MISSING_REQUIRED_FIELD:event_id, got: {violations}"
        )

    def test_prohibited_field_violation_is_specific(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        row    = self._invalid_outright_prohibited_field()
        result = partition_and_validate_outright_rows([row])
        violations = result["invalid"][0]["violations"]
        assert any("PROHIBITED_FIELD_PRESENT:line" in v for v in violations), (
            f"Expected specific PROHIBITED_FIELD_PRESENT:line, got: {violations}"
        )

    # ── The critical isolation invariant ────────────────────────────────

    def test_one_invalid_moneyline_does_not_affect_other_valid_rows(self):
        """
        Core Fix #3 invariant: 1 invalid moneyline row + 2 valid moneyline rows
        in the same partition call → only the invalid row ends up in invalid,
        valid rows are completely unaffected.
        """
        from gate_engine.market_family import partition_and_validate_outright_rows
        valid1   = self._valid_outright()
        valid2   = self._valid_outright(opponent="Houston Astros",
                                         event_id="mlb-2026-08-07-BOS-HOU")
        invalid  = self._invalid_outright_missing_event_id()
        result   = partition_and_validate_outright_rows([valid1, invalid, valid2])
        assert len(result["valid"])   == 2
        assert len(result["invalid"]) == 1

    def test_empty_input_returns_empty_partitions(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        result = partition_and_validate_outright_rows([])
        assert result["valid"]   == []
        assert result["invalid"] == []

    def test_partition_preserves_original_row_in_invalid_entry(self):
        from gate_engine.market_family import partition_and_validate_outright_rows
        row        = self._invalid_outright_missing_event_id()
        row_id_val = id(row)   # identity check
        result     = partition_and_validate_outright_rows([row])
        assert id(result["invalid"][0]["row"]) == row_id_val

    def test_can_execute_semantics_preserved_in_isolation(self):
        """
        partition_and_validate_outright_rows never scores rows — it only
        partitions.  can_execute is stamped on the failure entry by the
        caller (app.py), not here.  Verify the module itself doesn't override.
        """
        from gate_engine.market_family import partition_and_validate_outright_rows
        row    = self._invalid_outright_missing_event_id()
        result = partition_and_validate_outright_rows([row])
        entry  = result["invalid"][0]
        # partition() must not stamp can_execute on the row itself
        assert "can_execute" not in entry


# ---------------------------------------------------------------------------
# Test 6c: Canonical alias expansion (Fix #1) — display aliases classify correctly
# ---------------------------------------------------------------------------

class TestMarketTypeAliasExpansion:
    """
    Fix #1: Common display aliases ("outright", "win", "game", "winner", …)
    must classify as OUTRIGHT_WINNER without requiring the GPT to know the
    canonical internal key.
    """

    @pytest.mark.parametrize("market_type", [
        "outright",
        "outright win",
        "outright_win",
        "win",
        "winner",
        "game",
        "game win",
        "game_win",
        "full game",
        "full_game",
        "match",
    ])
    def test_display_alias_classifies_as_outright_winner(self, market_type):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = {"sport": "MLB", "team": "Boston Red Sox",
               "opponent": "New York Yankees", "market_type": market_type,
               "event_id": "test-event", "slate_date": "2026-08-07"}
        result = classify_market_family(row)
        assert result == MarketFamily.OUTRIGHT_WINNER, (
            f"market_type={market_type!r} expected OUTRIGHT_WINNER, got {result!r}"
        )

    def test_case_insensitive_alias_resolves(self):
        from gate_engine.market_family import classify_market_family, MarketFamily
        row = {"sport": "MLB", "team": "BOS", "opponent": "NYY",
               "market_type": "OUTRIGHT", "event_id": "e", "slate_date": "2026-08-07"}
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_alias_row_has_no_prop_fields_after_classify_row(self):
        from gate_engine.market_family import classify_row
        row = {"sport": "MLB", "team": "BOS", "opponent": "NYY",
               "market_type": "outright", "event_id": "e", "slate_date": "2026-08-07"}
        classify_row(row)
        assert row["market_family"] == "OUTRIGHT_WINNER"
        assert "l5_values"   not in row
        assert "stat_key"    not in row


# ---------------------------------------------------------------------------
# Test 6d: Declared-intent override (Fix #1) — explicit contract/objective wins
# ---------------------------------------------------------------------------

class TestDeclaredIntentOverride:
    """
    Fix #1: If a row explicitly carries input_contract_version=MONEYLINE_V1 or
    objective=OUTRIGHT_WIN_PROBABILITY_ONLY, it must be classified as
    OUTRIGHT_WINNER even when market_type is missing or ambiguous.
    """

    def test_input_contract_moneyline_v1_overrides_missing_market_type(self):
        from gate_engine.market_family import classify_market_family, MarketFamily, InputContract
        row = {"sport": "MLB", "team": "BOS", "opponent": "NYY",
               "input_contract_version": InputContract.MONEYLINE_V1,
               "event_id": "e", "slate_date": "2026-08-07"}
        # No market_type set — must still classify as OUTRIGHT_WINNER
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_objective_outright_win_probability_only_overrides_missing_market_type(self):
        from gate_engine.market_family import classify_market_family, MarketFamily, Objective
        row = {"sport": "NBA", "team": "Lakers", "opponent": "Celtics",
               "objective": Objective.OUTRIGHT_WIN_PROBABILITY_ONLY,
               "event_id": "e", "slate_date": "2026-08-07"}
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_declared_intent_wins_over_player_prop_signals(self):
        """
        Even if the row has player/line/direction signals that would normally
        trigger PLAYER_PROP (step 4), the declared-intent override (step 1.5)
        must fire first and classify as OUTRIGHT_WINNER.
        """
        from gate_engine.market_family import classify_market_family, MarketFamily, InputContract
        row = {
            "sport":                  "MLB",
            "team":                   "BOS",
            "opponent":               "NYY",
            "input_contract_version": InputContract.MONEYLINE_V1,
            "event_id":               "e",
            "slate_date":             "2026-08-07",
            # Prop-like signals that would normally trigger PLAYER_PROP:
            "player":     "Some Player",
            "line":       1.5,
            "direction":  "MORE",
        }
        assert classify_market_family(row) == MarketFamily.OUTRIGHT_WINNER

    def test_declared_intent_does_not_bypass_moneyline_v1_contract_validation(self):
        """
        Declared intent classifies correctly — but the row still has to pass
        MONEYLINE_V1 contract validation in partition_and_validate_outright_rows().
        A row with prohibited fields is still invalid even with declared intent.
        """
        from gate_engine.market_family import (
            classify_row, partition_and_validate_outright_rows, InputContract
        )
        row = {
            "sport":                  "MLB",
            "team":                   "BOS",
            "opponent":               "NYY",
            "input_contract_version": InputContract.MONEYLINE_V1,
            "event_id":               "e",
            "slate_date":             "2026-08-07",
            "line":     1.5,      # PROHIBITED
            "direction": "MORE",  # PROHIBITED
        }
        classify_row(row)
        result = partition_and_validate_outright_rows([row])
        assert len(result["invalid"]) == 1
        violations = result["invalid"][0]["violations"]
        assert any("PROHIBITED_FIELD_PRESENT:line"      in v for v in violations)
        assert any("PROHIBITED_FIELD_PRESENT:direction" in v for v in violations)


# ---------------------------------------------------------------------------
# Test 7: Publishable probability includes raw, calibrated, bounds, audit
# ---------------------------------------------------------------------------

class TestPublishableProbabilityContract:
    def _score_mlb(self, enrichment=None):
        from gate_engine.market_family import classify_row
        from gate_engine.moneyline_probability import score_outright_winner_row
        row = _mlb_outright_row()
        classify_row(row)
        enr = enrichment or {
            "sportsbook_odds": [
                {"team": "Boston Red Sox", "odds": -130},
                {"team": "Boston Red Sox", "odds": -125},
            ]
        }
        return score_outright_winner_row(row, enrichment=enr)

    def test_probability_snapshot_present(self):
        result = self._score_mlb()
        assert result["probability_snapshot"] is not None

    def test_raw_probability_in_snapshot(self):
        result = self._score_mlb()
        snap = result["probability_snapshot"]
        assert "raw_probability" in snap

    def test_calibrated_probability_in_snapshot(self):
        result = self._score_mlb()
        snap = result["probability_snapshot"]
        assert "calibrated_probability" in snap

    def test_lower_bound_in_snapshot(self):
        result = self._score_mlb()
        snap = result["probability_snapshot"]
        assert "lower_bound" in snap

    def test_upper_bound_in_snapshot(self):
        result = self._score_mlb()
        snap = result["probability_snapshot"]
        assert "upper_bound" in snap

    def test_probability_audit_in_snapshot(self):
        result = self._score_mlb()
        snap = result["probability_snapshot"]
        assert "probability_audit" in snap
        assert "passed" in snap["probability_audit"]

    def test_snapshot_hash_present(self):
        result = self._score_mlb()
        snap = result["probability_snapshot"]
        assert "snapshot_hash" in snap
        assert len(snap["snapshot_hash"]) > 0

    def test_can_execute_false_in_result(self):
        result = self._score_mlb()
        assert result["can_execute"] is False

    def test_can_approve_bets_false(self):
        result = self._score_mlb()
        assert result.get("can_approve_bets") is False

    def test_objective_is_outright_win_probability_only(self):
        result = self._score_mlb()
        assert result["objective"] == "OUTRIGHT_WIN_PROBABILITY_ONLY"

    def test_controlling_skill_is_moneyline_expert(self):
        result = self._score_mlb()
        assert result["controlling_skill"] == "wow.llp-moneyline-probability-expert"

    def test_bounds_are_ordered_correctly(self):
        result = self._score_mlb()
        snap = result["probability_snapshot"]
        lo, hi = snap.get("lower_bound"), snap.get("upper_bound")
        if lo is not None and hi is not None:
            assert lo <= hi

    def test_probability_audit_reflects_market_observation_only_contract(self):
        """
        When only sportsbook odds are provided (no independent model data),
        the independent model is not run.  The pipeline returns a bounded
        market-observation result with independent_probability=None.

        audit.passed is correctly False in this case because raw_probability
        is unavailable — market data cannot substitute for the independent model.
        The snapshot is still returned (not a crash) with a valid hash.

        This is the documented MARKET_OBSERVATION_ONLY contract:
          independent_probability = None
          calibrated_probability  = market_no_vig (bounded observation)
          terminal_label          = MODEL_QUALIFIED_HOLD (cannot qualify)
          audit.passed            = False
        """
        result = self._score_mlb()
        snap   = result["probability_snapshot"]
        audit  = snap.get("probability_audit", {})
        # Contract: audit is present and is a structured object
        assert isinstance(audit, dict)
        assert "passed" in audit
        # Market-only rows do not pass the full probability audit —
        # that requires an independent model probability.
        assert audit["passed"] is False
        # But the score is still returned (not a crash) with a valid hash.
        assert snap.get("snapshot_hash") and len(snap["snapshot_hash"]) > 0


# ---------------------------------------------------------------------------
# Test 8: Sportsbook odds cannot substitute when sport model unavailable
# ---------------------------------------------------------------------------

class TestSportModelUnavailableNoOddsSubstitute:
    def test_unavailable_model_returns_no_registered_model_blocker(self):
        from gate_engine.market_family import classify_row
        from gate_engine.moneyline_probability import score_outright_winner_row
        row = {
            "sport":       "CRICKET",
            "team":        "India",
            "opponent":    "Australia",
            "market_type": "match_winner",
            "event_id":    "cricket-2026-IND-AUS",
            "slate_date":  "2026-08-07",
        }
        classify_row(row)
        # Provide rich sportsbook odds — these must not substitute for the model
        enr = {"sportsbook_odds": [{"team": "India", "odds": -150}]}
        result = score_outright_winner_row(row, enrichment=enr)
        assert any("NO_REGISTERED_MODEL" in b for b in result["blockers"])

    def test_unavailable_model_terminal_is_not_qualifying(self):
        from gate_engine.market_family import classify_row
        from gate_engine.moneyline_probability import score_outright_winner_row, ModelStatus
        row = {
            "sport":       "CRICKET",
            "team":        "India",
            "opponent":    "Australia",
            "market_type": "match_winner",
            "event_id":    "cricket-2026-IND-AUS",
            "slate_date":  "2026-08-07",
        }
        classify_row(row)
        result = score_outright_winner_row(row)
        assert result["terminal_label"] not in ("FINAL_APPROVED", "MONEY_QUALIFIED",
                                                 "MARKET_VERIFIED_HOLD")

    def test_audit_flags_unavailable_model_substitution_attempt(self):
        from gate_engine.moneyline_probability import audit_probability, ModelStatus
        # Simulate: sportsbook odds available but model is UNAVAILABLE
        audit = audit_probability(
            raw_probability=0.55,       # odds-derived, not model-derived
            calibrated_probability=0.55,
            lower_bound=0.50,
            upper_bound=0.60,
            model_status=ModelStatus.UNAVAILABLE,
        )
        assert audit["passed"] is False
        assert any(
            "sportsbook_odds_cannot_substitute" in note
            for note in audit["audit_notes"]
        )

    def test_model_unavailable_probability_not_publishable(self):
        from gate_engine.moneyline_probability import audit_probability, ModelStatus
        audit = audit_probability(
            raw_probability=None,
            calibrated_probability=None,
            lower_bound=None,
            upper_bound=None,
            model_status=ModelStatus.UNAVAILABLE,
        )
        assert audit["probability_publishable"] is False


# ---------------------------------------------------------------------------
# Test 9: Material starter/lineup change → STALE_MODEL_INVALIDATED
# ---------------------------------------------------------------------------

class TestStaleModelInvalidation:
    def test_starting_pitcher_change_invalidates_snapshot(self):
        from gate_engine.moneyline_probability import check_stale_model
        row = _mlb_outright_row()
        enrichment = {"starting_pitcher_home": "Garrett Crochet"}
        prior_snapshot = {"starting_pitcher_home": "Nick Pivetta"}   # different SP
        result = check_stale_model(row, enrichment, prior_snapshot)
        assert result["stale"] is True
        assert result["disposition"] == "STALE_MODEL_INVALIDATED"
        assert "Starting pitcher changed" in result["reason"]

    def test_matching_pitcher_is_not_stale(self):
        from gate_engine.moneyline_probability import check_stale_model
        row = _mlb_outright_row()
        enrichment = {"starting_pitcher_home": "Garrett Crochet"}
        prior_snapshot = {"starting_pitcher_home": "Garrett Crochet"}
        result = check_stale_model(row, enrichment, prior_snapshot)
        assert result["stale"] is False

    def test_key_player_going_out_invalidates_snapshot(self):
        from gate_engine.moneyline_probability import check_stale_model
        row = _mlb_outright_row()
        enrichment  = {"out_players": ["Mookie Betts"]}
        prior_snap  = {"active_key_players": ["Mookie Betts", "Rafael Devers"]}
        result = check_stale_model(row, enrichment, prior_snap)
        assert result["stale"] is True
        assert result["disposition"] == "STALE_MODEL_INVALIDATED"

    def test_no_prior_snapshot_is_not_stale(self):
        from gate_engine.moneyline_probability import check_stale_model
        row = _mlb_outright_row()
        result = check_stale_model(row, {}, prior_snapshot=None)
        assert result["stale"] is False
        assert result["disposition"] == "NO_PRIOR"

    def test_stale_model_returns_invalidated_terminal(self):
        from gate_engine.market_family import classify_row
        from gate_engine.moneyline_probability import score_outright_winner_row
        row = _mlb_outright_row()
        classify_row(row)
        prior = {"starting_pitcher_home": "Old Pitcher"}
        enr   = {"starting_pitcher_home": "New Pitcher"}
        result = score_outright_winner_row(row, enrichment=enr, prior_snapshot=prior)
        assert result["terminal_label"] == "STALE_MODEL_INVALIDATED"
        assert any("STALE_MODEL_INVALIDATED" in b for b in result["blockers"])


# ---------------------------------------------------------------------------
# Test 10: Same event on 3 sportsbooks → modeled once
# ---------------------------------------------------------------------------

class TestEventDeduplication:
    def _make_three_appearances(self):
        base = _mlb_outright_row()
        rows = []
        for platform, odds in [("DraftKings", -130), ("FanDuel", -128), ("BetMGM", -132)]:
            r = dict(base)
            r["board_source"] = platform
            r["odds"]         = odds
            r["row_id"]       = f"row-{platform}"
            rows.append(r)
        return rows

    def test_three_sportsbook_appearances_deduplicate_to_one(self):
        from gate_engine.moneyline_probability import deduplicate_events
        rows = self._make_three_appearances()
        deduped, dedup_map = deduplicate_events(rows)
        assert len(deduped) == 1, (
            f"Expected 1 canonical row, got {len(deduped)}: {deduped}"
        )

    def test_dedup_map_records_all_three_source_ids(self):
        from gate_engine.moneyline_probability import deduplicate_events
        rows = self._make_three_appearances()
        _, dedup_map = deduplicate_events(rows)
        all_ids = [id_ for ids in dedup_map.values() for id_ in ids]
        assert len(all_ids) == 3

    def test_canonical_row_preserves_all_platform_appearances(self):
        from gate_engine.moneyline_probability import deduplicate_events
        rows = self._make_three_appearances()
        deduped, _ = deduplicate_events(rows)
        appearances = deduped[0].get("platform_appearances", [])
        platforms = {a["platform"] for a in appearances}
        assert "DraftKings" in platforms
        assert "FanDuel"    in platforms
        assert "BetMGM"     in platforms

    def test_different_events_not_deduplicated(self):
        from gate_engine.moneyline_probability import deduplicate_events
        row1 = _mlb_outright_row()    # BOS vs NYY
        row2 = _mlb_outright_row(team="Houston Astros", opponent="Texas Rangers",
                                  event_id="mlb-2026-08-07-HOU-TEX")
        row1["row_id"] = "r1"
        row2["row_id"] = "r2"
        deduped, _ = deduplicate_events([row1, row2])
        assert len(deduped) == 2

    def test_single_row_not_deduplicated(self):
        from gate_engine.moneyline_probability import deduplicate_events
        rows = [_mlb_outright_row()]
        deduped, _ = deduplicate_events(rows)
        assert len(deduped) == 1

    def test_dedup_is_idempotent(self):
        from gate_engine.moneyline_probability import deduplicate_events
        rows = self._make_three_appearances()
        deduped_once, _  = deduplicate_events(rows)
        deduped_twice, _ = deduplicate_events(deduped_once)
        assert len(deduped_twice) == 1


# ---------------------------------------------------------------------------
# Test 11: Boston Red Sox compatibility proof
# ---------------------------------------------------------------------------

class TestBostonRedSoxCompatibilityProof:
    """
    Canonical acceptance test for the OUTRIGHT_WINNER / MONEYLINE_V1 route.

    A Boston Red Sox full-game moneyline row must demonstrate:
    - market_family = OUTRIGHT_WINNER
    - objective = OUTRIGHT_WIN_PROBABILITY_ONLY
    - controlling_skill_id = wow.llp-moneyline-probability-expert
    - input_contract_version = MONEYLINE_V1
    - route_compatibility.compatibility = PASS
    """

    def _bos_row(self):
        from gate_engine.market_family import classify_row
        row = _mlb_outright_row()   # BOS vs NYY, h2h
        classify_row(row)
        return row

    def test_market_family_outright_winner(self):
        row = self._bos_row()
        from gate_engine.market_family import MarketFamily
        assert row["market_family"] == MarketFamily.OUTRIGHT_WINNER

    def test_objective_outright_win_probability_only(self):
        row = self._bos_row()
        from gate_engine.market_family import Objective
        assert row["objective"] == Objective.OUTRIGHT_WIN_PROBABILITY_ONLY

    def test_controlling_skill_moneyline_expert(self):
        row = self._bos_row()
        assert row["controlling_skill_id"] == "wow.llp-moneyline-probability-expert"

    def test_input_contract_moneyline_v1(self):
        row = self._bos_row()
        from gate_engine.market_family import InputContract
        assert row["input_contract_version"] == InputContract.MONEYLINE_V1

    def test_route_compatibility_pass(self):
        from gate_engine.market_family import check_route_compatibility, classify_row
        row = _mlb_outright_row()
        classify_row(row)
        compat = check_route_compatibility(row)
        assert compat.passed is True, f"Expected PASS, got violations: {compat.violations}"

    def test_route_compatibility_dict_compatibility_field(self):
        from gate_engine.market_family import check_route_compatibility, classify_row
        row = _mlb_outright_row()
        classify_row(row)
        compat_dict = check_route_compatibility(row).to_dict()
        assert compat_dict["compatibility"] == "PASS"

    def test_route_compatibility_dict_all_required_fields(self):
        from gate_engine.market_family import check_route_compatibility, classify_row
        from gate_engine.route_registry import ROUTE_COMPATIBILITY_FIELDS
        row = _mlb_outright_row()
        classify_row(row)
        compat_dict = check_route_compatibility(row).to_dict()
        for f in ROUTE_COMPATIBILITY_FIELDS:
            assert f in compat_dict, f"Missing required field: {f}"

    def test_required_field_profile_moneyline_v1(self):
        row = self._bos_row()
        assert row["required_field_profile"] == "MONEYLINE_V1"

    def test_can_execute_false_throughout(self):
        from gate_engine.market_family import check_route_compatibility, classify_row
        row = _mlb_outright_row()
        classify_row(row)
        assert row.get("can_execute") is None  # not stamped by classify_row itself
        compat_dict = check_route_compatibility(row).to_dict()
        assert compat_dict["can_execute"] is False

    def test_no_prop_pipeline_fields_in_classify_output(self):
        """classify_row must not stamp l5_l10_ledger, game_log, or prop-specific fields."""
        from gate_engine.market_family import classify_row
        row = _mlb_outright_row()
        classify_row(row)
        assert "l5_l10_ledger" not in row
        assert "game_log"      not in row
        assert "stat_key"      not in row

    def test_scoring_objective_stamp_is_immutable(self):
        """A second classify_row call on an already-classified OUTRIGHT_WINNER
        must not change the objective to something else."""
        from gate_engine.market_family import classify_row, Objective
        row = _mlb_outright_row()
        classify_row(row)
        classify_row(row)   # second call
        assert row["objective"] == Objective.OUTRIGHT_WIN_PROBABILITY_ONLY


# ---------------------------------------------------------------------------
# Module invariant: can_execute is False everywhere
# ---------------------------------------------------------------------------

class TestCanExecuteInvariant:
    def test_market_family_module_can_execute_false(self):
        from gate_engine import market_family
        assert market_family.can_execute is False

    def test_moneyline_probability_module_can_execute_false(self):
        from gate_engine import moneyline_probability
        assert moneyline_probability.can_execute is False

    def test_route_registry_can_execute_false(self):
        from gate_engine.route_registry import can_execute
        assert can_execute is False

    def test_scored_result_can_execute_false(self):
        from gate_engine.market_family import classify_row
        from gate_engine.moneyline_probability import score_outright_winner_row
        row = _mlb_outright_row()
        classify_row(row)
        result = score_outright_winner_row(row)
        assert result["can_execute"] is False
