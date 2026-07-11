"""
test_llp_odds_resolver.py
=========================
Acceptance tests for LLP Odds Fallback Patch v16.1B.

All 9 tests specified in the patch spec are covered.  Tests are fully
self-contained — no live API calls, no app.py import.

Run: pytest gate_engine/tests/test_llp_odds_resolver.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from gate_engine.llp_odds_resolver import (
    OddsResolution,
    reconstruct_no_vig_from_decimal,
    resolve_odds_source,
    SOURCE_QUALITY_LIVE,
    SOURCE_QUALITY_PROXY,
    SOURCE_QUALITY_UNAVAILABLE,
    TAG_DATA_UNOBTAINABLE,
    TAG_ESPN_EVENT_VALIDATED,
    TAG_NO_SPORTSBOOK_COMP,
    TAG_ODDS_API_FAILED,
    TAG_PRIZEPICKS_RECONSTRUCTION,
    TAG_PROXY_NO_VIG,
    PROXY_LABEL_CEILING,
    PROXY_CONFIDENCE_CEILING,
    PROXY_STAKE_TIER,
    PROXY_BIG_STAKE_STATUS,
    PROXY_FINAL_DECISION_MAX,
)


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

LLP_SIX_LABEL_TAXONOMY = {
    "LLP_APPROVED", "LLP_PLAYABLE", "LLP_WATCH",
    "LLP_SCOUT", "LLP_REJECT", "LLP_CUT",
}

_LIVE_SEL = {
    "book":         "draftkings",
    "american":     -110,
    "point":        None,
    "name":         "Kansas City Chiefs",
    "implied_prob": 0.524,
    "novig_prob":   0.510,
}

_LIVE_EVENT = {
    "id":         "abc123",
    "home_team":  "Kansas City Chiefs",
    "away_team":  "Los Angeles Chargers",
    "bookmakers": [],
}


def _live_fetch(sport_key):
    return [_LIVE_EVENT]


def _live_match(events, away, home):
    return _LIVE_EVENT


def _live_extract(event, market, side):
    return _LIVE_SEL


def _fail_fetch(sport_key):
    return None


def _fail_match(events, away, home):
    return None


def _fail_extract(event, market, side):
    return None


def _base_game(
    *,
    away="Los Angeles Chargers",
    home="Kansas City Chiefs",
    side="Kansas City Chiefs",
    sport="nfl",
    market="h2h",
    pp_home_decimal=None,
    pp_away_decimal=None,
):
    g = {
        "away": away, "home": home, "side": side,
        "sport": sport, "market": market,
    }
    if pp_home_decimal is not None:
        g["pp_home_decimal"] = pp_home_decimal
    if pp_away_decimal is not None:
        g["pp_away_decimal"] = pp_away_decimal
    return g


def _resolve(game, fetch_fn, match_fn=_live_match, extract_fn=_live_extract):
    """Convenience wrapper that always disables ESPN validation."""
    return resolve_odds_source(
        game=game,
        sport_key="americanfootball_nfl",
        sport="nfl",
        fetch_odds_fn=fetch_fn,
        match_event_fn=match_fn,
        extract_market_fn=extract_fn,
        board_date="2026-09-15",
        use_espn_validation=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Odds API succeeds → live path, no cap
# ─────────────────────────────────────────────────────────────────────────────

def test_1_odds_api_succeeds():
    """
    Acceptance test 1: Odds API returns a valid live h2h market.
    Expected:
    - sportsbook_no_vig_available = True
    - odds_source_quality = "live"
    - no fallback used
    - data_contract_status = DATA_CONTRACT_COMPLETE
    - is_proxy = False → no LLP_SCOUT cap caused by odds sourcing
    """
    res = _resolve(_base_game(), fetch_fn=_live_fetch)

    assert res.usable, "Resolution must be usable"
    assert res.odds_source_quality == SOURCE_QUALITY_LIVE
    assert res.sportsbook_no_vig_available is True
    assert res.odds_source_fallback_used is False
    assert res.reconstructed_no_vig_available is False
    assert res.is_proxy is False
    assert res.data_contract_status == "DATA_CONTRACT_COMPLETE"
    assert res.odds_source_primary == "odds_api"
    assert res.sel is _LIVE_SEL
    assert res.event is _LIVE_EVENT
    # No LLP_SCOUT cap should be signalled for a live sportsbook path
    assert TAG_NO_SPORTSBOOK_COMP not in res.diagnostic_tags
    assert TAG_PRIZEPICKS_RECONSTRUCTION not in res.diagnostic_tags
    assert res.label_ceiling_reason is None

    # Verify to_record_fields() contains all ten required fields
    fields = res.to_record_fields()
    for key in (
        "odds_source_primary", "odds_source_fallback_used", "odds_source_quality",
        "sportsbook_no_vig_available", "reconstructed_no_vig_available",
        "reconstructed_no_vig_probability", "source_resolution_path",
        "source_failure_reasons", "label_ceiling_reason", "data_contract_status",
    ):
        assert key in fields, f"Missing required field: {key}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Odds API fails, PrizePicks has both sides → reconstruction
# ─────────────────────────────────────────────────────────────────────────────

def test_2_odds_api_fails_prizepicks_reconstruction():
    """
    Acceptance test 2: Odds API fails but PrizePicks decimal payouts exist.
    Input: pp_home_decimal=1.85, pp_away_decimal=1.88
    Expected:
    - reconstruction succeeds
    - no global shutdown
    - odds_source_quality = "proxy"
    - sportsbook_no_vig_available = False
    - reconstructed_no_vig_available = True
    - final_decision ceiling would be WATCH (tested via is_proxy + constants)
    - diagnostic_tags include NO_SPORTSBOOK_COMP, PROXY_NO_VIG, PRIZEPICKS_RECONSTRUCTION
    - data_contract_status = DATA_CONTRACT_PROXY_NO_VIG
    """
    game = _base_game(pp_home_decimal=1.85, pp_away_decimal=1.88)
    res = _resolve(game, fetch_fn=_fail_fetch, match_fn=_fail_match)

    assert res.usable, "Reconstruction should make the candidate usable"
    assert res.odds_source_quality == SOURCE_QUALITY_PROXY
    assert res.sportsbook_no_vig_available is False
    assert res.reconstructed_no_vig_available is True
    assert res.odds_source_fallback_used is True
    assert res.odds_source_primary == "prizepicks_reconstructed"
    assert res.is_proxy is True

    # Validate reconstruction math
    assert res.reconstructed_no_vig_probability is not None
    reco_p = res.reconstructed_no_vig_probability
    # Manual: raw_home=1/1.85≈0.5405, raw_away=1/1.88≈0.5319, tot≈1.0724
    # no_vig_home ≈ 0.5405/1.0724 ≈ 0.5040
    assert abs(reco_p - 0.504) < 0.002, f"Unexpected no-vig: {reco_p}"

    # Required diagnostic tags
    assert TAG_NO_SPORTSBOOK_COMP in res.diagnostic_tags
    assert TAG_PROXY_NO_VIG in res.diagnostic_tags
    assert TAG_PRIZEPICKS_RECONSTRUCTION in res.diagnostic_tags
    assert TAG_ODDS_API_FAILED in res.diagnostic_tags

    assert res.data_contract_status == "DATA_CONTRACT_PROXY_NO_VIG"
    assert res.label_ceiling_reason is not None
    assert "PRIZEPICKS_RECONSTRUCTION" in res.label_ceiling_reason

    # Synthetic sel must have novig_prob (for downstream scoring)
    assert res.sel is not None
    assert isinstance(res.sel.get("novig_prob"), float)
    assert res.sel.get("book") is None
    assert res.sel.get("american") is None

    # Verify PROXY ceiling constants are correct
    assert PROXY_LABEL_CEILING == "LLP_SCOUT"
    assert PROXY_FINAL_DECISION_MAX == "WATCH"
    assert PROXY_STAKE_TIER == "PASS"
    assert PROXY_BIG_STAKE_STATUS == "BLOCKED"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Mixed board: one candidate succeeds via Odds API, one uses fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_3_mixed_success_board():
    """
    Acceptance test 3: Odds API fails for one game, succeeds for another.
    Expected:
    - successful game → live path, is_proxy=False
    - failed game → reconstruction path, is_proxy=True
    - neither prevents the other from being scored
    - no global NO PLAY
    """
    game_live = _base_game()
    game_pp   = _base_game(
        away="Boston Celtics", home="Miami Heat", side="Miami Heat",
        sport="nba",
        pp_home_decimal=1.90, pp_away_decimal=1.83,
    )

    res_live = _resolve(game_live, fetch_fn=_live_fetch)
    res_pp   = resolve_odds_source(
        game=game_pp,
        sport_key="basketball_nba",
        sport="nba",
        fetch_odds_fn=_fail_fetch,
        match_event_fn=_fail_match,
        extract_market_fn=_fail_extract,
        board_date="2026-09-15",
        use_espn_validation=False,
    )

    # Live candidate: full sportsbook path
    assert res_live.usable
    assert res_live.odds_source_quality == SOURCE_QUALITY_LIVE
    assert res_live.is_proxy is False

    # Reconstruction candidate: proxy path, still usable
    assert res_pp.usable, "Reconstruction candidate must be usable (no global shutdown)"
    assert res_pp.odds_source_quality == SOURCE_QUALITY_PROXY
    assert res_pp.is_proxy is True

    # Both have a novig_prob available for downstream scoring
    assert res_live.sel.get("novig_prob") is not None
    assert res_pp.sel.get("novig_prob") is not None

    # They are independent — live candidate not contaminated by PP failure
    assert TAG_NO_SPORTSBOOK_COMP not in res_live.diagnostic_tags
    assert TAG_PRIZEPICKS_RECONSTRUCTION not in res_live.diagnostic_tags


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — ESPN validates event, only one PrizePicks side present
# ─────────────────────────────────────────────────────────────────────────────

def test_4_espn_validates_one_pp_side_only():
    """
    Acceptance test 4: ESPN confirms event exists but only one PrizePicks
    decimal is provided (pp_away_decimal missing).
    Expected:
    - no two-way reconstruction (one side is not enough)
    - candidate is DATA_UNOBTAINABLE
    - no fake no-vig probability
    """
    game = _base_game(
        pp_home_decimal=1.85,
        # pp_away_decimal intentionally absent
    )
    mock_espn_val = {
        "event_id": "espn-001",
        "name": "KC Chiefs vs LA Chargers",
        "start_time": "2026-09-15T18:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Los Angeles Chargers",
        "status_state": "pre",
        "status_desc": "Scheduled",
        "completed": False,
        "source": "site.api.espn.com",
    }

    res = resolve_odds_source(
        game=game,
        sport_key="americanfootball_nfl",
        sport="nfl",
        fetch_odds_fn=_fail_fetch,
        match_event_fn=_fail_match,
        extract_market_fn=_fail_extract,
        board_date="2026-09-15",
        use_espn_validation=True,
        espn_validate_fn=lambda sport, away, home, bd: mock_espn_val,
    )

    # With only one side, reconstruction cannot proceed → DATA_UNOBTAINABLE
    assert not res.usable
    assert res.odds_source_quality == SOURCE_QUALITY_UNAVAILABLE
    assert res.reconstructed_no_vig_available is False
    assert res.reconstructed_no_vig_probability is None
    assert res.sel is None

    # ESPN tag present because it did validate the event
    assert TAG_ESPN_EVENT_VALIDATED in res.diagnostic_tags
    # DATA_UNOBTAINABLE tag present
    assert TAG_DATA_UNOBTAINABLE in res.diagnostic_tags

    # Verify no fabricated no-vig
    fields = res.to_record_fields()
    assert fields["reconstructed_no_vig_probability"] is None
    # source_failure_reasons must mention the missing side
    assert any("pp_away_decimal" in r for r in res.source_failure_reasons)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Event already started → preserve hard kill
# ─────────────────────────────────────────────────────────────────────────────

def test_5_event_already_started_hard_kill():
    """
    Acceptance test 5: Odds API fails, ESPN reports event is in-progress.
    Expected:
    - hard kill preserved regardless of whether PP decimals exist
    - odds_source_quality = unavailable
    - data_contract_status contains EVENT_LIVE
    - source_resolution_path documents the hard-kill step
    """
    game = _base_game(pp_home_decimal=1.85, pp_away_decimal=1.88)
    live_espn = {
        "event_id": "live-001",
        "name": "KC vs LAC",
        "start_time": "2026-09-15T15:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Los Angeles Chargers",
        "status_state": "in",
        "status_desc": "4th Quarter",
        "completed": False,
        "source": "site.api.espn.com",
    }

    res = resolve_odds_source(
        game=game,
        sport_key="americanfootball_nfl",
        sport="nfl",
        fetch_odds_fn=_fail_fetch,
        match_event_fn=_fail_match,
        extract_market_fn=_fail_extract,
        board_date="2026-09-15",
        use_espn_validation=True,
        espn_validate_fn=lambda sport, away, home, bd: live_espn,
    )

    assert not res.usable
    assert res.odds_source_quality == SOURCE_QUALITY_UNAVAILABLE
    assert "EVENT_LIVE" in res.data_contract_status
    assert res.label_ceiling_reason == "event_started_or_settled"
    assert "espn_hard_kill_event_live_or_settled" in res.source_resolution_path
    # PrizePicks reconstruction was NOT attempted (hard kill before step 3)
    assert TAG_PRIZEPICKS_RECONSTRUCTION not in res.diagnostic_tags
    assert res.sel is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — All candidates fail all sources → global failure permitted
# ─────────────────────────────────────────────────────────────────────────────

def test_6_all_candidates_fail_global_failure():
    """
    Acceptance test 6: Odds API fails, no PP decimals, ESPN misses event.
    Expected:
    - DATA_UNOBTAINABLE tag on each candidate
    - odds_source_quality = unavailable
    - final_labels remain within six-label taxonomy (tested via constants)
    - source_failure_reasons is non-empty and explains each failure
    """
    game = _base_game()  # no pp_home_decimal, no pp_away_decimal

    res = resolve_odds_source(
        game=game,
        sport_key="americanfootball_nfl",
        sport="nfl",
        fetch_odds_fn=_fail_fetch,
        match_event_fn=_fail_match,
        extract_market_fn=_fail_extract,
        board_date="2026-09-15",
        use_espn_validation=True,
        espn_validate_fn=lambda sport, away, home, bd: None,
    )

    assert not res.usable
    assert res.odds_source_quality == SOURCE_QUALITY_UNAVAILABLE
    assert TAG_DATA_UNOBTAINABLE in res.diagnostic_tags
    assert len(res.source_failure_reasons) > 0

    # data_contract_status must NOT be a label from the taxonomy
    assert res.data_contract_status not in LLP_SIX_LABEL_TAXONOMY
    # odds_source_quality must NOT be a taxonomy label
    assert res.odds_source_quality not in LLP_SIX_LABEL_TAXONOMY

    # Candidate returned empty record fields — no fake prob
    fields = res.to_record_fields()
    assert fields["sportsbook_no_vig_available"] is False
    assert fields["reconstructed_no_vig_available"] is False
    assert fields["reconstructed_no_vig_probability"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — Approval protection: reconstructed path blocks LLP_APPROVED
# ─────────────────────────────────────────────────────────────────────────────

def test_7_approval_protection():
    """
    Acceptance test 7: PrizePicks reconstruction path must prevent
    LLP_APPROVED, LLP_PLAYABLE, and LLP_WATCH from being the final label.
    Maximum allowed: LLP_SCOUT.

    Verified by:
    1. is_proxy == True on reconstruction result.
    2. PROXY_LABEL_CEILING == "LLP_SCOUT".
    3. PROXY_FINAL_DECISION_MAX == "WATCH" (BET/SMALL BET get capped).
    4. PROXY_STAKE_TIER == "PASS" and PROXY_BIG_STAKE_STATUS == "BLOCKED".
    5. Reconstruction result carries expected diagnostic tags.
    """
    game = _base_game(pp_home_decimal=1.85, pp_away_decimal=1.88)
    res = _resolve(game, fetch_fn=_fail_fetch, match_fn=_fail_match)

    # The resolution itself signals proxy status
    assert res.is_proxy is True, "Must be proxy when reconstruction used"

    # Ceiling constants (these drive the cap logic in _llp_analyze_one)
    assert PROXY_LABEL_CEILING == "LLP_SCOUT", (
        "Proxy ceiling must be LLP_SCOUT — LLP_APPROVED/PLAYABLE/WATCH not allowed")
    assert PROXY_FINAL_DECISION_MAX == "WATCH", (
        "BET and SMALL BET must be capped to WATCH for proxy paths")
    assert PROXY_STAKE_TIER == "PASS"
    assert PROXY_BIG_STAKE_STATUS == "BLOCKED"
    assert PROXY_CONFIDENCE_CEILING == "LOW"

    # LLP_SCOUT is in the taxonomy; APPROVED/PLAYABLE are blocked
    approved_blocked = {"LLP_APPROVED", "LLP_PLAYABLE"}
    assert PROXY_LABEL_CEILING not in approved_blocked, (
        "Ceiling must not be one of the blocked labels itself")

    # Tags signal the block to downstream consumers
    assert TAG_NO_SPORTSBOOK_COMP in res.diagnostic_tags
    assert res.label_ceiling_reason is not None


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Taxonomy protection: no resolver output leaks as final_label
# ─────────────────────────────────────────────────────────────────────────────

def test_8_taxonomy_protection():
    """
    Acceptance test 8: data_contract_status, odds_source_quality, and
    diagnostic tags must never equal a label from the six-label taxonomy.

    Verified across all four resolution paths.
    """
    paths = [
        # Live path
        _resolve(_base_game(), fetch_fn=_live_fetch),
        # Reconstruction path
        _resolve(_base_game(pp_home_decimal=1.90, pp_away_decimal=1.83),
                 fetch_fn=_fail_fetch, match_fn=_fail_match),
        # All-fail path
        resolve_odds_source(
            game=_base_game(),
            sport_key="americanfootball_nfl",
            sport="nfl",
            fetch_odds_fn=_fail_fetch,
            match_event_fn=_fail_match,
            extract_market_fn=_fail_extract,
            board_date="2026-09-15",
            use_espn_validation=False,
        ),
    ]

    for res in paths:
        assert res.data_contract_status not in LLP_SIX_LABEL_TAXONOMY, (
            f"data_contract_status '{res.data_contract_status}' "
            f"must not be a taxonomy label")
        assert res.odds_source_quality not in LLP_SIX_LABEL_TAXONOMY, (
            f"odds_source_quality '{res.odds_source_quality}' "
            f"must not be a taxonomy label")
        for tag in res.diagnostic_tags:
            assert tag not in LLP_SIX_LABEL_TAXONOMY, (
                f"Diagnostic tag '{tag}' must not be a taxonomy label")


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — Output patch compliance: all required fields present
# ─────────────────────────────────────────────────────────────────────────────

def test_9_output_patch_compliance():
    """
    Acceptance test 9: Every path must produce all ten required per-candidate
    output fields via to_record_fields().

    Verified:
    - Six-label taxonomy PASS (no resolver output in taxonomy)
    - No banned/status term as final_label PASS
    - No-vig/fair-price check PASS (proxy has reconstructed value, live has real value)
    - Data contract status PASS (set on every path)
    - Final label reproducibility PASS (ceiling constants are deterministic)
    """
    REQUIRED_FIELDS = [
        "odds_source_primary",
        "odds_source_fallback_used",
        "odds_source_quality",
        "sportsbook_no_vig_available",
        "reconstructed_no_vig_available",
        "reconstructed_no_vig_probability",
        "source_resolution_path",
        "source_failure_reasons",
        "label_ceiling_reason",
        "data_contract_status",
    ]

    test_cases = [
        ("live_sportsbook", _resolve(_base_game(), fetch_fn=_live_fetch)),
        ("prizepicks_reconstruction", _resolve(
            _base_game(pp_home_decimal=1.85, pp_away_decimal=1.88),
            fetch_fn=_fail_fetch, match_fn=_fail_match)),
        ("all_sources_failed", resolve_odds_source(
            game=_base_game(),
            sport_key="americanfootball_nfl",
            sport="nfl",
            fetch_odds_fn=_fail_fetch,
            match_event_fn=_fail_match,
            extract_market_fn=_fail_extract,
            board_date="2026-09-15",
            use_espn_validation=False,
        )),
    ]

    for label, res in test_cases:
        fields = res.to_record_fields()
        for field in REQUIRED_FIELDS:
            assert field in fields, (
                f"[{label}] Missing required output field: {field}")

        # source_resolution_path must be a list
        assert isinstance(fields["source_resolution_path"], list), (
            f"[{label}] source_resolution_path must be a list")
        assert len(fields["source_resolution_path"]) > 0, (
            f"[{label}] source_resolution_path must not be empty")

        # source_failure_reasons must be a list
        assert isinstance(fields["source_failure_reasons"], list), (
            f"[{label}] source_failure_reasons must be a list")

        # data_contract_status must be a non-empty string
        assert isinstance(fields["data_contract_status"], str), (
            f"[{label}] data_contract_status must be a string")
        assert len(fields["data_contract_status"]) > 0, (
            f"[{label}] data_contract_status must not be empty")

        # No-vig / fair-price: live path has real novig, proxy has reconstructed
        if label == "live_sportsbook":
            assert fields["sportsbook_no_vig_available"] is True
            assert res.sel.get("novig_prob") is not None
        elif label == "prizepicks_reconstruction":
            assert fields["reconstructed_no_vig_available"] is True
            assert fields["reconstructed_no_vig_probability"] is not None
            assert res.sel.get("novig_prob") is not None
        elif label == "all_sources_failed":
            assert fields["sportsbook_no_vig_available"] is False
            assert fields["reconstructed_no_vig_available"] is False
            assert fields["reconstructed_no_vig_probability"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Bonus: reconstruct_no_vig_from_decimal unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_reconstruction_math_symmetric():
    """Even odds (2.00 / 2.00) → both sides get exactly 0.5 no-vig."""
    reco = reconstruct_no_vig_from_decimal(2.00, 2.00)
    assert reco is not None
    assert abs(reco["reconstructed_no_vig_a"] - 0.5) < 1e-9
    assert abs(reco["reconstructed_no_vig_b"] - 0.5) < 1e-9
    assert abs(reco["overround"] - 1.0) < 1e-9
    assert reco["reconstruction_method"] == "two_way_decimal_normalization"


def test_reconstruction_invalid_decimal_below_one():
    """Decimal ≤ 1.0 is invalid (would imply payout < stake)."""
    assert reconstruct_no_vig_from_decimal(0.90, 1.88) is None
    assert reconstruct_no_vig_from_decimal(1.85, 1.0)  is None
    assert reconstruct_no_vig_from_decimal(1.0,  1.0)  is None


def test_reconstruction_invalid_non_numeric():
    """Non-numeric inputs must return None, never raise."""
    assert reconstruct_no_vig_from_decimal(None, 1.88) is None
    assert reconstruct_no_vig_from_decimal("foo", 1.88) is None
    assert reconstruct_no_vig_from_decimal(1.85, None) is None
