"""Cross-sport ranked research retro: synthetic receipts, no fitted credit."""
from copy import deepcopy

import pytest

from v17.llp_ranked_winner_retro import audit_ranked_winner_retro

START = "2026-11-10T23:00:00Z"
FREEZE = "2026-11-10T15:00:00Z"
SETTLED = "2026-11-11T03:00:00Z"


def pick(sport="NCAAB", event="a", side="HOME", **overrides):
    v = {"sport": sport, "official_event_id": event, "home_team": "Home",
         "away_team": "Away", "event_start_time_utc": START,
         "frozen_at_utc": FREEZE, "available_to_user_at_utc": FREEZE,
         "selected_side": side, "immutable_pregame": True,
         "settlement_basis": ("REGULATION" if sport == "UWCL" else
                               "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT" if sport == "NHL" else
                               "FULL_GAME_INCLUDING_OVERTIME"),
         "pregame_receipt_verified": True, "pregame_receipt_id": "pre-" + event,
         "market": {"sport": sport, "official_event_id": event,
                    "home_team": "Home", "away_team": "Away",
                    "event_start_time_utc": START,
                    "available_to_user_at_utc": FREEZE,
                    "market_type": "REGULATION_3WAY" if sport == "UWCL" else "FULL_GAME_2WAY",
                    "as_of_utc": FREEZE, "independently_verified": True,
                    "receipt_id": "market-" + event,
                    "decimal_odds": ({"HOME": 1.4, "AWAY": 8.0, "DRAW": 4.0}
                                     if sport == "UWCL" else {"HOME": 1.4, "AWAY": 3.4})}}
    v.update(overrides)
    return v


def final(sport="NCAAB", event="a", outcome="HOME", **overrides):
    hs, aws = (90, 80) if outcome == "HOME" else (80, 90) if outcome == "AWAY" else (0, 0)
    v = {"sport": sport, "official_event_id": event, "home_team": "Home",
         "away_team": "Away", "event_start_time_utc": START,
         "final_status": "FINAL", "independently_verified": True,
         "settlement_receipt_id": "official-" + event,
         "settlement_source": "OFFICIAL_LEAGUE", "settled_at_utc": SETTLED,
         "outcome": outcome, "home_score": hs, "away_score": aws,
         "settlement_basis": ("REGULATION" if sport == "UWCL" else
                              "FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT" if sport == "NHL" else
                              "FULL_GAME_INCLUDING_OVERTIME") }
    v.update(overrides)
    return v


def audit(picks, finals, **kw):
    return audit_ranked_winner_retro(
        picks, finals, ranked_cohort=[{k: p[k] for k in
                    ("sport", "official_event_id", "home_team", "away_team", "event_start_time_utc",
                     "selected_side", "pregame_receipt_id", "frozen_at_utc")}
                    for p in picks],
        cohort_verified=True, cohort_receipt_id="independent-card-snapshot",
        cohort_kind="RANKED_SUBSET", **kw)


def test_market_favorites_winning_does_not_establish_research_edge():
    rows = [pick("NCAAB", "a"), pick("NCAAF", "b")]
    finals = [final("NCAAB", "a"), final("NCAAF", "b", "AWAY")]
    frozen = deepcopy((rows, finals))
    report = audit(rows, finals)
    assert report["cohort_complete"] and report["market_baseline_complete"]
    assert report["correct"] == report["market_favorite_correct"] == 1
    assert report["incremental_correct_vs_market"] == 0
    assert report["market_edge_established"] is False
    assert report["research_scope"] == "RANKED_SUBSET"
    assert report["fitted_model_samples_added"] == 0
    assert report["can_execute"] is False
    assert (rows, finals) == frozen


def test_soccer_draw_is_a_loss_for_team_pick_and_market_favorite():
    report = audit([pick("UWCL", "s")], [final("UWCL", "s", "DRAW")])
    assert report["subset_accuracy"] == 0
    assert report["rows"][0]["selection_grade"] == "LOSS"
    assert report["rows"][0]["market_favorite_grade"] == "LOSS"
    assert report["rows"][0]["official_outcome"] == "DRAW"


def test_two_way_soccer_quote_cannot_erase_draw_outcome():
    p = pick("UWCL", "s")
    p["market"]["market_type"] = "FULL_GAME_2WAY"
    p["market"]["decimal_odds"] = {"HOME": 1.4, "AWAY": 3.4}
    report = audit([p], [final("UWCL", "s", "DRAW")])
    assert report["cohort_complete"]
    assert not report["market_baseline_complete"]
    assert report["market_favorite_correct"] is None
    assert "MARKET_SETTLEMENT_BASIS_INVALID" in report["rows"][0]["market_blockers"]


def test_missing_market_does_not_fabricate_baseline():
    p = pick(market=None)
    r = audit([p], [final()])
    assert r["correct"] == 1
    assert r["market_baseline_complete"] is False
    assert r["incremental_correct_vs_market"] is None


def test_late_pick_and_unverified_result_fail_closed():
    late = audit([pick(frozen_at_utc="2026-11-11T00:00:00Z")], [final()])
    assert late["subset_accuracy"] is None
    assert "PREGAME_FREEZE_INVALID" in late["rows"][0]["selection_blockers"]
    bad = audit([pick()], [final(independently_verified=False)])
    assert bad["correct"] is None
    assert bad["rows"][0]["selection_grade"] == "HELD"


def test_boolean_attestation_is_literal_and_cohort_cannot_be_called_full_slate():
    p = pick()
    rep = audit_ranked_winner_retro(
        [p], [final()], ranked_cohort=[p],
        cohort_verified="true", cohort_receipt_id="fake",
        cohort_kind="RANKED_SUBSET")
    assert rep["cohort_complete"] is False
    assert rep["subset_accuracy"] is None


def test_spread_cover_independent_of_outright_winner():
    p = pick(spread={"home_line": -38.5, "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
                     "sport": "NCAAB", "official_event_id": "a",
                     "home_team": "Home", "away_team": "Away",
                     "event_start_time_utc": START,
                     "available_to_user_at_utc": FREEZE,
                     "as_of_utc": FREEZE, "receipt_id": "spread-a",
                     "independently_verified": True})
    r = audit([p], [final(home_score=95, away_score=61)])
    assert r["rows"][0]["selection_grade"] == "WIN"
    assert r["rows"][0]["spread_outcome_diagnostic_only"] == "AWAY_COVER"
    assert r["rows"][0]["spread_was_selected_as_pick"] is False


def test_late_market_and_reused_receipts_do_not_claim_edge():
    a, b = pick(event="a"), pick(event="b")
    b["market"]["receipt_id"] = a["market"]["receipt_id"]
    a["market"]["as_of_utc"] = SETTLED
    r = audit([a, b], [final(event="a"), final(event="b")])
    assert r["cohort_complete"] is True
    assert r["market_baseline_complete"] is False
    assert all(x["market_favorite_grade"] == "UNAVAILABLE" for x in r["rows"])


def test_market_event_swap_is_not_a_valid_baseline():
    p = pick()
    p["market"]["official_event_id"] = "different-game"
    r = audit([p], [final()])
    assert r["correct"] == 1
    assert r["market_baseline_complete"] is False
    assert r["rows"][0]["market_favorite"] is None
    assert "MARKET_IDENTITY_OR_RECEIPT_REUSED" in r["rows"][0]["market_blockers"]


def test_market_and_spread_available_after_freeze_are_not_pregame():
    p = pick(spread={"home_line": -1.5, "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
                     "sport": "NCAAB", "official_event_id": "a",
                     "home_team": "Home", "away_team": "Away",
                     "event_start_time_utc": START, "as_of_utc": FREEZE,
                     "available_to_user_at_utc": SETTLED,
                     "receipt_id": "spread-a", "independently_verified": True})
    p["market"]["available_to_user_at_utc"] = SETTLED
    r = audit([p], [final()])
    assert r["correct"] == 1
    assert r["market_baseline_complete"] is False
    assert "MARKET_NOT_VISIBLE_AT_FREEZE" in r["rows"][0]["market_blockers"]
    assert r["rows"][0]["spread_outcome_diagnostic_only"] == "NOT_EVALUATED"

def test_market_snapshot_age_needs_same_time_within_sixty_minutes():
    p = pick()
    p["market"]["as_of_utc"] = "2026-11-10T13:59:59Z"
    too_old = audit([p], [final()])
    assert too_old["correct"] == 1
    assert too_old["market_baseline_complete"] is False
    assert too_old["rows"][0]["market_favorite"] is None
    assert "MARKET_SNAPSHOT_TOO_OLD_FOR_SAME_TIME_BASELINE" in too_old["rows"][0]["market_blockers"]

    p["market"]["as_of_utc"] = "2026-11-10T14:00:00Z"
    at_boundary = audit([p], [final()])
    assert at_boundary["market_baseline_complete"] is True
    assert at_boundary["market_favorite_correct"] == 1


def test_oversized_prices_are_market_unavailable_not_runtime_failure():
    for bad in (10**1000, -(10**1000), float("inf"), float("nan")):
        p = pick()
        p["market"]["decimal_odds"]["HOME"] = bad
        r = audit([p], [final()])
        assert r["correct"] == 1
        assert r["market_baseline_complete"] is False
        assert "MARKET_PRICE_INVALID" in r["rows"][0]["market_blockers"]


def test_oversized_spreads_do_not_crash_or_change_winner_grade():
    for bad in (10**1000, -(10**1000), float("inf"), float("nan")):
        p = pick(spread={
            "home_line": bad, "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
            "sport": "NCAAB", "official_event_id": "a",
            "home_team": "Home", "away_team": "Away",
            "event_start_time_utc": START, "available_to_user_at_utc": FREEZE,
            "as_of_utc": FREEZE, "receipt_id": "spread-a",
            "independently_verified": True,
        })
        r = audit([p], [final()])
        assert r["correct"] == 1
        assert r["rows"][0]["spread_outcome_diagnostic_only"] == "NOT_EVALUATED"
        assert r["rows"][0]["selection_grade"] == "WIN"


def test_full_slate_scope_is_explicitly_unsupported_even_if_attested_true():
    p = pick()
    with pytest.raises(ValueError, match="FULL_SLATE_SCOPE_UNSUPPORTED"):
        audit_ranked_winner_retro(
            [p], [final()], ranked_cohort=[p], cohort_verified=True,
            cohort_receipt_id="cohort-1", cohort_kind="VERIFIED_FULL_SLATE")


def test_verified_cohort_side_mismatch_is_held():
    p = pick(side="HOME")
    cohort = [{k: p[k] for k in
               ("sport", "official_event_id", "home_team", "away_team", "event_start_time_utc",
                "pregame_receipt_id", "frozen_at_utc")}]
    cohort[0]["selected_side"] = "AWAY"
    r = audit_ranked_winner_retro(
        [p], [final()], ranked_cohort=cohort,
        cohort_verified=True, cohort_receipt_id="cohort-2")
    assert r["cohort_complete"] is False
    assert r["correct"] is None
    assert "RANKED_COHORT_NOT_INDEPENDENTLY_RECONCILED" in r["rows"][0]["selection_blockers"]


def test_huge_official_score_holds_before_spread_arithmetic():
    p = pick(spread={
        "home_line": -1.5, "settlement_basis": "FULL_GAME_INCLUDING_OVERTIME",
        "sport": "NCAAB", "official_event_id": "a",
        "home_team": "Home", "away_team": "Away",
        "event_start_time_utc": START, "available_to_user_at_utc": FREEZE,
        "as_of_utc": FREEZE, "receipt_id": "spread-a", "independently_verified": True})
    r = audit([p], [final(home_score=10**1000, away_score=80)])
    assert r["correct"] is None
    assert r["rows"][0]["selection_grade"] == "HELD"
    assert "OFFICIAL_SCORE_INVALID" in r["rows"][0]["selection_blockers"]
    assert r["rows"][0]["spread_outcome_diagnostic_only"] == "NOT_EVALUATED"


def test_incomplete_cohort_withholds_all_sport_totals():
    ps = [pick("NCAAB", "a"), pick("NCAAF", "b")]
    finals = [final("NCAAB", "a"), final("NCAAF", "b", independently_verified=False)]
    r = audit(ps, finals)
    assert r["cohort_complete"] is False
    assert r["correct"] is None
    assert len(r["by_sport"]) == 2
    assert all(k["graded"] is None and k["correct"] is None
               and k["incomplete_cohort"] is True for k in r["by_sport"])


def test_replacement_receipt_cannot_be_graded_as_original_frozen_card():
    original = pick()
    cohort = [{k: original[k] for k in (
        "sport", "official_event_id", "home_team", "away_team",
        "event_start_time_utc", "selected_side",
        "pregame_receipt_id", "frozen_at_utc")}]
    replacement = deepcopy(original)
    replacement["pregame_receipt_id"] = "substituted-later-receipt"
    replacement["frozen_at_utc"] = "2026-11-10T15:30:00Z"
    replacement["available_to_user_at_utc"] = "2026-11-10T15:30:00Z"
    replacement["market"]["as_of_utc"] = "2026-11-10T15:30:00Z"
    replacement["market"]["available_to_user_at_utc"] = "2026-11-10T15:30:00Z"
    r = audit_ranked_winner_retro(
        [replacement], [final()], ranked_cohort=cohort,
        cohort_verified=True, cohort_receipt_id="immutable-original")
    assert r["cohort_complete"] is False
    assert r["correct"] is None
    assert r["market_baseline_complete"] is False
    assert "RANKED_COHORT_NOT_INDEPENDENTLY_RECONCILED" in r["rows"][0]["selection_blockers"]


@pytest.mark.parametrize("field,new_value", [
    ("pregame_receipt_id", ""), ("frozen_at_utc", None),
])
def test_cohort_without_frozen_receipt_or_timestamp_cannot_attest_pick(field, new_value):
    p = pick()
    frozen = {k: p[k] for k in (
        "sport", "official_event_id", "home_team", "away_team",
        "event_start_time_utc", "selected_side", "pregame_receipt_id", "frozen_at_utc")}
    frozen[field] = new_value
    r = audit_ranked_winner_retro(
        [p], [final()], ranked_cohort=[frozen],
        cohort_verified=True, cohort_receipt_id="synthetic-verified")
    assert r["cohort_complete"] is False
    assert r["market_baseline_complete"] is False
    assert r["rows"][0]["selection_grade"] == "HELD"


def test_nhl_regulation_pick_cannot_be_graded_as_full_game_winner():
    p = pick("NHL", "nhl-01", settlement_basis="REGULATION")
    r = audit([p], [final("NHL", "nhl-01")])
    assert r["subset_accuracy"] is None
    assert r["market_favorite_correct"] is None
    assert "PICK_SETTLEMENT_BASIS_INVALID" in r["rows"][0]["selection_blockers"]


def test_nhl_ambiguous_full_game_label_is_not_overtime_shootout_receipt():
    p = pick("NHL", "nhl-01", settlement_basis="FULL_GAME")
    r = audit([p], [final("NHL", "nhl-01", settlement_basis="FULL_GAME")])
    assert r["cohort_complete"] is False
    assert "PICK_SETTLEMENT_BASIS_INVALID" in r["rows"][0]["selection_blockers"]
    assert "OFFICIAL_OUTCOME_OR_BASIS_INVALID" in r["rows"][0]["selection_blockers"]


def test_three_way_soccer_regulation_pick_with_draw_stays_a_loss():
    r = audit([pick("UWCL", "soccer-1")], [final("UWCL", "soccer-1", "DRAW")])
    assert r["cohort_complete"] is True
    assert r["rows"][0]["selection_grade"] == "LOSS"
    assert r["market_favorite_correct"] == 0
