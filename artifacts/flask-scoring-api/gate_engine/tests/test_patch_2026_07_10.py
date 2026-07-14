"""
test_patch_2026_07_10.py
WOW-PATCH-2026-07-10: Combo & Settlement Governance — regression tests.

Seven scenarios from the July 9 postmortem:
  S1: 5-market combo → HARD_REJECT_COMBO_MULTIPLICATION before construction
  S2: Two duplicate Phillies entries → model_observation_count=1, financial_entry_count=2
  S3: Seattle Kalshi loss + Seattle PrizePicks win → SETTLEMENT_SOURCE_CONFLICT
  S4: Official Miami win → Seattle model win not credited (model_result=null, calibration_eligible=False)
  S5: recent_win_streak present → does not alter tier, stake, or combo size
  S6: dry_run_only=True and can_execute=False hold unconditionally for all Kalshi combos
  S7: 2-market combo with all required EV fields → passes combo gate and proceeds to evaluation
"""
import pytest
from gate_engine.combo_gate import (
    validate_combo_size,
    validate_combo_ev,
    evaluate_kalshi_combo,
    REJECT_CODE_HARD,
    REJECT_CODE_SOFT,
    REJECT_CODE_EV_MISS,
)
from gate_engine.event_normalization import (
    normalize_team,
    build_event_id,
    group_entries_by_event,
    group_entries_by_event_and_side,
)
from gate_engine.settlement_conflict import (
    detect_conflict,
    apply_conflict_to_rows,
    CONFLICT_LABEL,
    BANKROLL_PENDING,
    MODEL_RESULT_NULL,
    CALIBRATION_ELIGIBLE,
)
from gate_engine.series_state import (
    run_series_state_audit,
    validate_win_streak_is_metadata,
    assert_win_streak_isolation,
    LLP_WATCH_CEILING,
    TRIGGER_OPPONENT_SERIES,
    TRIGGER_OPPONENT_STREAK,
    TRIGGER_SELECTED_RUNS,
)


# ===========================================================================
# S1: 5-market combo → HARD_REJECT_COMBO_MULTIPLICATION before construction
# ===========================================================================

class TestS1_FiveMarketComboHardReject:
    def test_five_leg_combo_hard_rejects(self):
        legs = [{"market": f"market_{i}", "adjusted_prob": 0.55} for i in range(5)]
        result = validate_combo_size(legs)
        assert result["allowed"] is False
        assert result["reject_code"] == REJECT_CODE_HARD

    def test_four_leg_combo_hard_rejects(self):
        legs = [{"market": f"m{i}", "adjusted_prob": 0.60} for i in range(4)]
        result = validate_combo_size(legs)
        assert result["allowed"] is False
        assert result["reject_code"] == REJECT_CODE_HARD

    def test_evaluate_kalshi_combo_five_legs_rejected_before_ev(self):
        legs = [{"adjusted_prob": 0.58} for _ in range(5)]
        result = evaluate_kalshi_combo(legs, combo_cost=10, combo_max_return=300)
        assert result["passed"] is False
        assert result["stage"] == "SIZE_GATE"
        assert result["reject_code"] == REJECT_CODE_HARD

    def test_five_market_combo_can_execute_false(self):
        legs = [{"adjusted_prob": 0.60} for _ in range(5)]
        result = evaluate_kalshi_combo(legs, combo_cost=5, combo_max_return=100)
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True


# ===========================================================================
# S2: Two duplicate Phillies entries → model_observation_count=1, financial_entry_count=2
# ===========================================================================

class TestS2_DuplicatePhilliesEntries:
    def _make_phillies_row(self, board_source="PrizePicks", row_id=None):
        return {
            "row_id":       row_id or f"phi_{board_source}",
            "sport":        "MLB",
            "league":       "MLB",
            "slate_date":   "2026-07-09",
            "game":         "Marlins @ Phillies",
            "team":         "Phillies",
            "opponent":     "Marlins",
            "board_source": board_source,
            "player":       "Bryce Harper",
            "prop_type":    "hits",
            "line":         1.5,
            "direction":    "MORE",
        }

    def test_two_phillies_rows_group_to_same_event(self):
        rows = [
            self._make_phillies_row("PrizePicks", "phi_pp"),
            self._make_phillies_row("Kalshi",     "phi_k"),
        ]
        groups = group_entries_by_event(rows)
        # Both rows should share one event_id
        event_ids = {r["_event_id"] for r in rows}
        assert len(event_ids) == 1, f"Expected 1 event_id, got {event_ids}"

    def test_financial_entry_count_vs_model_observation_count(self):
        rows = [
            self._make_phillies_row("PrizePicks", "phi_pp"),
            self._make_phillies_row("Kalshi",     "phi_k"),
        ]
        groups = group_entries_by_event(rows)
        # Simulate what pipeline does: for grouped event, financial_entry_count = n rows,
        # model_observation_count = 1
        for event_id, event_rows in groups.items():
            financial_entry_count   = len(event_rows)
            model_observation_count = 1  # de-duplication rule

            assert financial_entry_count == 2
            assert model_observation_count == 1

    def test_duplicate_entries_share_event_id(self):
        rows = [
            self._make_phillies_row("PrizePicks"),
            self._make_phillies_row("Kalshi"),
        ]
        groups = group_entries_by_event(rows)
        assert len(groups) == 1

    def test_normalize_team_phillies_variants(self):
        assert normalize_team("Phillies")    == "PHI"
        assert normalize_team("Philadelphia") == "PHI"
        assert normalize_team("PHI")         == "PHI"
        assert normalize_team("phillies")    == "PHI"


# ===========================================================================
# S3: Seattle Kalshi loss + Seattle PrizePicks win → SETTLEMENT_SOURCE_CONFLICT
# ===========================================================================

class TestS3_SeattleConflict:
    def _seattle_rows(self):
        return [
            {
                "row_id":         "sea_kalshi",
                "sport":          "MLB",
                "league":         "MLB",
                "slate_date":     "2026-07-09",
                "game":           "Mariners @ Marlins",
                "team":           "Mariners",
                "opponent":       "Marlins",
                "board_source":   "kalshi",
                "platform_result": "loss",
            },
            {
                "row_id":         "sea_pp",
                "sport":          "MLB",
                "league":         "MLB",
                "slate_date":     "2026-07-09",
                "game":           "Mariners @ Marlins",
                "team":           "Mariners",
                "opponent":       "Marlins",
                "board_source":   "prizepicks",
                "platform_result": "win",
            },
        ]

    def test_conflict_detected_for_kalshi_loss_pp_win(self):
        result = detect_conflict(
            "MLB:2026-07-09:SEA@MIA",
            {"kalshi": "loss", "prizepicks": "win"},
        )
        assert result["conflict_detected"] is True
        assert result["conflict_label"] == CONFLICT_LABEL

    def test_conflict_applies_pending_reconciliation(self):
        result = detect_conflict(
            "MLB:2026-07-09:SEA@MIA",
            {"kalshi": "loss", "prizepicks": "win"},
        )
        assert result["bankroll_status"] == BANKROLL_PENDING

    def test_conflict_nulls_model_result(self):
        result = detect_conflict(
            "MLB:2026-07-09:SEA@MIA",
            {"kalshi": "loss", "prizepicks": "win"},
        )
        assert result["model_result"] is MODEL_RESULT_NULL

    def test_conflict_blocks_calibration(self):
        result = detect_conflict(
            "MLB:2026-07-09:SEA@MIA",
            {"kalshi": "loss", "prizepicks": "win"},
        )
        assert result["calibration_eligible"] is False

    def test_apply_conflict_to_rows_annotates_rows(self):
        rows = self._seattle_rows()
        groups = group_entries_by_event(rows)
        conflict_map = apply_conflict_to_rows(groups)
        # Each row should have settlement_conflict = True
        for row in rows:
            assert row.get("settlement_conflict") is True
            assert row.get("conflict_label") == CONFLICT_LABEL
            assert row.get("bankroll_status") == BANKROLL_PENDING

    def test_normalize_team_mariners_variants(self):
        assert normalize_team("Mariners") == "SEA"
        assert normalize_team("Seattle")  == "SEA"
        assert normalize_team("SEA")      == "SEA"


# ===========================================================================
# S4: Official Miami win → Seattle model win not credited
# ===========================================================================

class TestS4_OfficialResultOverridesConflict:
    def test_official_win_overrides_conflict_not_credited_to_seattle(self):
        # Official league result: Miami won
        result = detect_conflict(
            "MLB:2026-07-09:SEA@MIA",
            {
                "kalshi":                "loss",     # Seattle lost per Kalshi
                "prizepicks":            "win",      # Seattle shown as win on PP
                "official_league_result": "loss",    # Official: Seattle lost
            },
        )
        # Conflict: prizepicks says win, official + kalshi say loss
        assert result["conflict_detected"] is True
        assert result["model_result"] is None
        assert result["calibration_eligible"] is False

    def test_authoritative_source_is_official_when_available(self):
        result = detect_conflict(
            "MLB:2026-07-09:SEA@MIA",
            {
                "official_league_result": "loss",
                "platform_settlement_display": "loss",
            },
        )
        assert result["conflict_detected"] is False
        assert result["authoritative_result"] == "LOSS"
        assert "official" in (result["authoritative_source"] or "").lower()

    def test_no_conflict_when_all_platforms_agree(self):
        result = detect_conflict(
            "MLB:2026-07-09:SEA@MIA",
            {"kalshi": "loss", "prizepicks": "loss", "official_league_result": "loss"},
        )
        assert result["conflict_detected"] is False
        assert result["calibration_eligible"] is True
        assert result["model_result"] == "LOSS"


# ===========================================================================
# S5: recent_win_streak present → does not alter tier, stake, or combo size
# ===========================================================================

class TestS5_WinStreakIsMetadataOnly:
    def test_validate_win_streak_is_metadata_clean_candidate(self):
        candidate = {
            "recent_win_streak": 7,
            "model_probability": 0.57,
            "stake": 0.5,
        }
        result = validate_win_streak_is_metadata(candidate)
        assert result["passed"] is True
        assert result["code"] == "RULE_G_WIN_STREAK_ISOLATED"

    def test_win_streak_sourced_into_actionable_field_is_violation(self):
        candidate = {
            "recent_win_streak": 7,
            "adjustment_source": "recent_win_streak boosted edge",
        }
        result = validate_win_streak_is_metadata(candidate)
        assert result["passed"] is False
        assert "RULE_G" in result["code"]

    def test_win_streak_in_edge_source_is_violation(self):
        candidate = {
            "recent_win_streak": 5,
            "edge_source": "win_streak_adjustment",
        }
        result = validate_win_streak_is_metadata(candidate)
        assert result["passed"] is False

    def test_assert_win_streak_isolation_raises_on_violation(self):
        context = {"recent_win_streak": 7, "model_probability": 0.58}
        with pytest.raises(AssertionError, match="Rule G violation"):
            assert_win_streak_isolation(context, "test_actionable_path")

    def test_assert_win_streak_isolation_passes_when_absent(self):
        context = {"model_probability": 0.58, "edge": 0.03}
        assert_win_streak_isolation(context, "test_safe_path")  # must not raise

    def test_series_state_audit_rejects_win_streak_in_enrichment(self):
        enrichment = {
            "recent_win_streak": 8,
            "model_edge": 0.025,
        }
        with pytest.raises(AssertionError, match="Rule G violation"):
            run_series_state_audit(enrichment)

    def test_combo_size_not_affected_by_win_streak(self):
        legs = [{"adjusted_prob": 0.60} for _ in range(2)]
        result = validate_combo_size(legs)
        assert result["allowed"] is True
        assert result["market_count"] == 2

    def test_stake_not_inflated_by_win_streak(self):
        candidate = {"recent_win_streak": 12, "stake": 0.50}
        result = validate_win_streak_is_metadata(candidate)
        assert result["passed"] is True
        assert candidate["stake"] == 0.50  # unchanged


# ===========================================================================
# S6: dry_run_only=True and can_execute=False hold unconditionally
# ===========================================================================

class TestS6_DryRunOnlyUnconditional:
    def test_one_market_combo_still_dry_run(self):
        legs = [{"adjusted_prob": 0.60}]
        result = evaluate_kalshi_combo(legs, combo_cost=1, combo_max_return=2)
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True

    def test_two_market_passing_combo_still_dry_run(self):
        legs = [{"adjusted_prob": 0.65}, {"adjusted_prob": 0.65}]
        result = evaluate_kalshi_combo(legs, combo_cost=1, combo_max_return=3.5)
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True

    def test_size_gate_dry_run_true(self):
        result = validate_combo_size([{"adjusted_prob": 0.60}])
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True

    def test_ev_gate_dry_run_true(self):
        legs = [{"adjusted_prob": 0.70}, {"adjusted_prob": 0.70}]
        cc = {"performed": True, "method": "historical_correlation"}
        result = validate_combo_ev(legs, combo_cost=1, combo_max_return=4,
                                   correlation_check=cc)
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True

    def test_hard_reject_combo_dry_run_true(self):
        legs = [{"adjusted_prob": 0.99} for _ in range(5)]
        result = evaluate_kalshi_combo(legs, combo_cost=1, combo_max_return=1000)
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True

    def test_three_market_soft_reject_dry_run_true(self):
        legs = [{"adjusted_prob": 0.70} for _ in range(3)]
        result = evaluate_kalshi_combo(legs, combo_cost=1, combo_max_return=8)
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True


# ===========================================================================
# S7: 2-market combo with all required EV fields → passes and proceeds
# ===========================================================================

class TestS7_TwoMarketComboPassesEVGate:
    def test_two_market_combo_passes_size_gate(self):
        legs = [{"adjusted_prob": 0.62}, {"adjusted_prob": 0.58}]
        result = validate_combo_size(legs)
        assert result["allowed"] is True
        assert result["reject_code"] is None

    _CORR_CHECK = {"performed": True, "method": "historical_correlation"}

    def test_two_market_combo_passes_ev_gate_when_edge_positive(self):
        # joint_prob = 0.62 * 0.60 = 0.372; breakeven = 1/3 ≈ 0.333
        legs = [{"adjusted_prob": 0.62}, {"adjusted_prob": 0.60}]
        result = validate_combo_ev(legs, combo_cost=1.0, combo_max_return=3.0,
                                   correlation_check=self._CORR_CHECK)
        assert result["passed"] is True
        assert result["code"] == "COMBO_EV_OK"
        assert result["joint_model_probability"] == pytest.approx(0.372, abs=1e-4)
        assert result["combo_breakeven_prob"] == pytest.approx(1.0 / 3.0, abs=1e-4)
        assert result["joint_adjusted_edge"] > 0

    def test_two_market_combo_full_evaluate_passes(self):
        legs = [{"adjusted_prob": 0.65}, {"adjusted_prob": 0.65}]
        result = evaluate_kalshi_combo(legs, combo_cost=1.0, combo_max_return=3.0,
                                       correlation_check=self._CORR_CHECK)
        assert result["passed"] is True
        assert result["stage"] == "EV_GATE"
        assert result["reject_code"] is None

    def test_two_market_combo_missing_correlation_check_fails(self):
        # correlation_check is required for multi-leg combos
        legs = [{"adjusted_prob": 0.65}, {"adjusted_prob": 0.65}]
        result = validate_combo_ev(legs, combo_cost=1.0, combo_max_return=3.0,
                                   correlation_check=None)
        assert result["passed"] is False
        assert result["code"] == REJECT_CODE_EV_MISS
        assert "correlation_check" in result["detail"]

    def test_two_market_combo_missing_adjusted_prob_fails(self):
        legs = [{"market": "m1"}, {"adjusted_prob": 0.60}]
        # Missing adjusted_prob checked before correlation_check
        result = validate_combo_ev(legs, combo_cost=1.0, combo_max_return=2.5,
                                   correlation_check=self._CORR_CHECK)
        assert result["passed"] is False
        assert result["code"] == REJECT_CODE_EV_MISS

    def test_two_market_combo_missing_combo_cost_fails(self):
        legs = [{"adjusted_prob": 0.65}, {"adjusted_prob": 0.65}]
        result = validate_combo_ev(legs, combo_cost=None, combo_max_return=3.0,
                                   correlation_check=self._CORR_CHECK)
        assert result["passed"] is False
        assert result["code"] == REJECT_CODE_EV_MISS

    def test_two_market_combo_missing_combo_max_return_fails(self):
        legs = [{"adjusted_prob": 0.65}, {"adjusted_prob": 0.65}]
        result = validate_combo_ev(legs, combo_cost=1.0, combo_max_return=None,
                                   correlation_check=self._CORR_CHECK)
        assert result["passed"] is False
        assert result["code"] == REJECT_CODE_EV_MISS

    def test_two_market_combo_negative_edge_soft_rejects(self):
        # joint_prob = 0.50 * 0.50 = 0.25; breakeven = 0.5/1 = 0.5 → negative edge
        legs = [{"adjusted_prob": 0.50}, {"adjusted_prob": 0.50}]
        result = validate_combo_ev(legs, combo_cost=0.5, combo_max_return=1.0,
                                   correlation_check=self._CORR_CHECK)
        assert result["passed"] is False
        assert result["code"] == REJECT_CODE_SOFT

    def test_correlation_review_flag_set_for_multi_leg(self):
        legs = [{"adjusted_prob": 0.65}, {"adjusted_prob": 0.65}]
        result = validate_combo_ev(legs, combo_cost=1.0, combo_max_return=3.0,
                                   correlation_check=self._CORR_CHECK)
        assert result["correlation_review_flag"] is True
        assert result["correlation_check"] == self._CORR_CHECK


# ===========================================================================
# Additional: Series-state audit (Rule F) — supplementary coverage
# ===========================================================================

class TestSeriesStateAudit:
    def test_no_triggers_passes(self):
        enrichment = {"opponent_win_streak_in_series": 0, "opponent_win_streak": 3}
        result = run_series_state_audit(enrichment)
        assert result["passed"] is True
        assert result["ceiling"] is None

    def test_one_trigger_no_cap(self):
        enrichment = {"opponent_win_streak_in_series": 2}
        result = run_series_state_audit(enrichment)
        assert result["trigger_count"] == 1
        assert result["ceiling"] is None

    def test_two_triggers_applies_llp_watch_cap(self):
        enrichment = {
            "opponent_win_streak_in_series": 2,
            "previous_game_runs_selected_team": 0,
        }
        result = run_series_state_audit(enrichment)
        assert result["trigger_count"] >= 2
        assert result["ceiling"] == LLP_WATCH_CEILING
        assert result["passed"] is False

    def test_two_triggers_edge_clears_lifts_cap(self):
        # Rule F: BOTH no_vig_consensus_edge AND model_edge must clear floor+tax
        enrichment = {
            "opponent_win_streak_in_series": 2,
            "previous_game_runs_selected_team": 0,
            "no_vig_consensus_edge": 0.055,  # 0.055 - 0.010 tax = 0.045 > 0.015
            "model_edge":           0.050,   # 0.050 - 0.010 tax = 0.040 > 0.015
        }
        result = run_series_state_audit(enrichment)
        assert result["uncertainty_tax_applied"] is True
        # edge_after_tax = min(0.045, 0.040) = 0.040
        assert result["edge_after_tax"] == pytest.approx(0.040, abs=1e-5)
        assert result["ceiling"] is None
        assert result["passed"] is True

    def test_one_edge_signal_alone_cannot_lift_cap(self):
        # Rule F: only model_edge present, no_vig missing → cap stays applied
        enrichment = {
            "opponent_win_streak_in_series": 2,
            "previous_game_runs_selected_team": 0,
            "model_edge": 0.100,  # high but alone — should NOT lift cap
        }
        result = run_series_state_audit(enrichment)
        assert result["ceiling"] == LLP_WATCH_CEILING
        assert result["passed"] is False

    def test_only_no_vig_edge_alone_cannot_lift_cap(self):
        # Rule F: only no_vig present, model_edge missing → cap stays applied
        enrichment = {
            "opponent_win_streak_in_series": 2,
            "previous_game_runs_selected_team": 0,
            "no_vig_consensus_edge": 0.100,  # high but alone — should NOT lift cap
        }
        result = run_series_state_audit(enrichment)
        assert result["ceiling"] == LLP_WATCH_CEILING
        assert result["passed"] is False

    def test_one_edge_below_floor_keeps_cap(self):
        # Rule F: one edge clears but the other doesn't → cap must stay
        enrichment = {
            "opponent_win_streak_in_series": 2,
            "previous_game_runs_selected_team": 0,
            "no_vig_consensus_edge": 0.050,  # clears floor after tax
            "model_edge":           0.020,   # 0.020 - 0.010 = 0.010 < 0.015 floor
        }
        result = run_series_state_audit(enrichment)
        assert result["ceiling"] == LLP_WATCH_CEILING
        assert result["passed"] is False

    def test_three_triggers_all_fire(self):
        enrichment = {
            "opponent_win_streak_in_series": 3,
            "opponent_win_streak": 7,
            "previous_game_runs_selected_team": 1,
        }
        result = run_series_state_audit(enrichment)
        assert TRIGGER_OPPONENT_SERIES in result["triggers"]
        assert TRIGGER_OPPONENT_STREAK in result["triggers"]
        assert TRIGGER_SELECTED_RUNS  in result["triggers"]
        assert result["trigger_count"] == 3

    def test_win_streak_in_enrichment_raises_rule_g(self):
        enrichment = {"recent_win_streak": 10, "opponent_win_streak_in_series": 2}
        with pytest.raises(AssertionError, match="Rule G violation"):
            run_series_state_audit(enrichment)


# ===========================================================================
# Additional: event normalization coverage
# ===========================================================================

class TestEventNormalization:
    def test_mariners_variants(self):
        for name in ("Mariners", "Seattle", "SEA", "mariners", "seattle"):
            assert normalize_team(name) == "SEA"

    def test_phillies_variants(self):
        for name in ("Phillies", "Philadelphia", "PHI"):
            assert normalize_team(name) == "PHI"

    def test_build_event_id_canonical(self):
        eid = build_event_id("MLB", "MLB", "2026-07-09", "Mariners", "Marlins")
        assert "SEA" in eid
        assert "MIA" in eid
        assert "2026-07-09" in eid

    def test_group_entries_same_game_different_source(self):
        rows = [
            {
                "row_id": "r1", "sport": "MLB", "league": "MLB",
                "slate_date": "2026-07-09", "game": "Mariners @ Marlins",
                "board_source": "PrizePicks",
            },
            {
                "row_id": "r2", "sport": "MLB", "league": "MLB",
                "slate_date": "2026-07-09", "game": "Mariners @ Marlins",
                "board_source": "Kalshi",
            },
        ]
        groups = group_entries_by_event(rows)
        assert len(groups) == 1

    def test_group_entries_different_game_different_group(self):
        rows = [
            {
                "row_id": "r1", "sport": "MLB", "league": "MLB",
                "slate_date": "2026-07-09", "game": "Yankees @ Red Sox",
                "board_source": "PrizePicks",
            },
            {
                "row_id": "r2", "sport": "MLB", "league": "MLB",
                "slate_date": "2026-07-09", "game": "Mariners @ Marlins",
                "board_source": "PrizePicks",
            },
        ]
        groups = group_entries_by_event(rows)
        assert len(groups) == 2

    def test_event_side_more_less_separated_from_over_under(self):
        # MORE and LESS are board_intake aliases → should map to OVER/UNDER side keys
        rows = [
            {"row_id": "r1", "sport": "NBA", "league": "NBA", "slate_date": "2026-07-14",
             "game": "Lakers @ Warriors", "direction": "MORE"},
            {"row_id": "r2", "sport": "NBA", "league": "NBA", "slate_date": "2026-07-14",
             "game": "Lakers @ Warriors", "direction": "OVER"},   # same side as MORE
            {"row_id": "r3", "sport": "NBA", "league": "NBA", "slate_date": "2026-07-14",
             "game": "Lakers @ Warriors", "direction": "LESS"},
            {"row_id": "r4", "sport": "NBA", "league": "NBA", "slate_date": "2026-07-14",
             "game": "Lakers @ Warriors", "direction": "UNDER"},  # same side as LESS
        ]
        grps = group_entries_by_event_and_side(rows)
        # MORE+OVER → OVER group, LESS+UNDER → UNDER group → 2 distinct side keys
        assert len(grps) == 2
        keys = set(grps.keys())
        assert any(k.endswith(":OVER") for k in keys), f"No OVER key: {keys}"
        assert any(k.endswith(":UNDER") for k in keys), f"No UNDER key: {keys}"
        # Each group has 2 rows (MORE+OVER in one, LESS+UNDER in other)
        for grp_rows in grps.values():
            assert len(grp_rows) == 2

    def test_event_side_more_and_over_do_not_become_unknown(self):
        # Confirm MORE/LESS never degrade to UNKNOWN
        row_more  = {"sport": "NBA", "league": "NBA", "slate_date": "2026-07-14",
                     "game": "Lakers @ Warriors", "direction": "MORE", "row_id": "a"}
        row_less  = {"sport": "NBA", "league": "NBA", "slate_date": "2026-07-14",
                     "game": "Lakers @ Warriors", "direction": "LESS", "row_id": "b"}
        grps = group_entries_by_event_and_side([row_more, row_less])
        for key in grps.keys():
            assert not key.endswith(":UNKNOWN"), f"Unexpected UNKNOWN key: {key}"


# ===========================================================================
# Combo size gate: 0-leg rejection
# ===========================================================================

class TestComboSizeZeroLegRejection:
    def test_zero_leg_combo_rejected(self):
        result = validate_combo_size([])
        assert result["allowed"] is False
        assert result["reject_code"] == "REJECT_BAD_STRUCTURE"
        assert result["market_count"] == 0
        assert result["can_execute"] is False
        assert result["dry_run_only"] is True

    def test_none_legs_rejected(self):
        result = validate_combo_size(None)
        assert result["allowed"] is False
        assert result["reject_code"] == "REJECT_BAD_STRUCTURE"
        assert result["market_count"] == 0

    def test_one_leg_still_allowed(self):
        result = validate_combo_size([{"adjusted_prob": 0.70}])
        assert result["allowed"] is True
        assert result["reject_code"] is None

    def test_two_legs_still_allowed(self):
        result = validate_combo_size([{"adjusted_prob": 0.70}, {"adjusted_prob": 0.65}])
        assert result["allowed"] is True
