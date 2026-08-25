"""
gate_engine/tests/test_balldontlie_acquisition.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

Regression tests for the BallDontLie TRUSTED_STRUCTURED_STATS acquisition layer.

Coverage:
  1.  Missing credentials → AUTH_REQUIRED, base model continues
  2.  RATE_LIMITED → structured status, never raises
  3.  AUTH_FAILED → structured status, never raises
  4.  WNBA game_log matches WOW canonical schema (list[float], most recent first)
  5.  WNBA box_score_log matches WOW canonical schema (list[dict])
  6.  L5/L10 constructed from chronological game records, not season averages
  7.  DNP rows (min < 1) excluded from game_log
  8.  Null source fields remain null — never imputed
  9.  MLB pitching outs → IP conversion correct (7 outs = 2.1 IP)
  10. MLB pitching game_log correct stat_key routing (IP, OUTS, K)
  11. GOAT pitch metrics absent when tier not GOAT
  12. BDL lineups cannot override official contradiction
  13. Odds not double-counted when same book/side/price already in enrichment
  14. Odds SOURCE_CONFLICT surfaced when same book/side has different price
  15. Game-log reconciliation: corroboration within threshold
  16. Game-log reconciliation: SOURCE_CONFLICT when materially different
  17. Reconciliation: higher-precedence source wins over BDL
  18. Reconciliation: BDL wins over lower-precedence source
  19. source_grade is "A-" (TRUSTED_STRUCTURED_STATS) in all contexts
  20. BallDontLieAdapter.source_grade == "A-"
  21. source_grade.py has balldontlie_api mapped to "A-"
  22. can_execute=False across all balldontlie submodules
  23. NBA combo stat PTS+REB+AST computed correctly per game row
  24. minutes_stats() returns correct mean/variance/cv from game rows
  25. fetch_bdl_player_package routes MLB pitcher vs batter by stat_key
  26. empty BDL response → NO_DATA, enrichment_game_log falls back to existing
  27. Patch ID registered in wow_runtime_manifest
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / stubs
# ─────────────────────────────────────────────────────────────────────────────

def _make_nba_row(
    pts=20.0, reb=5.0, ast=3.0, min_=32.0,
    date="2026-07-10", game_id="G1001", player_id="1234",
    stl=1.0, blk=0.0, fg3m=2.0,
    home_team_id="10", team_id="10",
):
    return {
        "game": {
            "id": game_id,
            "date": date,
            "season": 2025,
            "home_team_id": home_team_id,
            "home_team": {"id": home_team_id, "full_name": "Home Team"},
            "visitor_team": {"id": "20", "full_name": "Away Team"},
            "visitor_team_id": "20",
        },
        "player": {
            "id": player_id,
            "first_name": "Test",
            "last_name": "Player",
        },
        "team": {"id": team_id},
        "min":  str(min_),
        "pts":  pts,
        "reb":  reb,
        "ast":  ast,
        "stl":  stl,
        "blk":  blk,
        "fg3m": fg3m,
        "fga":  12.0,
        "fgm":  8.0,
        "fg3a": 5.0,
        "fta":  3.0,
        "ftm":  2.0,
        "oreb": 1.0,
        "dreb": 4.0,
        "tov":  2.0,
        "pf":   3.0,
    }


def _make_mlb_pitching_row(
    outs=18, bf=25, k=7, bb=2, h=5, er=2, hr=1, pc=96,
    date="2026-07-10", game_id="G2001", player_id="500",
):
    return {
        "game": {"id": game_id, "date": date, "season": 2026},
        "player": {"id": player_id, "first_name": "Ace", "last_name": "Pitcher"},
        "outs_pitched": outs,
        "batters_faced": bf,
        "strikeouts": k,
        "walks": bb,
        "hits_allowed": h,
        "earned_runs": er,
        "home_runs_allowed": hr,
        "pitch_count": pc,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Missing credentials → AUTH_REQUIRED
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_credentials_auth_required():
    from gate_engine.balldontlie.types import BDLStatus
    with patch.dict(os.environ, {}, clear=True):
        # Remove BDL keys if present
        env = {k: v for k, v in os.environ.items()
               if k not in ("balldontlie", "BALLDONTLIE_API_KEY")}
        with patch.dict(os.environ, env, clear=True):
            from gate_engine.balldontlie import client as c
            assert not c.credentials_available()

            from gate_engine.balldontlie.nba_wnba import fetch_player_package
            pkg = fetch_player_package("123", "WNBA", season=2026)
            assert pkg.acquisition_status == BDLStatus.AUTH_REQUIRED


def test_missing_credentials_base_model_continues():
    """AUTH_REQUIRED must not propagate as an exception to the caller."""
    with patch.dict(os.environ, {}, clear=True):
        env = {k: v for k, v in os.environ.items()
               if k not in ("balldontlie", "BALLDONTLIE_API_KEY")}
        with patch.dict(os.environ, env, clear=True):
            from gate_engine.balldontlie.nba_wnba import fetch_player_package
            # Must not raise
            pkg = fetch_player_package("999", "NBA", season=2026)
            assert pkg is not None
            assert pkg.acquisition_status in ("AUTH_REQUIRED", "AUTH_FAILED")


# ─────────────────────────────────────────────────────────────────────────────
# 2. RATE_LIMITED → structured status, never raises
# ─────────────────────────────────────────────────────────────────────────────

def test_rate_limited_returns_structured_status():
    from gate_engine.balldontlie.types import BDLStatus, BDLResponse

    mock_resp = BDLResponse(status=BDLStatus.RATE_LIMITED, endpoint="/wnba/v1/stats")
    # Patch at nba_wnba module level — it imported _get/credentials_available/detect_tier
    # by name at load time, so we must patch the names in that namespace.
    with patch("gate_engine.balldontlie.nba_wnba._get", return_value=mock_resp), \
         patch("gate_engine.balldontlie.nba_wnba.credentials_available", return_value=True), \
         patch("gate_engine.balldontlie.nba_wnba.detect_tier", return_value="FREE"):
        from gate_engine.balldontlie.nba_wnba import fetch_player_package
        pkg = fetch_player_package("123", "WNBA", season=2026)
        assert pkg.acquisition_status == BDLStatus.RATE_LIMITED


# ─────────────────────────────────────────────────────────────────────────────
# 3. AUTH_FAILED → structured status
# ─────────────────────────────────────────────────────────────────────────────

def test_auth_failed_returns_structured_status():
    from gate_engine.balldontlie.types import BDLStatus, BDLResponse

    mock_resp = BDLResponse(status=BDLStatus.AUTH_FAILED, endpoint="/wnba/v1/stats")
    with patch("gate_engine.balldontlie.client._get", return_value=mock_resp), \
         patch("gate_engine.balldontlie.client.credentials_available", return_value=True), \
         patch("gate_engine.balldontlie.client.detect_tier", return_value="FREE"):
        from gate_engine.balldontlie.nba_wnba import fetch_player_package
        pkg = fetch_player_package("123", "NBA", season=2026)
        assert pkg.acquisition_status == BDLStatus.AUTH_FAILED


# ─────────────────────────────────────────────────────────────────────────────
# 4. WNBA game_log matches WOW canonical schema
# ─────────────────────────────────────────────────────────────────────────────

def test_wnba_wow_game_log_schema():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLTier

    raw = _make_nba_row(pts=18.0, reb=7.0, ast=4.0, min_=30.0, date="2026-07-05")
    row = normalize_nba_wnba_row(raw, sport="WNBA", bdl_tier=BDLTier.FREE)
    assert not row.is_dnp
    assert isinstance(row.pts, float)
    assert row.pts == 18.0

    # Single-row package
    from gate_engine.balldontlie.types import BDLPlayerPackage
    pkg = BDLPlayerPackage(game_rows=[row], sport="WNBA")
    gl = pkg.wow_game_log("PTS", n=10)
    assert isinstance(gl, list)
    assert all(isinstance(v, float) for v in gl)
    assert gl == [18.0]


# ─────────────────────────────────────────────────────────────────────────────
# 5. WNBA box_score_log matches WOW canonical schema
# ─────────────────────────────────────────────────────────────────────────────

def test_wnba_wow_box_score_log_schema():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLPlayerPackage, BDLTier

    raw = _make_nba_row(pts=20.0, reb=8.0, ast=3.0, min_=35.0, date="2026-07-10")
    row = normalize_nba_wnba_row(raw, sport="WNBA", bdl_tier=BDLTier.FREE)
    pkg = BDLPlayerPackage(game_rows=[row], sport="WNBA")
    bsl = pkg.wow_box_score_log(n=10)
    assert isinstance(bsl, list)
    assert isinstance(bsl[0], dict)
    assert "game_date" in bsl[0]
    assert "provenance" in bsl[0]
    assert bsl[0]["pts"] == 20.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. L5/L10 from chronological game records, not season averages
# ─────────────────────────────────────────────────────────────────────────────

def test_l5_l10_from_game_records_not_season_averages():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLPlayerPackage, BDLTier

    # Three games oldest → newest
    rows = [
        normalize_nba_wnba_row(_make_nba_row(pts=10.0, date="2026-06-01"), "WNBA", BDLTier.FREE),
        normalize_nba_wnba_row(_make_nba_row(pts=20.0, date="2026-06-05"), "WNBA", BDLTier.FREE),
        normalize_nba_wnba_row(_make_nba_row(pts=30.0, date="2026-06-10"), "WNBA", BDLTier.FREE),
    ]
    pkg = BDLPlayerPackage(game_rows=rows, sport="WNBA")
    # nba_wnba.py stores game_rows oldest-first → wow_game_log most-recent-first
    gl = pkg.wow_game_log("PTS", n=5)
    # Most recent game (30.0) should be first
    assert gl[0] == 30.0
    assert gl[-1] == 10.0

    # season_averages must NOT appear in game_log
    pkg.season_averages = {"pts": 99.0, "_NOTE": "SEASON_AVERAGES"}
    gl2 = pkg.wow_game_log("PTS", n=5)
    assert 99.0 not in gl2


# ─────────────────────────────────────────────────────────────────────────────
# 7. DNP rows excluded from game_log
# ─────────────────────────────────────────────────────────────────────────────

def test_dnp_rows_excluded_from_game_log():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLPlayerPackage, BDLTier

    dnp_row = _make_nba_row(pts=20.0, min_=0.0, date="2026-06-01")
    active_row = _make_nba_row(pts=25.0, min_=32.0, date="2026-06-02")

    dnp  = normalize_nba_wnba_row(dnp_row,    "NBA", BDLTier.FREE)
    act  = normalize_nba_wnba_row(active_row, "NBA", BDLTier.FREE)
    assert dnp.is_dnp
    assert not act.is_dnp

    pkg = BDLPlayerPackage(game_rows=[dnp, act], sport="NBA")
    gl = pkg.wow_game_log("PTS", n=10)
    assert 20.0 not in gl
    assert 25.0 in gl


# ─────────────────────────────────────────────────────────────────────────────
# 8. Null source fields remain null
# ─────────────────────────────────────────────────────────────────────────────

def test_null_fields_preserved_exactly():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLTier

    raw = _make_nba_row(pts=18.0, min_=30.0)
    # Remove stl and blk to simulate null
    del raw["stl"]
    del raw["blk"]
    row = normalize_nba_wnba_row(raw, sport="WNBA", bdl_tier=BDLTier.FREE)
    assert row.stl is None
    assert row.blk is None
    assert "stl" in row.provenance.null_fields
    assert "blk" in row.provenance.null_fields


# ─────────────────────────────────────────────────────────────────────────────
# 9. MLB pitching outs → IP conversion
# ─────────────────────────────────────────────────────────────────────────────

def test_mlb_outs_to_ip_conversion():
    from gate_engine.balldontlie.normalizer import normalize_mlb_pitching_row
    from gate_engine.balldontlie.types import BDLTier

    # 7 outs = 2 full innings + 1 out = 2.1 IP
    raw = _make_mlb_pitching_row(outs=7)
    row = normalize_mlb_pitching_row(raw, bdl_tier=BDLTier.FREE)
    assert row.outs_recorded == 7
    assert row.ip == 2.1

    # 18 outs = 6.0 IP
    raw18 = _make_mlb_pitching_row(outs=18)
    row18 = normalize_mlb_pitching_row(raw18, bdl_tier=BDLTier.FREE)
    assert row18.ip == 6.0

    # 20 outs = 6 full + 2 remaining = 6.2 IP
    raw20 = _make_mlb_pitching_row(outs=20)
    row20 = normalize_mlb_pitching_row(raw20, bdl_tier=BDLTier.FREE)
    assert row20.ip == 6.2


# ─────────────────────────────────────────────────────────────────────────────
# 10. MLB pitching game_log stat_key routing
# ─────────────────────────────────────────────────────────────────────────────

def test_mlb_pitching_stat_key_routing():
    from gate_engine.balldontlie.normalizer import normalize_mlb_pitching_row
    from gate_engine.balldontlie.types import BDLPlayerPackage, BDLTier

    raw = _make_mlb_pitching_row(outs=18, k=7)
    row = normalize_mlb_pitching_row(raw, bdl_tier=BDLTier.FREE)
    pkg = BDLPlayerPackage(game_rows=[row], sport="MLB")

    # IP stat key
    ip_log = pkg.wow_game_log("IP", n=5)
    assert ip_log == [6.0]

    # OUTS stat key
    outs_log = pkg.wow_game_log("OUTS", n=5)
    assert outs_log == [18.0]

    # K stat key
    k_log = pkg.wow_game_log("K", n=5)
    assert k_log == [7.0]


# ─────────────────────────────────────────────────────────────────────────────
# 11. GOAT pitch metrics absent when tier is not GOAT
# ─────────────────────────────────────────────────────────────────────────────

def test_goat_metrics_absent_when_tier_not_goat():
    from gate_engine.balldontlie.normalizer import normalize_mlb_pitching_row
    from gate_engine.balldontlie.types import BDLTier

    raw = _make_mlb_pitching_row(outs=18)
    row = normalize_mlb_pitching_row(raw, bdl_tier=BDLTier.FREE)
    # GOAT fields should be None when not in raw row
    assert row.avg_velocity is None
    assert row.zone_rate is None
    assert row.whiff_rate is None
    assert row.pitch_mix is None


def test_goat_metrics_present_when_supplied():
    from gate_engine.balldontlie.normalizer import normalize_mlb_pitching_row
    from gate_engine.balldontlie.types import BDLTier

    raw = _make_mlb_pitching_row(outs=18)
    raw["avg_velocity"] = 95.2
    raw["zone_rate"]    = 0.47
    raw["whiff_rate"]   = 0.32
    raw["pitch_mix"]    = {"FF": 0.55, "SL": 0.30, "CH": 0.15}
    row = normalize_mlb_pitching_row(raw, bdl_tier=BDLTier.GOAT)
    assert row.avg_velocity == pytest.approx(95.2)
    assert row.zone_rate    == pytest.approx(0.47)
    assert row.whiff_rate   == pytest.approx(0.32)
    assert row.pitch_mix == {"FF": 0.55, "SL": 0.30, "CH": 0.15}


# ─────────────────────────────────────────────────────────────────────────────
# 12. BDL lineups cannot override official contradiction
# ─────────────────────────────────────────────────────────────────────────────

def test_bdl_lineup_cannot_override_official():
    from gate_engine.balldontlie.reconciliation import reconcile_lineup

    bdl_lineup      = [{"player_id": "A", "order": 1}]
    official_lineup = [{"player_id": "B", "order": 1, "source": "mlb_official"}]

    result, status, notes = reconcile_lineup(
        bdl_lineup      = bdl_lineup,
        official_lineup = official_lineup,
        official_source = "official_feed",
    )
    assert status == "OFFICIAL_CONTROLS"
    assert result == official_lineup   # BDL lineup discarded
    assert any("official" in n for n in notes)


def test_bdl_lineup_used_when_no_official():
    from gate_engine.balldontlie.reconciliation import reconcile_lineup

    bdl_lineup = [{"player_id": "A", "order": 1}]
    result, status, notes = reconcile_lineup(
        bdl_lineup=bdl_lineup, official_lineup=None, official_source=None
    )
    assert status == "RETRIEVED"
    assert result == bdl_lineup
    assert any("must_reconcile" in n for n in notes)


# ─────────────────────────────────────────────────────────────────────────────
# 13. Odds not double-counted (corroborated match)
# ─────────────────────────────────────────────────────────────────────────────

def test_odds_not_double_counted_corroborated():
    from gate_engine.balldontlie.anti_double_count import deduplicate_odds

    existing = [{"name": "FanDuel", "side": "home", "odds": -145}]
    bdl_obs  = [{"name": "FanDuel", "side": "home", "odds": -145}]  # same

    merged, notes = deduplicate_odds(existing, bdl_obs)
    assert len(merged) == 1   # not added again
    assert any("CORROBORATED" in n for n in notes)


def test_odds_new_book_added():
    from gate_engine.balldontlie.anti_double_count import deduplicate_odds

    existing = [{"name": "FanDuel", "side": "home", "odds": -145}]
    bdl_obs  = [{"name": "DraftKings", "side": "home", "odds": -142}]

    merged, notes = deduplicate_odds(existing, bdl_obs)
    assert len(merged) == 2   # new book added
    assert any("added" in n for n in notes)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Odds SOURCE_CONFLICT when same book has different price
# ─────────────────────────────────────────────────────────────────────────────

def test_odds_source_conflict_different_price():
    from gate_engine.balldontlie.anti_double_count import deduplicate_odds

    existing = [{"name": "FanDuel", "side": "home", "odds": -145}]
    bdl_obs  = [{"name": "FanDuel", "side": "home", "odds": -160}]  # different

    merged, notes = deduplicate_odds(existing, bdl_obs)
    assert any("SOURCE_CONFLICT" in n for n in notes)
    # Existing entry should be marked as conflicted
    assert merged[0].get("bdl_conflict") is True


# ─────────────────────────────────────────────────────────────────────────────
# 15. Game-log reconciliation: corroboration within threshold
# ─────────────────────────────────────────────────────────────────────────────

def test_game_log_reconciliation_corroborated():
    from gate_engine.balldontlie.reconciliation import reconcile_game_log

    existing = [20.0, 18.0, 22.0]
    bdl_vals = [20.5, 18.0, 21.5]   # within 15% threshold

    winner, status, notes = reconcile_game_log(
        bdl_values=bdl_vals,
        existing_values=existing,
        existing_source="statmuse",
        stat_key="PTS",
    )
    assert status == "CORROBORATED"
    assert winner == existing   # existing preserved on corroboration


# ─────────────────────────────────────────────────────────────────────────────
# 16. Game-log reconciliation: SOURCE_CONFLICT when materially different
# ─────────────────────────────────────────────────────────────────────────────

def test_game_log_reconciliation_source_conflict():
    from gate_engine.balldontlie.reconciliation import reconcile_game_log

    existing = [10.0, 10.0, 10.0]
    bdl_vals = [20.0, 20.0, 20.0]   # 100% difference → conflict

    winner, status, notes = reconcile_game_log(
        bdl_values=bdl_vals,
        existing_values=existing,
        existing_source="espn_blurb",
        stat_key="PTS",
    )
    assert status == "SOURCE_CONFLICT"
    assert any("SOURCE_CONFLICT" in n for n in notes)


# ─────────────────────────────────────────────────────────────────────────────
# 17. Reconciliation: higher-precedence source wins over BDL
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_higher_precedence_wins():
    from gate_engine.balldontlie.reconciliation import reconcile_value
    from gate_engine.balldontlie.types import BDLProvenance, BDLStatus

    prov = BDLProvenance()
    winner, status, _ = reconcile_value(
        bdl_value       = 20.0,
        existing_value  = 35.0,    # very different
        existing_source = "official_gamelog",  # precedence 10 > BDL 7
        field_name      = "pts",
        provenance      = prov,
    )
    assert winner == 35.0   # official wins
    assert status == "SOURCE_CONFLICT"


# ─────────────────────────────────────────────────────────────────────────────
# 18. Reconciliation: BDL wins over lower-precedence source
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_bdl_wins_over_lower_precedence():
    from gate_engine.balldontlie.reconciliation import reconcile_value
    from gate_engine.balldontlie.types import BDLProvenance

    prov = BDLProvenance()
    winner, status, _ = reconcile_value(
        bdl_value       = 20.0,
        existing_value  = 35.0,   # very different
        existing_source = "espn_blurb",  # precedence 3 < BDL 7
        field_name      = "pts",
        provenance      = prov,
    )
    assert winner == 20.0   # BDL wins
    assert status == "SOURCE_CONFLICT"


# ─────────────────────────────────────────────────────────────────────────────
# 19. source_grade is "A-" in normalizer provenance
# ─────────────────────────────────────────────────────────────────────────────

def test_source_grade_a_minus_in_provenance():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLTier, BDL_SOURCE_GRADE

    raw = _make_nba_row(pts=20.0)
    row = normalize_nba_wnba_row(raw, sport="NBA", bdl_tier=BDLTier.FREE)
    assert row.provenance.source_grade == "A-"
    assert BDL_SOURCE_GRADE == "A-"


# ─────────────────────────────────────────────────────────────────────────────
# 20. BallDontLieAdapter.source_grade == "A-"
# ─────────────────────────────────────────────────────────────────────────────

def test_opportunity_adapter_source_grade():
    from gate_engine.opportunity_acquisition.adapters import BallDontLieAdapter
    adapter = BallDontLieAdapter()
    assert adapter.source_grade == "A-"


# ─────────────────────────────────────────────────────────────────────────────
# 21. source_grade.py has balldontlie_api mapped to "A-"
# ─────────────────────────────────────────────────────────────────────────────

def test_source_grade_py_balldontlie_mapping():
    from gate_engine.source_grade import SOURCE_TYPE_GRADES
    assert SOURCE_TYPE_GRADES.get("balldontlie_api") == "A-"
    assert SOURCE_TYPE_GRADES.get("balldontlie") == "A-"


# ─────────────────────────────────────────────────────────────────────────────
# 22. can_execute=False across all balldontlie submodules
# ─────────────────────────────────────────────────────────────────────────────

def test_can_execute_false_all_submodules():
    from gate_engine.balldontlie import types, client, normalizer, nba_wnba, mlb
    from gate_engine.balldontlie import reconciliation, anti_double_count

    for mod in (types, client, normalizer, nba_wnba, mlb, reconciliation, anti_double_count):
        assert hasattr(mod, "can_execute"), f"{mod.__name__} missing can_execute"
        assert mod.can_execute is False, \
            f"{mod.__name__}.can_execute must be False, got {mod.can_execute}"


# ─────────────────────────────────────────────────────────────────────────────
# 23. NBA combo stat PTS+REB+AST computed correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_nba_combo_stat_pra():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLPlayerPackage, BDLTier

    raw = _make_nba_row(pts=20.0, reb=8.0, ast=5.0, min_=35.0)
    row = normalize_nba_wnba_row(raw, sport="NBA", bdl_tier=BDLTier.FREE)
    pkg = BDLPlayerPackage(game_rows=[row], sport="NBA")

    gl = pkg.wow_game_log("PTS+REB+AST", n=5)
    assert gl == [33.0]   # 20 + 8 + 5


def test_nba_combo_stat_pts_reb():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLPlayerPackage, BDLTier

    raw = _make_nba_row(pts=25.0, reb=10.0, ast=3.0)
    row = normalize_nba_wnba_row(raw, sport="NBA", bdl_tier=BDLTier.FREE)
    pkg = BDLPlayerPackage(game_rows=[row], sport="NBA")

    gl = pkg.wow_game_log("PTS+REB", n=5)
    assert gl == [35.0]   # 25 + 10


# ─────────────────────────────────────────────────────────────────────────────
# 24. minutes_stats() correct mean/variance/cv
# ─────────────────────────────────────────────────────────────────────────────

def test_minutes_stats_correct():
    from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row
    from gate_engine.balldontlie.types import BDLPlayerPackage, BDLTier

    # Three games at 30, 32, 34 minutes → mean=32, variance=2.67, cv=0.051
    rows = [
        normalize_nba_wnba_row(_make_nba_row(min_=30.0, date="2026-07-01"), "WNBA", BDLTier.FREE),
        normalize_nba_wnba_row(_make_nba_row(min_=32.0, date="2026-07-02"), "WNBA", BDLTier.FREE),
        normalize_nba_wnba_row(_make_nba_row(min_=34.0, date="2026-07-03"), "WNBA", BDLTier.FREE),
    ]
    pkg = BDLPlayerPackage(game_rows=rows, sport="WNBA")
    stats = pkg.minutes_stats()

    assert stats["n"] == 3
    assert stats["mean"] == pytest.approx(32.0)
    assert stats["variance"] == pytest.approx(8.0 / 3.0, rel=0.01)
    assert stats["cv"] is not None
    assert stats["role_stability"] is not None


def test_minutes_stats_empty_returns_none_fields():
    from gate_engine.balldontlie.types import BDLPlayerPackage

    pkg = BDLPlayerPackage(sport="WNBA")  # no game rows
    stats = pkg.minutes_stats()
    assert stats["n"] == 0
    assert stats["mean"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 25. fetch_bdl_player_package routes MLB pitcher vs batter by stat_key
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_bdl_player_package_routes_pitcher():
    """IP stat key must route to pitcher package, not batter."""
    from gate_engine.balldontlie.types import BDLStatus

    mock_pitcher_pkg = MagicMock()
    mock_pitcher_pkg.wow_game_log.return_value   = [6.0]
    mock_pitcher_pkg.wow_box_score_log.return_value = [{}]
    mock_pitcher_pkg.minutes_stats.return_value  = {"mean": None}
    mock_pitcher_pkg.acquisition_status = BDLStatus.OK
    mock_pitcher_pkg.provenance.to_dict.return_value = {}
    mock_pitcher_pkg.notes = []

    with patch("gate_engine.balldontlie.mlb.fetch_pitcher_package",
               return_value=mock_pitcher_pkg) as mock_p, \
         patch("gate_engine.balldontlie.mlb.fetch_batter_package") as mock_b, \
         patch("gate_engine.balldontlie.client.credentials_available", return_value=True):
        from gate_engine.auto_game_log import fetch_bdl_player_package
        result = fetch_bdl_player_package("500", "MLB", stat_key="IP", season=2026)
        mock_p.assert_called_once()
        mock_b.assert_not_called()
        assert result["source_grade"] == "A-"


def test_fetch_bdl_player_package_routes_batter():
    """HITS stat key must route to batter package, not pitcher."""
    from gate_engine.balldontlie.types import BDLStatus

    mock_batter_pkg = MagicMock()
    mock_batter_pkg.wow_game_log.return_value   = [2.0]
    mock_batter_pkg.wow_box_score_log.return_value = [{}]
    mock_batter_pkg.minutes_stats.return_value  = {"mean": None}
    mock_batter_pkg.acquisition_status = BDLStatus.OK
    mock_batter_pkg.provenance.to_dict.return_value = {}
    mock_batter_pkg.notes = []

    with patch("gate_engine.balldontlie.mlb.fetch_batter_package",
               return_value=mock_batter_pkg) as mock_b, \
         patch("gate_engine.balldontlie.mlb.fetch_pitcher_package") as mock_p, \
         patch("gate_engine.balldontlie.client.credentials_available", return_value=True):
        from gate_engine.auto_game_log import fetch_bdl_player_package
        result = fetch_bdl_player_package("100", "MLB", stat_key="HITS", season=2026)
        mock_b.assert_called_once()
        mock_p.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 26. Empty BDL response → NO_DATA; enrichment falls back to existing
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_bdl_response_enrichment_fallback():
    from gate_engine.balldontlie.reconciliation import reconcile_enrichment_game_log
    from gate_engine.balldontlie.types import BDLProvenance

    existing_enrichment = {
        "game_log": [15.0, 18.0, 20.0],
        "game_log_source": "statmuse",
    }
    prov = BDLProvenance()

    # BDL returns empty game log
    updated = reconcile_enrichment_game_log(
        enrichment    = existing_enrichment,
        bdl_values    = [],      # empty
        stat_key      = "PTS",
        bdl_provenance = prov,
    )
    # Existing game_log must be preserved
    assert updated["game_log"] == [15.0, 18.0, 20.0]


# ─────────────────────────────────────────────────────────────────────────────
# 27. Patch ID registered in wow_runtime_manifest
# ─────────────────────────────────────────────────────────────────────────────

def test_patch_id_registered_in_manifest():
    from gate_engine.wow_runtime_manifest import WOW_RUNTIME_MANIFEST
    patch_ids = WOW_RUNTIME_MANIFEST.get("active_patch_ids", [])
    assert "WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS" in patch_ids
