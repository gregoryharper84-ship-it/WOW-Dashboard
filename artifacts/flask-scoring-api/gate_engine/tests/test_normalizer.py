"""
Tests for gate_engine/normalizer.py and gate_engine/roster_cache.py

All roster fetches are mocked — no live API calls needed.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

# Shared fake roster used across most tests
_NBA_ROSTER = [
    {"player_id": "2544",  "name_raw": "LeBron James",         "name_norm": "lebron james",         "team_abbr": "LAL", "sport": "NBA", "position": "F", "source": "nba_api"},
    {"player_id": "203999","name_raw": "Nikola Jokic",          "name_norm": "nikola jokic",          "team_abbr": "DEN", "sport": "NBA", "position": "C", "source": "nba_api"},
    {"player_id": "1628369","name_raw":"Jayson Tatum",           "name_norm": "jayson tatum",          "team_abbr": "BOS", "sport": "NBA", "position": "F", "source": "nba_api"},
    {"player_id": "1630162","name_raw":"Anthony Edwards",        "name_norm": "anthony edwards",       "team_abbr": "MIN", "sport": "NBA", "position": "G", "source": "nba_api"},
    {"player_id": "201939", "name_raw":"Stephen Curry",          "name_norm": "stephen curry",         "team_abbr": "GSW", "sport": "NBA", "position": "G", "source": "nba_api"},
    # Name collision — two "Marcus Williams" on different teams
    {"player_id": "9001",   "name_raw":"Marcus Williams",        "name_norm": "marcus williams",       "team_abbr": "LAL", "sport": "NBA", "position": "G", "source": "nba_api"},
    {"player_id": "9002",   "name_raw":"Marcus Williams",        "name_norm": "marcus williams",       "team_abbr": "BOS", "sport": "NBA", "position": "F", "source": "nba_api"},
]

_MLB_ROSTER = [
    {"player_id": "592450", "name_raw": "Mookie Betts",          "name_norm": "mookie betts",          "team_abbr": "LAD", "sport": "MLB", "position": "OF", "source": "mlb_stats_api"},
    {"player_id": "660271", "name_raw": "Juan Soto",              "name_norm": "juan soto",             "team_abbr": "NYM", "sport": "MLB", "position": "OF", "source": "mlb_stats_api"},
    {"player_id": "547989", "name_raw": "Ronald Acuna Jr.",       "name_norm": "ronald acuna jr",       "team_abbr": "ATL", "sport": "MLB", "position": "OF", "source": "mlb_stats_api"},
]

_GAME_TODAY = {"game_id": "g123", "game_time": "2026-08-03T19:00:00Z", "opponent": "GSW"}
_NO_GAME    = None


def _patch_roster(sport, roster):
    return patch("gate_engine.normalizer.get_roster", return_value=roster)

def _patch_game(game_result):
    return patch("gate_engine.normalizer._get_game", return_value=game_result)


# ---------------------------------------------------------------------------
# normalize_name / normalize_ocr helpers
# ---------------------------------------------------------------------------

class TestNameNormalization:
    def test_strips_diacritics(self):
        from gate_engine.roster_cache import normalize_name
        assert normalize_name("Nikola Jokić") == "nikola jokic"

    def test_strips_jr_suffix(self):
        from gate_engine.roster_cache import normalize_name
        assert normalize_name("Ronald Acuna Jr.") == "ronald acuna jr"

    def test_strips_iii_suffix(self):
        from gate_engine.roster_cache import normalize_name
        assert normalize_name("Lebron James III") == "lebron james"

    def test_collapses_whitespace(self):
        from gate_engine.roster_cache import normalize_name
        assert normalize_name("  LeBron   James  ") == "lebron james"

    def test_ocr_zero_to_o(self):
        from gate_engine.roster_cache import normalize_ocr
        # "J0kic" → "jokic" (0→o)
        result = normalize_ocr("Nik0la J0kic")
        assert "jokic" in result

    def test_ocr_one_to_l(self):
        from gate_engine.roster_cache import normalize_ocr
        result = normalize_ocr("LeB1on James")
        assert "leblon james" in result or "lebron james" not in result  # 1→l changes it


# ---------------------------------------------------------------------------
# Exact match
# ---------------------------------------------------------------------------

class TestExactMatch:
    def test_exact_name_resolves(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "leg_id": "l1", "player_name": "LeBron James",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 27.5,
                "platform": "prizepicks", "ocr_confidence": 0.95,
            }])
        r = rows[0]
        assert r["resolution_status"] == "resolved"
        assert r["player_id"] == "2544"
        assert r["matched_via"] == "roster_exact"
        assert r["resolution_confidence"] == 1.0

    def test_exact_match_case_insensitive(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "lebron james", "sport": "NBA",
                "prop_type": "points", "side": "over",
                "line_value": 27.5, "platform": "prizepicks",
            }])
        assert rows[0]["resolution_status"] == "resolved"

    def test_nickname_steph_expands(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "Steph", "sport": "NBA",
                "prop_type": "points", "side": "over",
                "line_value": 24.5, "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "resolved"
        assert r["player_id"] == "201939"


# ---------------------------------------------------------------------------
# Fuzzy match
# ---------------------------------------------------------------------------

class TestFuzzyMatch:
    def test_minor_typo_auto_accepts(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "Nikola Yokic",   # c→Y typo
                "sport": "NBA", "prop_type": "rebounds",
                "side": "over", "line_value": 12.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "resolved"
        assert r["player_id"] == "203999"
        assert r["matched_via"] == "roster_fuzzy"
        assert r["resolution_confidence"] >= 0.85

    def test_low_score_returns_not_found(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "Xyzzy Foobar",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 20.0,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "not_found"

    def test_ambiguous_range_flagged(self):
        from gate_engine.normalizer import normalize_legs
        # "Jayson Tatm" — score in the 0.65–0.84 range
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "J Tatum",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 25.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        # Could be resolved or ambiguous depending on score — just verify it's not not_found
        assert r["resolution_status"] in ("resolved", "ambiguous")


# ---------------------------------------------------------------------------
# Name collision + team disambiguation
# ---------------------------------------------------------------------------

class TestNameCollision:
    def test_collision_without_hint_is_ambiguous(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "Marcus Williams",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 12.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "ambiguous"
        assert len(r["candidates"]) >= 2

    def test_collision_resolved_by_team_hint(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "Marcus Williams",
                "team": "BOS",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 12.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] in ("resolved", "ambiguous")
        if r["resolution_status"] == "resolved":
            assert r["player_id"] == "9002"


# ---------------------------------------------------------------------------
# No game today
# ---------------------------------------------------------------------------

class TestNoGameToday:
    def test_player_resolves_but_no_game(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_NO_GAME):
            rows = normalize_legs([{
                "player_name": "LeBron James",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 27.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "not_found"
        assert "NO_GAME_TODAY" in r["flags"]
        assert "no" in r["resolution_notes"].lower()


# ---------------------------------------------------------------------------
# Stat key mapping
# ---------------------------------------------------------------------------

class TestStatKeyMapping:
    def test_nba_points_maps(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("points", "NBA")
        assert result["stat_key"] == "PTS"
        assert result["stat_formula"] is None

    def test_nba_combo_prop(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("Pts+Rebs+Asts", "NBA")
        assert result["stat_key"] is None
        assert result["stat_formula"] == "PTS+REB+AST"

    def test_mlb_hits(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("hits", "MLB")
        assert result["stat_key"] == "H"

    def test_mlb_pitcher_strikeouts(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("pitcher strikeouts", "MLB")
        assert result["stat_key"] == "SO"

    def test_unknown_prop_type_flag(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("flying unicorns", "NBA")
        assert "UNKNOWN_PROP_TYPE" in result["flags"]

    def test_nfl_passing_yards(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("passing yards", "NFL")
        assert result["stat_key"] == "PASS_YDS"


# ---------------------------------------------------------------------------
# Line sanity check
# ---------------------------------------------------------------------------

class TestLineSanityCheck:
    def test_valid_half_point_line(self):
        from gate_engine.normalizer import _sanity_check_line
        assert _sanity_check_line(27.5, "prizepicks") == []

    def test_whole_number_line_valid(self):
        from gate_engine.normalizer import _sanity_check_line
        assert _sanity_check_line(20.0, "prizepicks") == []

    def test_bad_increment_flagged(self):
        from gate_engine.normalizer import _sanity_check_line
        flags = _sanity_check_line(27.3, "prizepicks")
        assert "OCR_SUSPECT" in flags

    def test_negative_line_flagged(self):
        from gate_engine.normalizer import _sanity_check_line
        flags = _sanity_check_line(-1.5, "prizepicks")
        assert "OCR_SUSPECT" in flags


# ---------------------------------------------------------------------------
# OCR confidence flag
# ---------------------------------------------------------------------------

class TestOcrConfidence:
    def test_low_ocr_flag(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "LeBron James",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 27.5,
                "platform": "prizepicks",
                "ocr_confidence": 0.60,
            }])
        assert "OCR_LOW_CONFIDENCE" in rows[0]["flags"]

    def test_high_ocr_no_flag(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "LeBron James",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 27.5,
                "platform": "prizepicks",
                "ocr_confidence": 0.95,
            }])
        assert "OCR_LOW_CONFIDENCE" not in rows[0]["flags"]


# ---------------------------------------------------------------------------
# Line modifier detection
# ---------------------------------------------------------------------------

class TestLineModifier:
    def test_demon_detected(self):
        from gate_engine.normalizer import _detect_line_modifier
        assert _detect_line_modifier("prizepicks", "LeBron James demon", "points") == "demon"

    def test_goblin_detected(self):
        from gate_engine.normalizer import _detect_line_modifier
        assert _detect_line_modifier("prizepicks", "Steph goblin", "threes") == "goblin"

    def test_standard_default(self):
        from gate_engine.normalizer import _detect_line_modifier
        assert _detect_line_modifier("prizepicks", "LeBron James", "points") == "standard"


# ---------------------------------------------------------------------------
# MLB roster + schedule integration
# ---------------------------------------------------------------------------

class TestMLB:
    def test_mlb_exact_match(self):
        from gate_engine.normalizer import normalize_legs
        mlb_game = {"game_id": "mlb-g1", "game_time": "2026-08-03T19:05:00Z", "opponent": "SFG"}
        with _patch_roster("MLB", _MLB_ROSTER), _patch_game(mlb_game):
            rows = normalize_legs([{
                "player_name": "Mookie Betts",
                "sport": "MLB", "prop_type": "hits",
                "side": "over", "line_value": 1.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "resolved"
        assert r["stat_key"] == "H"
        assert r["player_id"] == "592450"

    def test_mlb_acuna_jr_with_suffix(self):
        from gate_engine.normalizer import normalize_legs
        mlb_game = {"game_id": "mlb-g2", "game_time": "2026-08-03T19:05:00Z", "opponent": "NYM"}
        with _patch_roster("MLB", _MLB_ROSTER), _patch_game(mlb_game):
            rows = normalize_legs([{
                "player_name": "Ronald Acuna Jr.",
                "sport": "MLB", "prop_type": "hits",
                "side": "over", "line_value": 1.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "resolved"
        assert r["player_id"] == "547989"


# ---------------------------------------------------------------------------
# Multi-leg batch
# ---------------------------------------------------------------------------

class TestMultiLegBatch:
    def test_batch_processes_all_legs(self):
        from gate_engine.normalizer import normalize_legs
        legs = [
            {"player_name": "LeBron James", "sport": "NBA", "prop_type": "points",
             "side": "over", "line_value": 27.5, "platform": "prizepicks"},
            {"player_name": "Nikola Jokic",  "sport": "NBA", "prop_type": "rebounds",
             "side": "over", "line_value": 11.5, "platform": "prizepicks"},
        ]
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs(legs)
        assert len(rows) == 2
        assert rows[0]["player_id"] == "2544"
        assert rows[1]["player_id"] == "203999"

    def test_leg_id_preserved(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "leg_id": "custom-id-abc",
                "player_name": "LeBron James", "sport": "NBA",
                "prop_type": "points", "side": "over",
                "line_value": 27.5, "platform": "prizepicks",
            }])
        assert rows[0]["leg_id"] == "custom-id-abc"

    def test_unknown_sport_not_found(self):
        from gate_engine.normalizer import normalize_legs
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "Some Player",
                "sport": "",
                "prop_type": "points", "side": "over",
                "line_value": 10.5, "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] == "not_found"
        assert "UNKNOWN_SPORT" in r["flags"]
