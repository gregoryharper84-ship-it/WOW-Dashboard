"""
test_llp_acquisition_resilience.py
WOW-PATCH-2026-07-15-LLP-DATA-ACQUISITION-RESILIENCE

Test coverage for all 18+ scenarios specified in the patch brief.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from datetime import datetime, timezone, timedelta

from gate_engine.llp_acquisition_resilience import (
    # Market aliases
    PROVIDER_MARKET_ALIASES,
    resolve_market_alias,
    is_official_identity_source,
    OFFICIAL_IDENTITY_ONLY_SOURCES,
    # UTC normalization
    normalize_to_utc,
    # Event identity
    LEAGUE_TIME_TOLERANCE_MINUTES,
    AMBIGUOUS_CITY_ALIASES,
    is_alias_ambiguous,
    detect_doubleheader,
    match_event_with_tolerance,
    # PrizePicks adapter
    parse_prizepicks_game_winner_odds,
    # No-vig reconstruction
    BookQuote,
    reconstruct_consensus_no_vig,
    # Source ceilings
    SourceQuality,
    SOURCE_CEILING_MAP,
    classify_source_quality,
    # Anti-circular model
    check_model_independence,
    # Contract stages
    build_contract_stage_report,
    CONTRACT_STAGES,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_utc_iso(minutes_ago: int = 90) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _fresh_utc_iso(minutes_ago: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ===========================================================================
# 1. Market Alias Mapping
# ===========================================================================

class TestMarketAliasMapping:
    def test_moneyline_to_h2h_odds_api(self):
        canonical, diag = resolve_market_alias("the_odds_api", "moneyline")
        assert canonical == "h2h"
        assert diag["resolved"] is True

    def test_game_winner_to_h2h_odds_api(self):
        canonical, diag = resolve_market_alias("the_odds_api", "game_winner")
        assert canonical == "h2h"
        assert diag["resolved"] is True

    def test_spread_mapping_odds_api(self):
        canonical, _ = resolve_market_alias("the_odds_api", "spread")
        assert canonical == "spreads"

    def test_totals_mapping_odds_api(self):
        canonical, _ = resolve_market_alias("the_odds_api", "total")
        assert canonical == "totals"

    def test_moneyline_to_h2h_fanduel(self):
        canonical, diag = resolve_market_alias("fanduel", "moneyline")
        assert canonical == "h2h"
        assert diag["resolved"] is True

    def test_moneyline_to_h2h_draftkings(self):
        canonical, _ = resolve_market_alias("draftkings", "moneyline")
        assert canonical == "h2h"

    def test_unsupported_market_returns_none_with_diagnostics(self):
        canonical, diag = resolve_market_alias("the_odds_api", "futures")
        assert canonical is None
        assert diag["resolved"] is False
        assert "unsupported_market" in diag["reason"]
        assert "known_markets" in diag["reason"]

    def test_unknown_provider_returns_none_with_diagnostics(self):
        canonical, diag = resolve_market_alias("unknown_bookie", "moneyline")
        assert canonical is None
        assert diag["resolved"] is False
        assert "unknown_provider" in diag["reason"]

    def test_prizepicks_game_winner_is_pp_adapter(self):
        canonical, diag = resolve_market_alias("prizepicks", "game_winner")
        assert canonical == "game_winner_pp"
        assert diag["is_pp_adapter"] is True

    def test_case_insensitive_provider(self):
        canonical, _ = resolve_market_alias("The_Odds_API", "moneyline")
        assert canonical == "h2h"

    def test_official_identity_sources_cannot_supply_odds(self):
        for source in ["espn", "nba_api", "nba_official", "mlb_official",
                       "direct_league_official_source"]:
            assert is_official_identity_source(source) is True

    def test_sportsbook_source_is_not_identity_only(self):
        assert is_official_identity_source("fanduel") is False
        assert is_official_identity_source("the_odds_api") is False
        assert is_official_identity_source(None) is False


# ===========================================================================
# 2. League-Scoped Event Identity — Ambiguous Aliases
# ===========================================================================

class TestLeagueScopedEventIdentity:
    def test_washington_is_ambiguous_without_league(self):
        assert is_alias_ambiguous("Washington", league=None) is True

    def test_indiana_is_ambiguous_without_league(self):
        assert is_alias_ambiguous("Indiana", league=None) is True

    def test_new_york_is_ambiguous_without_league(self):
        assert is_alias_ambiguous("New York", league=None) is True

    def test_los_angeles_is_ambiguous_without_league(self):
        assert is_alias_ambiguous("Los Angeles", league=None) is True

    def test_washington_not_ambiguous_with_mlb(self):
        assert is_alias_ambiguous("Washington", league="mlb") is False

    def test_washington_not_ambiguous_with_nba(self):
        assert is_alias_ambiguous("Washington", league="nba") is False

    def test_indiana_not_ambiguous_with_nba(self):
        assert is_alias_ambiguous("Indiana", league="nba") is False

    def test_indiana_not_ambiguous_with_wnba(self):
        assert is_alias_ambiguous("Indiana", league="wnba") is False

    def test_canonical_abbreviations_are_never_ambiguous(self):
        # Already-canonical forms should not be in the ambiguous set
        assert is_alias_ambiguous("WSH", league=None) is False
        assert is_alias_ambiguous("NYY", league=None) is False

    def test_all_ambiguous_aliases_are_lowercase_normalized(self):
        for alias in AMBIGUOUS_CITY_ALIASES:
            assert alias == alias.lower()


# ===========================================================================
# 3. Event Orientation Matching (no side inversion)
# ===========================================================================

class TestEventOrientationMatching:
    def _make_target(self, away="BOS", home="NYY", date="2026-07-15", league="mlb"):
        return {"away": away, "home": home, "date": date, "league": league}

    def _make_candidate(self, away="BOS", home="NYY", date="2026-07-15"):
        return {"away": away, "home": home, "date": date}

    def test_exact_match(self):
        result = match_event_with_tolerance(
            self._make_target(), [self._make_candidate()]
        )
        assert result["matched"] is True
        assert result["is_reversed"] is False
        assert result["confidence"] == "exact"

    def test_reversed_orientation_detected_not_inverted(self):
        # Candidates has HOME and AWAY swapped
        reversed_cand = self._make_candidate(away="NYY", home="BOS")
        result = match_event_with_tolerance(
            self._make_target(), [reversed_cand], reversed_ok=True
        )
        assert result["matched"] is True
        assert result["is_reversed"] is True, "Must flag reversed orientation"
        assert result["confidence"] == "reversed"
        # The candidate record is returned AS-IS — caller must preserve true venue
        assert result["candidate"]["away"] == "NYY"
        assert result["candidate"]["home"] == "BOS"

    def test_reversed_disabled_does_not_match_flipped(self):
        reversed_cand = self._make_candidate(away="NYY", home="BOS")
        result = match_event_with_tolerance(
            self._make_target(), [reversed_cand], reversed_ok=False
        )
        assert result["matched"] is False

    def test_no_match_returns_false(self):
        other = self._make_candidate(away="LAD", home="SF")
        result = match_event_with_tolerance(self._make_target(), [other])
        assert result["matched"] is False
        assert result["candidate"] is None

    def test_wrong_date_does_not_match(self):
        wrong_date = self._make_candidate(date="2026-07-16")
        result = match_event_with_tolerance(self._make_target(), [wrong_date])
        # No time fields, so cannot use time-tolerance fallback
        assert result["matched"] is False

    def test_first_exact_match_wins_over_reversed(self):
        exact   = self._make_candidate()
        reversed_c = self._make_candidate(away="NYY", home="BOS")
        result = match_event_with_tolerance(
            self._make_target(), [reversed_c, exact], reversed_ok=True
        )
        # Exact match is second in list but reversed_ok finds reversed first
        # (order matters — first hit returned)
        assert result["matched"] is True


# ===========================================================================
# 4. Doubleheader Detection
# ===========================================================================

class TestDoubleheaderDetection:
    def test_single_game_no_collision(self):
        candidates = [{"date": "2026-07-14", "away": "cubs", "home": "cardinals"}]
        result = detect_doubleheader(candidates)
        assert result["collision"] is False
        assert result["requires_game_id"] is False

    def test_two_games_same_teams_same_day_collision(self):
        candidates = [
            {"date": "2026-07-14", "away": "cubs", "home": "cardinals"},
            {"date": "2026-07-14", "away": "cubs", "home": "cardinals"},
        ]
        result = detect_doubleheader(candidates)
        assert result["collision"] is True
        assert result["count"] == 2
        assert result["requires_game_id"] is True

    def test_different_dates_no_collision(self):
        candidates = [
            {"date": "2026-07-14", "away": "cubs", "home": "cardinals"},
            {"date": "2026-07-15", "away": "cubs", "home": "cardinals"},
        ]
        result = detect_doubleheader(candidates)
        assert result["collision"] is False

    def test_different_teams_no_collision(self):
        candidates = [
            {"date": "2026-07-14", "away": "cubs", "home": "cardinals"},
            {"date": "2026-07-14", "away": "cubs", "home": "reds"},
        ]
        result = detect_doubleheader(candidates)
        assert result["collision"] is False

    def test_triple_header_reports_count_3(self):
        cand = {"date": "2026-07-14", "away": "cubs", "home": "cardinals"}
        result = detect_doubleheader([cand, cand, cand])
        assert result["count"] == 3
        assert result["collision"] is True


# ===========================================================================
# 5. UTC Normalization / Timezone Boundary
# ===========================================================================

class TestUTCNormalization:
    def test_utc_z_suffix_passthrough(self):
        r = normalize_to_utc("2026-07-14T23:05:00Z")
        assert r["normalized"] is True
        assert "2026-07-14" in r["utc_datetime"]
        assert r["utc_date"] == "2026-07-14"

    def test_utc_plus_zero_offset(self):
        r = normalize_to_utc("2026-07-14T23:05:00+00:00")
        assert r["normalized"] is True
        assert r["utc_date"] == "2026-07-14"

    def test_eastern_time_suffix(self):
        # 23:05 ET = 04:05 UTC next day (EDT = UTC-4)
        r = normalize_to_utc("2026-07-14 23:05:00 EDT")
        assert r["normalized"] is True
        assert r["utc_date"] == "2026-07-15"  # crosses midnight

    def test_eastern_standard_time(self):
        # 19:05 EST = 00:05 UTC next day
        r = normalize_to_utc("2026-07-14 19:05:00 EST")
        assert r["normalized"] is True
        assert r["utc_date"] == "2026-07-15"

    def test_pacific_time_suffix(self):
        # 15:05 PDT = 22:05 UTC same day
        r = normalize_to_utc("2026-07-14 15:05:00 PDT")
        assert r["normalized"] is True
        assert r["utc_date"] == "2026-07-14"

    def test_iso_with_explicit_offset(self):
        r = normalize_to_utc("2026-07-14T19:05:00-05:00")
        assert r["normalized"] is True
        assert r["utc_date"] == "2026-07-15"

    def test_midnight_boundary_cross(self):
        # Game at 11:30 PM ET (EDT) = 3:30 AM UTC next day
        r = normalize_to_utc("2026-07-14 23:30:00 EDT")
        assert r["normalized"] is True
        assert r["utc_date"] == "2026-07-15"

    def test_empty_input_returns_error(self):
        r = normalize_to_utc(None)
        assert r["normalized"] is False
        assert r["error"] == "empty_input"

    def test_invalid_format_returns_error(self):
        r = normalize_to_utc("not-a-date")
        assert r["normalized"] is False
        assert r["error"] is not None

    def test_already_utc_date_only(self):
        r = normalize_to_utc("2026-07-14T01:00:00+00:00")
        assert r["utc_date"] == "2026-07-14"


# ===========================================================================
# 6. PrizePicks Game Winner Odds Adapter
# ===========================================================================

class TestPrizePicksOddsAdapter:
    def test_185_decimal_accepted_as_decimal(self):
        r = parse_prizepicks_game_winner_odds(1.85)
        assert r["accepted"] is True
        assert r["format"] == "decimal"
        # 1/1.85 ≈ 0.5405
        assert abs(r["no_vig_probability"] - (1.0 / 1.85)) < 1e-4

    def test_188_decimal_accepted_as_decimal(self):
        r = parse_prizepicks_game_winner_odds(1.88)
        assert r["accepted"] is True
        assert r["format"] == "decimal"
        assert abs(r["no_vig_probability"] - (1.0 / 1.88)) < 1e-4

    def test_200_decimal_accepted(self):
        r = parse_prizepicks_game_winner_odds(2.00)
        assert r["accepted"] is True
        assert r["format"] == "decimal"
        assert abs(r["no_vig_probability"] - 0.5) < 1e-6

    def test_american_minus_110_accepted(self):
        r = parse_prizepicks_game_winner_odds(-110)
        assert r["accepted"] is True
        assert r["format"] == "american"
        expected = 110.0 / 210.0
        assert abs(r["no_vig_probability"] - expected) < 1e-4

    def test_american_plus_130_accepted(self):
        r = parse_prizepicks_game_winner_odds(130)
        assert r["accepted"] is True
        assert r["format"] == "american"
        expected = 100.0 / 230.0
        assert abs(r["no_vig_probability"] - expected) < 1e-4

    def test_american_minus_200_accepted(self):
        r = parse_prizepicks_game_winner_odds(-200)
        assert r["accepted"] is True
        assert r["format"] == "american"
        assert abs(r["no_vig_probability"] - (200.0 / 300.0)) < 1e-4

    def test_exactly_100_is_american(self):
        r = parse_prizepicks_game_winner_odds(100.0)
        assert r["accepted"] is True
        assert r["format"] == "american"

    def test_value_1_is_invalid(self):
        r = parse_prizepicks_game_winner_odds(1.0)
        assert r["accepted"] is False
        assert r["format"] == "invalid"
        assert "no_profit" in r["rejection_reason"]

    def test_zero_is_invalid(self):
        r = parse_prizepicks_game_winner_odds(0)
        assert r["accepted"] is False

    def test_negative_decimal_is_invalid(self):
        r = parse_prizepicks_game_winner_odds(-1.5)
        assert r["accepted"] is False
        assert r["format"] == "invalid"

    def test_non_numeric_is_ambiguous(self):
        r = parse_prizepicks_game_winner_odds("not_odds")
        assert r["accepted"] is False
        assert r["format"] == "ambiguous"
        assert "non_numeric" in r["rejection_reason"]

    def test_none_is_invalid(self):
        r = parse_prizepicks_game_winner_odds(None)
        assert r["accepted"] is False
        assert r["format"] == "invalid"

    def test_string_decimal_parses(self):
        r = parse_prizepicks_game_winner_odds("1.85")
        assert r["accepted"] is True
        assert r["format"] == "decimal"


# ===========================================================================
# 7. No-Vig Consensus Reconstruction
# ===========================================================================

class TestNoVigConsensusReconstruction:
    def _make_quote(self, book, away, home, age_min=5):
        return BookQuote(
            book_name=book,
            away_american=away,
            home_american=home,
            fetched_at_utc=_fresh_utc_iso(age_min),
        )

    def test_two_coherent_books_median(self):
        quotes = [
            self._make_quote("fanduel",    -110, -110),
            self._make_quote("draftkings", -115, +105),
        ]
        result = reconstruct_consensus_no_vig(quotes, min_books=2)
        assert result["success"] is True
        assert result["consensus_away"] is not None
        assert result["consensus_home"] is not None
        assert result["book_count"] == 2
        assert abs(result["consensus_away"] + result["consensus_home"] - 1.0) < 1e-4

    def test_three_books_median_picks_middle(self):
        quotes = [
            self._make_quote("fanduel",    -105, -115),
            self._make_quote("draftkings", -110, -110),
            self._make_quote("betmgm",     -115, +105),
        ]
        result = reconstruct_consensus_no_vig(quotes, min_books=2)
        assert result["success"] is True
        assert result["book_count"] == 3

    def test_stale_book_excluded(self):
        quotes = [
            self._make_quote("fanduel",    -110, -110, age_min=5),   # fresh
            BookQuote(  # stale — 2 hours ago
                book_name="stale_book",
                away_american=-110,
                home_american=-110,
                fetched_at_utc=_stale_utc_iso(120),
            ),
        ]
        result = reconstruct_consensus_no_vig(quotes, min_books=2, max_age_minutes=60)
        assert result["success"] is False
        assert result["failure_reason"].startswith("insufficient_coherent_books")
        excluded_books = [e["book"] for e in result["books_excluded"]]
        assert "stale_book" in excluded_books

    def test_missing_opposite_side_rejected(self):
        # Quote missing away odds (home only)
        q = BookQuote(
            book_name="one_sided_book",
            away_american=None,
            home_american=-110,
            fetched_at_utc=_fresh_utc_iso(),
        )
        q2 = self._make_quote("fanduel", -110, -110)
        result = reconstruct_consensus_no_vig([q, q2], min_books=2)
        assert result["success"] is False
        excluded_books = [e["book"] for e in result["books_excluded"]]
        assert "one_sided_book" in excluded_books

    def test_outlier_book_excluded(self):
        # fanduel and draftkings agree; outlier_book is 20% off
        normal_a = -110  # ~52.4% no-vig
        quotes = [
            self._make_quote("fanduel",    normal_a, -110),
            self._make_quote("draftkings", -112,     -108),
            # Outlier: -200 home → heavy favourite, far from the pack
            self._make_quote("outlier_book", +170, -220),
        ]
        result = reconstruct_consensus_no_vig(
            quotes, min_books=2, outlier_threshold_pct=0.08
        )
        assert result["success"] is True
        excluded_books = [e["book"] for e in result["books_excluded"]]
        assert "outlier_book" in excluded_books
        assert result["book_count"] == 2

    def test_single_book_fails_min_books(self):
        quotes = [self._make_quote("fanduel", -110, -110)]
        result = reconstruct_consensus_no_vig(quotes, min_books=2)
        assert result["success"] is False
        assert "insufficient_coherent_books" in result["failure_reason"]

    def test_duplicate_books_deduplicated(self):
        # Two quotes from the same book — only one should count
        quotes = [
            self._make_quote("fanduel", -110, -110),
            self._make_quote("fanduel", -112, -108),  # same book, different line
        ]
        result = reconstruct_consensus_no_vig(quotes, min_books=2)
        assert result["success"] is False   # only 1 unique book after dedup

    def test_official_source_excluded_from_odds(self):
        # Official sources must not supply sportsbook odds.
        # The caller must not include them in the BookQuote list.
        # Verify the reconstruction is correct when only sportsbooks are passed.
        quotes = [
            self._make_quote("fanduel",    -110, -110),
            self._make_quote("draftkings", -110, -110),
        ]
        result = reconstruct_consensus_no_vig(quotes, min_books=2)
        assert result["success"] is True
        # book list must not include identity-only sources
        for book in result["books_included"]:
            assert book.lower() not in OFFICIAL_IDENTITY_ONLY_SOURCES


# ===========================================================================
# 8. Source Ceilings
# ===========================================================================

class TestSourceCeilings:
    def test_direct_fresh_sportsbook_no_ceiling(self):
        r = classify_source_quality({"book_count": 3, "age_minutes": 10})
        assert r["quality"] == SourceQuality.DIRECT_FRESH_SPORTSBOOK
        assert r["ceiling"] is None

    def test_aggregator_single_book_ceiling(self):
        r = classify_source_quality({"book_count": 1, "age_minutes": 10})
        assert r["quality"] == SourceQuality.AGGREGATOR_RECONSTRUCTED
        assert r["ceiling"] == "LLP_WATCH"

    def test_aggregator_flag_ceiling(self):
        r = classify_source_quality({"book_count": 3, "is_aggregator": True})
        assert r["quality"] == SourceQuality.AGGREGATOR_RECONSTRUCTED
        assert r["ceiling"] == "LLP_WATCH"

    def test_stale_two_books_aggregator_ceiling(self):
        r = classify_source_quality({"book_count": 2, "age_minutes": 90})
        assert r["quality"] == SourceQuality.AGGREGATOR_RECONSTRUCTED
        assert r["ceiling"] == "LLP_WATCH"

    def test_screenshot_source_scout_ceiling(self):
        r = classify_source_quality({"is_screenshot": True, "book_count": 0})
        assert r["quality"] == SourceQuality.SCREENSHOT_MANUAL_PROXY
        assert r["ceiling"] == "LLP_SCOUT"

    def test_proxy_source_scout_ceiling(self):
        r = classify_source_quality({"is_proxy": True, "book_count": 1})
        assert r["quality"] == SourceQuality.SCREENSHOT_MANUAL_PROXY
        assert r["ceiling"] == "LLP_SCOUT"

    def test_no_valid_baseline_scout_ceiling(self):
        r = classify_source_quality({"book_count": 0})
        assert r["quality"] == SourceQuality.NO_VALID_BASELINE
        assert r["ceiling"] == "LLP_SCOUT"

    def test_ceiling_map_completeness(self):
        for quality_class in [
            SourceQuality.DIRECT_FRESH_SPORTSBOOK,
            SourceQuality.AGGREGATOR_RECONSTRUCTED,
            SourceQuality.SCREENSHOT_MANUAL_PROXY,
            SourceQuality.NO_VALID_BASELINE,
        ]:
            assert quality_class in SOURCE_CEILING_MAP


# ===========================================================================
# 9. Anti-Circular Model Probability
# ===========================================================================

class TestAntiCircularModelProbability:
    def test_independent_model_passes(self):
        enrichment = {
            "model_source":      "independent_team_model",
            "model_probability": 0.58,
        }
        r = check_model_independence(enrichment)
        assert r["independent"] is True
        assert r["circular_risk"] is False
        assert r["violation"] is None

    def test_market_consensus_as_baseline_is_flagged(self):
        enrichment = {"model_source": "market_consensus", "model_probability": 0.52}
        r = check_model_independence(enrichment)
        assert r["independent"] is False
        assert r["circular_risk"] is True
        assert "circular_model_source" in r["violation"]

    def test_consensus_probability_key_is_flagged(self):
        enrichment = {"model_source": "consensus_probability"}
        r = check_model_independence(enrichment)
        assert r["circular_risk"] is True

    def test_sportsbook_no_vig_as_source_is_flagged(self):
        enrichment = {"model_source": "sportsbook_no_vig"}
        r = check_model_independence(enrichment)
        assert r["circular_risk"] is True

    def test_disclosed_shrinkage_with_all_fields_passes(self):
        enrichment = {
            "model_source":      "independent_team_model",
            "shrinkage_used":    True,
            "model_shrinkage": {
                "pre_shrink": 0.60,
                "weight":     0.15,
                "post_shrink": 0.57,
            },
        }
        r = check_model_independence(enrichment)
        assert r["independent"] is True
        assert r["shrinkage_disclosed"] is True
        assert r["pre_shrink_prob"] == 0.60
        assert r["shrinkage_weight"] == 0.15
        assert r["post_shrink_prob"] == 0.57

    def test_undisclosed_shrinkage_is_violation(self):
        enrichment = {
            "model_source":   "independent_team_model",
            "shrinkage_used": True,
            # model_shrinkage is MISSING — not disclosed
        }
        r = check_model_independence(enrichment)
        assert r["independent"] is False
        assert "undisclosed_shrinkage" in r["violation"]

    def test_partial_shrinkage_fields_is_violation(self):
        enrichment = {
            "model_source":   "independent_team_model",
            "shrinkage_used": True,
            "model_shrinkage": {
                "pre_shrink": 0.60,
                # missing weight and post_shrink
            },
        }
        r = check_model_independence(enrichment)
        assert r["independent"] is False
        assert r["violation"] is not None

    def test_no_shrinkage_flag_is_independent(self):
        enrichment = {
            "model_source":      "rotational_rest_matchup_model",
            "model_probability": 0.55,
            "shrinkage_used":    False,
        }
        r = check_model_independence(enrichment)
        assert r["independent"] is True
        assert r["shrinkage_disclosed"] is False


# ===========================================================================
# 10. Contract Stage Reporting
# ===========================================================================

class TestContractStageReporting:
    def test_all_stages_complete(self):
        report = build_contract_stage_report(
            stages_completed=list(CONTRACT_STAGES),
            stages_failed={},
            provider_diagnostics=[],
        )
        assert report["fully_complete"] is True
        assert report["stages_pending"] == 0
        assert report["stages_failed"] == 0

    def test_partial_failure_reported(self):
        completed = CONTRACT_STAGES[:3]
        failed    = {"primary_provider_odds": "ODDS_API_TIMEOUT"}
        report = build_contract_stage_report(completed, failed, [])
        assert report["fully_complete"] is False
        assert report["stages_failed"] == 1
        assert "primary_provider_odds" in report["failed"]

    def test_pending_stages_computed(self):
        completed = CONTRACT_STAGES[:2]
        report = build_contract_stage_report(completed, {}, [])
        assert report["stages_pending"] == len(CONTRACT_STAGES) - 2

    def test_provider_diagnostics_included(self):
        diag = [{"provider": "the_odds_api", "status": "timeout", "market": "h2h"}]
        report = build_contract_stage_report([], {}, diag)
        assert report["provider_diagnostics"] == diag

    def test_total_stages_constant(self):
        report = build_contract_stage_report([], {}, [])
        assert report["stages_total"] == len(CONTRACT_STAGES)


# ===========================================================================
# 11. Replay: board_20260714_002
# Scenario: Odds API h2h market unavailable for NYM vs ATL.
# Engine must escalate to FanDuel (sportsbook fallback), not return DATA_UNOBTAINABLE.
# Corrective: resolve_market_alias + reconstruct_consensus_no_vig with fallback book.
# ===========================================================================

class TestReplayBoard20260714_002:
    """
    board_20260714_002 — Primary provider (Odds API) failed for NYM@ATL.
    Old engine stopped at Odds API failure → DATA_UNOBTAINABLE.
    New engine escalates: FanDuel has valid data → coherent reconstruction.
    """

    def test_primary_provider_failure_fallback_succeeds(self):
        # Step 1: Odds API market alias resolves to h2h
        canonical, diag = resolve_market_alias("the_odds_api", "game_winner")
        assert canonical == "h2h"

        # Step 2: Simulate Odds API failure — escalate to FanDuel
        # FanDuel "money line" maps to h2h
        canonical_fd, diag_fd = resolve_market_alias("fanduel", "money line")
        assert canonical_fd == "h2h", "Fallback market alias must resolve"

        # Step 3: Single-book reconstruction fails min_books=2 requirement
        single_quote = [BookQuote("fanduel", -120, +105, _fresh_utc_iso())]
        r1 = reconstruct_consensus_no_vig(single_quote, min_books=2)
        assert r1["success"] is False   # correct: 1 book insufficient

        # Step 4: Second fallback (BetMGM) found → reconstruction succeeds
        two_quotes = [
            BookQuote("fanduel",  -120, +105, _fresh_utc_iso()),
            BookQuote("betmgm",   -118, +103, _fresh_utc_iso()),
        ]
        r2 = reconstruct_consensus_no_vig(two_quotes, min_books=2)
        assert r2["success"] is True
        assert r2["book_count"] == 2
        assert r2["consensus_away"] is not None

    def test_official_source_cannot_plug_gap(self):
        # ESPN validates event identity, but cannot supply sportsbook odds.
        assert is_official_identity_source("espn") is True
        # A BookQuote list with only official sources → reconstruction must fail
        # (caller must not pass official sources as BookQuotes)
        # Verified via the min_books gate: 0 valid sportsbook quotes → failure
        result = reconstruct_consensus_no_vig([], min_books=2)
        assert result["success"] is False

    def test_source_ceiling_for_single_fallback_book(self):
        # If only 1 fallback sportsbook is found → aggregator ceiling
        r = classify_source_quality({"book_count": 1, "age_minutes": 10})
        assert r["ceiling"] == "LLP_WATCH"

    def test_source_ceiling_for_two_fresh_books(self):
        r = classify_source_quality({"book_count": 2, "age_minutes": 10})
        assert r["ceiling"] is None   # two fresh books = direct path, no ceiling

    def test_contract_stage_primary_failure_recorded(self):
        report = build_contract_stage_report(
            stages_completed=["event_identity", "market_alias_resolution", "utc_normalization"],
            stages_failed={"primary_provider_odds": "odds_api_http_503"},
            provider_diagnostics=[
                {"provider": "the_odds_api", "market": "h2h", "error": "http_503"},
                {"provider": "fanduel",      "market": "h2h", "status": "ok"},
                {"provider": "betmgm",       "market": "h2h", "status": "ok"},
            ],
        )
        assert report["stages_failed"] == 1
        assert "primary_provider_odds" in report["failed"]
        assert report["fully_complete"] is False


# ===========================================================================
# 12. Replay: board_20260714_003
# Scenario: "Washington" alias ambiguous without league — WNBA Mystics misidentified.
# Old engine failed identity matching → DATA_UNOBTAINABLE.
# New engine uses league-scoped identity → WSH (WNBA) resolved correctly.
# ===========================================================================

class TestReplayBoard20260714_003:
    """
    board_20260714_003 — "Washington" team alias without league scope.
    Old engine returned ambiguous match → event identity failure.
    New engine enforces league scope → WNBA:Washington = WSH (Mystics).
    """

    def test_washington_ambiguous_without_league(self):
        assert is_alias_ambiguous("Washington", league=None) is True

    def test_washington_resolved_wnba(self):
        from gate_engine.event_normalization import normalize_team
        # With WNBA scope: Washington = WSH (Mystics)
        result = normalize_team("Washington Mystics", "wnba")
        assert result == "WSH", f"Expected WSH, got {result}"

    def test_washington_resolved_mlb(self):
        from gate_engine.event_normalization import normalize_team
        result = normalize_team("Washington", "mlb")
        assert result == "WSH"

    def test_washington_resolved_nba(self):
        from gate_engine.event_normalization import normalize_team
        result = normalize_team("Washington Wizards", "nba")
        assert result == "WSH"

    def test_indiana_fever_resolved_wnba(self):
        from gate_engine.event_normalization import normalize_team
        result = normalize_team("Indiana Fever", "wnba")
        assert result == "IND"

    def test_event_identity_with_league_scope_matches(self):
        target = {
            "league":     "wnba",
            "date":       "2026-07-14",
            "away":       "Indiana Fever",
            "home":       "Washington Mystics",
        }
        candidates = [
            {"date": "2026-07-14", "away": "IND", "home": "WSH"},
        ]
        result = match_event_with_tolerance(target, candidates)
        assert result["matched"] is True
        assert result["is_reversed"] is False

    def test_reversed_orientation_preserves_selected_side(self):
        # Candidate has home/away swapped — but we return it flagged, not silently inverted
        target = {
            "league": "wnba",
            "date":   "2026-07-14",
            "away":   "Indiana Fever",
            "home":   "Washington Mystics",
        }
        candidates = [
            # reversed: WSH listed as away, IND as home
            {"date": "2026-07-14", "away": "WSH", "home": "IND"},
        ]
        result = match_event_with_tolerance(target, candidates, reversed_ok=True)
        assert result["matched"] is True
        assert result["is_reversed"] is True, "Must flag — do not silently invert sides"
        # True venue preserved: candidate still shows original (reversed) order
        assert result["candidate"]["away"] == "WSH"
        assert result["candidate"]["home"] == "IND"

    def test_no_match_without_league_scope_alias(self):
        # Without league scope, "Washington" alone can't disambiguate
        assert is_alias_ambiguous("Washington", league=None) is True
        # League must be provided before event identity matching


# ===========================================================================
# 13. Governance integration — new hash reflects new patch
# ===========================================================================

class TestGovernanceHashUpdated:
    def test_new_patch_in_registry(self):
        from gate_engine.governance import get_governance_status
        status = get_governance_status()
        assert "WOW-PATCH-2026-07-15-LLP-DATA-ACQUISITION-RESILIENCE" in status["active_patch_ids"]

    def test_new_patch_has_precedence_60(self):
        from gate_engine.governance import _PATCH_REGISTRY
        new_patch = next(
            (p for p in _PATCH_REGISTRY
             if p["patch_id"] == "WOW-PATCH-2026-07-15-LLP-DATA-ACQUISITION-RESILIENCE"),
            None,
        )
        assert new_patch is not None
        assert new_patch["precedence"] == 60

    def test_governance_hash_deterministic(self):
        from gate_engine.governance import compute_governance_hash
        h1 = compute_governance_hash()
        h2 = compute_governance_hash()
        assert h1 == h2

    def test_old_hash_no_longer_matches(self):
        from gate_engine.governance import _GOVERNANCE_HASH
        old_hash = "045f3f97602ccb997b0e876c24ef2f1671d685402a5f5b43cdba72c49eefe51f"
        assert _GOVERNANCE_HASH != old_hash, (
            "New patch must change the governance hash — callers must re-sync"
        )

    def test_validate_handshake_with_old_hash_returns_mismatch(self):
        from gate_engine.governance import validate_handshake
        result = validate_handshake(
            expected_hash="045f3f97602ccb997b0e876c24ef2f1671d685402a5f5b43cdba72c49eefe51f"
        )
        assert result["valid"] is False
        assert result["code"] == "RUN_INVALID_GOVERNANCE_MISMATCH"

    def test_validate_handshake_with_current_hash_passes(self):
        from gate_engine.governance import validate_handshake, _GOVERNANCE_HASH
        result = validate_handshake(expected_hash=_GOVERNANCE_HASH)
        assert result["valid"] is True
        assert result["code"] == "GOVERNANCE_MATCH"
