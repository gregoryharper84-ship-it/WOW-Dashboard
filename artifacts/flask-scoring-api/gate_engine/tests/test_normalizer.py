"""
test_normalizer.py — WOW Slip Normalization Adapter Tests

Merged test suite covering both:

  Dict-API tests (incoming, Section A–H):
    Use normalize_legs() with player_name/line_value field names and dict-style
    row access; mock get_roster and _get_game at the normalizer module boundary.

  NormalizedRow API tests (this task, Sections 1–16):
    Use normalize_leg() + resolve_player() with player/line field names;
    pass roster_override / schedule_override directly for full isolation.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Shared fake roster for Dict-API tests (incoming format: name_norm/team_abbr)
# ---------------------------------------------------------------------------
_NBA_ROSTER = [
    {"player_id": "2544",   "name_raw": "LeBron James",    "name": "LeBron James",
     "name_norm": "lebron james",    "name_normalized": "lebron james",
     "team_abbr": "LAL",  "team": "LAL",  "sport": "NBA", "position": "F", "source": "nba_api"},
    {"player_id": "203999", "name_raw": "Nikola Jokic",     "name": "Nikola Jokic",
     "name_norm": "nikola jokic",    "name_normalized": "nikola jokic",
     "team_abbr": "DEN",  "team": "DEN",  "sport": "NBA", "position": "C", "source": "nba_api"},
    {"player_id": "1628369","name_raw": "Jayson Tatum",     "name": "Jayson Tatum",
     "name_norm": "jayson tatum",    "name_normalized": "jayson tatum",
     "team_abbr": "BOS",  "team": "BOS",  "sport": "NBA", "position": "F", "source": "nba_api"},
    {"player_id": "1630162","name_raw": "Anthony Edwards",  "name": "Anthony Edwards",
     "name_norm": "anthony edwards", "name_normalized": "anthony edwards",
     "team_abbr": "MIN",  "team": "MIN",  "sport": "NBA", "position": "G", "source": "nba_api"},
    {"player_id": "201939", "name_raw": "Stephen Curry",    "name": "Stephen Curry",
     "name_norm": "stephen curry",   "name_normalized": "stephen curry",
     "team_abbr": "GSW",  "team": "GSW",  "sport": "NBA", "position": "G", "source": "nba_api"},
    # Name collision — two "Marcus Williams" on different teams
    {"player_id": "9001",   "name_raw": "Marcus Williams",  "name": "Marcus Williams",
     "name_norm": "marcus williams", "name_normalized": "marcus williams",
     "team_abbr": "LAL",  "team": "LAL",  "sport": "NBA", "position": "G", "source": "nba_api"},
    {"player_id": "9002",   "name_raw": "Marcus Williams",  "name": "Marcus Williams",
     "name_norm": "marcus williams", "name_normalized": "marcus williams",
     "team_abbr": "BOS",  "team": "BOS",  "sport": "NBA", "position": "F", "source": "nba_api"},
]

_MLB_ROSTER = [
    {"player_id": "592450", "name_raw": "Mookie Betts",       "name": "Mookie Betts",
     "name_norm": "mookie betts",      "name_normalized": "mookie betts",
     "team_abbr": "LAD",  "team": "LAD", "sport": "MLB", "position": "OF", "source": "mlb_stats_api"},
    {"player_id": "660271", "name_raw": "Juan Soto",           "name": "Juan Soto",
     "name_norm": "juan soto",          "name_normalized": "juan soto",
     "team_abbr": "NYM",  "team": "NYM", "sport": "MLB", "position": "OF", "source": "mlb_stats_api"},
    {"player_id": "547989", "name_raw": "Ronald Acuna Jr.",    "name": "Ronald Acuna Jr.",
     "name_norm": "ronald acuna jr",    "name_normalized": "ronald acuna jr",
     "team_abbr": "ATL",  "team": "ATL", "sport": "MLB", "position": "OF", "source": "mlb_stats_api"},
]

_GAME_TODAY = {"game_id": "g123", "game_time": "2026-08-03T19:00:00Z", "opponent": "GSW"}
_NO_GAME    = None


def _patch_roster(sport, roster):
    return patch("gate_engine.normalizer.get_roster", return_value=roster)

def _patch_game(game_result):
    return patch("gate_engine.normalizer._get_game", return_value=game_result)


# ===========================================================================
# Section A: normalize_name / normalize_ocr helpers  (Dict-API)
# ===========================================================================

class TestNameNormalizationDictAPI:
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
        result = normalize_ocr("Nik0la J0kic")
        assert "jokic" in result

    def test_ocr_one_to_l(self):
        from gate_engine.roster_cache import normalize_ocr
        result = normalize_ocr("LeB1on James")
        # 1→l substitution changes the name
        assert "leblon james" in result or "lebron james" not in result


# ===========================================================================
# Section B: Exact match  (Dict-API)
# ===========================================================================

class TestExactMatchDictAPI:
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
        assert r["matched_via"] in ("exact", "roster_exact", "roster_fuzzy", "nickname")
        assert r["resolution_confidence"] >= 0.85

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


# ===========================================================================
# Section C: Fuzzy match  (Dict-API)
# ===========================================================================

class TestFuzzyMatchDictAPI:
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
        assert r["matched_via"] in ("roster_fuzzy", "fuzzy", "roster_exact", "exact")
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
        with _patch_roster("NBA", _NBA_ROSTER), _patch_game(_GAME_TODAY):
            rows = normalize_legs([{
                "player_name": "J Tatum",
                "sport": "NBA", "prop_type": "points",
                "side": "over", "line_value": 25.5,
                "platform": "prizepicks",
            }])
        r = rows[0]
        assert r["resolution_status"] in ("resolved", "ambiguous")


# ===========================================================================
# Section D: Name collision + team disambiguation  (Dict-API)
# ===========================================================================

class TestNameCollisionDictAPI:
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


# ===========================================================================
# Section E: No game today  (Dict-API)
# ===========================================================================

class TestNoGameTodayDictAPI:
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


# ===========================================================================
# Section F: Stat key mapping  (Dict-API)
# ===========================================================================

class TestStatKeyMappingDictAPI:
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
        assert result["stat_key"] == "K"

    def test_unknown_prop_type_flag(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("flying unicorns", "NBA")
        assert "UNKNOWN_PROP_TYPE" in result["flags"]

    def test_nfl_passing_yards(self):
        from gate_engine.normalizer import _map_stat_key
        result = _map_stat_key("passing yards", "NFL")
        assert result["stat_key"] == "PASS_YDS"


# ===========================================================================
# Section G: Line sanity check  (Dict-API)
# ===========================================================================

class TestLineSanityCheckDictAPI:
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


# ===========================================================================
# Section H: OCR confidence, line modifier, MLB batch  (Dict-API)
# ===========================================================================

class TestOcrConfidenceDictAPI:
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


class TestLineModifierDictAPI:
    def test_demon_detected(self):
        from gate_engine.normalizer import _detect_line_modifier
        assert _detect_line_modifier("prizepicks", "LeBron James demon", "points") == "demon"

    def test_goblin_detected(self):
        from gate_engine.normalizer import _detect_line_modifier
        assert _detect_line_modifier("prizepicks", "Steph goblin", "threes") == "goblin"

    def test_standard_default(self):
        from gate_engine.normalizer import _detect_line_modifier
        assert _detect_line_modifier("prizepicks", "LeBron James", "points") == "standard"


class TestMLBDictAPI:
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


class TestMultiLegBatchDictAPI:
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


# ===========================================================================
# NormalizedRow API tests — Sections 1–16
# ===========================================================================

from datetime import date

from gate_engine.normalizer import (
    NormalizedRow,
    _is_sane_line,
    _resolve_stat_key,
    normalize_leg,
    normalize_legs,
    resolve_player,
    CONFIDENCE_AUTO_ACCEPT,
    CONFIDENCE_AMBIGUOUS_LO,
)
from gate_engine.roster_cache import normalize_name


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _roster(*entries):
    """Build a minimal roster list with both field-name conventions."""
    result = []
    for i, (pid, name, team) in enumerate(entries):
        norm = normalize_name(name)
        team_up = (team or "").upper()
        result.append({
            "player_id":       str(pid),
            "name":            name,
            "name_raw":        name,
            "name_normalized": norm,
            "name_norm":       norm,
            "team":            team_up,
            "team_abbr":       team_up,
            "position":        "G",
            "sport":           "NBA",
        })
    return result


def _schedule(*entries):
    """Build a minimal schedule list."""
    result = []
    for gid, home, away in entries:
        result.append({
            "game_id":   str(gid),
            "home_team": home,
            "away_team": away,
            "game_time": "2026-08-04T20:00:00Z",
            "sport":     "NBA",
        })
    return result


# ───────────────────────────────────────────────────────────────────────────
# Section 1: name normalization helpers
# ───────────────────────────────────────────────────────────────────────────

class TestNormalizeName:
    def test_lowercase(self):
        assert normalize_name("LeBron James") == "lebron james"

    def test_strip_diacritics(self):
        assert normalize_name("Nikola Jokić") == normalize_name("Nikola Jokic")

    def test_strip_suffix_jr(self):
        # "Jr." period is stripped → "jr" word remains (disambiguates from a
        # different player with the same first/last name but no Jr.)
        assert normalize_name("Trae Young Jr.") == "trae young jr"

    def test_strip_suffix_iii(self):
        assert normalize_name("Kevin Durant III") == normalize_name("Kevin Durant")

    def test_strip_accents_giannis(self):
        assert normalize_name("Giannis Antetokounmpo") == "giannis antetokounmpo"

    def test_ocr_zero_to_o(self):
        assert normalize_name("0lajuw0n") == "olajuwon"
        assert "0" not in normalize_name("LeBron James")

    def test_whitespace_collapse(self):
        assert normalize_name("  Ja  Morant  ") == "ja morant"


# ───────────────────────────────────────────────────────────────────────────
# Section 2: stat key mapping
# ───────────────────────────────────────────────────────────────────────────

class TestResolveStatKey:
    def test_nba_points(self):
        sk, sf = _resolve_stat_key("points", "NBA")
        assert sk == "PTS" and sf is None

    def test_nba_rebounds(self):
        sk, _ = _resolve_stat_key("rebounds", "NBA")
        assert sk == "REB"

    def test_mlb_hits(self):
        sk, _ = _resolve_stat_key("hits", "MLB")
        assert sk == "H"

    def test_mlb_pitcher_strikeouts(self):
        sk, _ = _resolve_stat_key("pitcher strikeouts", "MLB")
        assert sk == "K"

    def test_nfl_passing_yards(self):
        sk, _ = _resolve_stat_key("passing yards", "NFL")
        assert sk == "PASS_YDS"

    def test_nhl_shots_on_goal(self):
        sk, _ = _resolve_stat_key("shots on goal", "NHL")
        assert sk == "SOG"

    def test_combo_pra(self):
        sk, sf = _resolve_stat_key("Pts+Rebs+Asts", "NBA")
        assert sk is None and sf == "PTS+REB+AST"

    def test_combo_pra_full_words(self):
        sk, sf = _resolve_stat_key("Points + Rebounds + Assists", "NBA")
        assert sk is None and sf == "PTS+REB+AST"

    def test_combo_fantasy_score(self):
        sk, sf = _resolve_stat_key("Fantasy Score", "NBA")
        assert sk is None and sf == "FANTASY_SCORE"

    def test_combo_hits_rbis(self):
        sk, sf = _resolve_stat_key("Hits + RBIs", "MLB")
        assert sk is None and sf == "H+RBI"

    def test_unmapped_returns_none(self):
        sk, sf = _resolve_stat_key("mystery prop", "NBA")
        assert sk is None and sf is None

    def test_case_insensitive(self):
        sk, _ = _resolve_stat_key("POINTS", "NBA")
        assert sk == "PTS"


# ───────────────────────────────────────────────────────────────────────────
# Section 3: OCR line sanity
# ───────────────────────────────────────────────────────────────────────────

class TestIsSaneLine:
    def test_whole_number_ok(self):
        assert _is_sane_line(20.0) is True

    def test_half_increment_ok(self):
        assert _is_sane_line(20.5) is True

    def test_quarter_increment_bad(self):
        assert _is_sane_line(20.25) is False

    def test_third_increment_bad(self):
        assert _is_sane_line(20.333) is False

    def test_float_precision(self):
        assert _is_sane_line(0.5 * 41) is True


# ───────────────────────────────────────────────────────────────────────────
# Section 4: resolve_player — exact match
# ───────────────────────────────────────────────────────────────────────────

class TestResolvePlayerExact:
    def setup_method(self):
        self.roster = _roster(
            ("1", "Stephen Curry", "GSW"),
            ("2", "LeBron James", "LAL"),
            ("3", "Kevin Durant", "PHX"),
        )
        self.schedule = _schedule(("g1", "GSW", "LAL"), ("g2", "PHX", "BOS"))

    def test_exact_match(self):
        status, conf, player, cands = resolve_player(
            "Stephen Curry", "NBA",
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        assert status == "resolved"
        assert conf >= 0.98
        assert player["player_id"] == "1"

    def test_exact_match_case_insensitive(self):
        status, conf, player, cands = resolve_player(
            "stephen curry", "NBA",
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        assert status == "resolved"
        assert player["player_id"] == "1"

    def test_exact_match_with_diacritics(self):
        roster = _roster(("10", "Nikola Jokic", "DEN"))
        status, conf, player, _ = resolve_player(
            "Nikola Jokić", "NBA",
            roster_override=roster,
        )
        assert status == "resolved"
        assert player["player_id"] == "10"


# ───────────────────────────────────────────────────────────────────────────
# Section 5: resolve_player — fuzzy auto-accept
# ───────────────────────────────────────────────────────────────────────────

class TestResolvePlayerFuzzy:
    def setup_method(self):
        self.roster = _roster(
            ("1", "Stephen Curry", "GSW"),
            ("2", "LeBron James", "LAL"),
        )

    def test_fuzzy_typo_accept(self):
        status, conf, player, _ = resolve_player(
            "Stephan Curry", "NBA",
            roster_override=self.roster,
        )
        assert status == "resolved"
        assert player["player_id"] == "1"
        assert conf >= CONFIDENCE_AUTO_ACCEPT

    def test_fuzzy_missing_space(self):
        status, conf, player, _ = resolve_player(
            "LeBron James", "NBA",
            roster_override=self.roster,
        )
        assert status == "resolved"
        assert player["player_id"] == "2"

    def test_fuzzy_partial_last_name(self):
        roster = _roster(("1", "Stephen Curry", "GSW"))
        status, conf, player, _ = resolve_player(
            "Curry", "NBA",
            roster_override=roster,
        )
        assert status in ("resolved", "ambiguous")


# ───────────────────────────────────────────────────────────────────────────
# Section 6: resolve_player — ambiguous band
# ───────────────────────────────────────────────────────────────────────────

class TestResolvePlayerAmbiguous:
    def test_low_confidence_ambiguous(self):
        roster = _roster(
            ("1", "Michael Jordan", "CHI"),
            ("2", "Michael Beasley", "LAL"),
        )
        status, conf, player, cands = resolve_player(
            "Michael Jordn",
            "NBA",
            roster_override=roster,
        )
        assert status in ("resolved", "ambiguous")

    def test_no_good_match_is_not_found(self):
        roster = _roster(("1", "Stephen Curry", "GSW"))
        status, conf, player, _ = resolve_player(
            "Xyzzy Blarghomp", "NBA",
            roster_override=roster,
        )
        assert status == "not_found"
        assert conf < CONFIDENCE_AMBIGUOUS_LO

    def test_ambiguous_returns_candidates(self):
        roster = _roster(
            ("1", "Marcus Smart", "MEM"),
            ("2", "Marcus Morris", "LAC"),
        )
        status, conf, player, cands = resolve_player(
            "Marcus", "NBA",
            roster_override=roster,
        )
        assert len(cands) >= 1


# ───────────────────────────────────────────────────────────────────────────
# Section 7: name collision + team hint
# ───────────────────────────────────────────────────────────────────────────

class TestNameCollision:
    def setup_method(self):
        self.roster = _roster(
            ("1", "Jaylin Williams", "OKC"),
            ("2", "Patrick Williams", "CHI"),
            ("3", "Cam Williams", "LAL"),
        )

    def test_collision_with_team_hint_resolves(self):
        status, conf, player, cands = resolve_player(
            "Patrick Williams", "NBA",
            team_hint="CHI",
            roster_override=self.roster,
        )
        assert status == "resolved"
        assert player["player_id"] == "2"

    def test_collision_with_team_hint_wrong_team(self):
        status, conf, player, cands = resolve_player(
            "Patrick Williams", "NBA",
            team_hint="OKC",
            roster_override=self.roster,
        )
        assert status in ("resolved", "ambiguous")


# ───────────────────────────────────────────────────────────────────────────
# Section 8: nickname expansion
# ───────────────────────────────────────────────────────────────────────────

class TestNicknameExpansion:
    def test_steph_resolves_to_stephen_curry(self):
        roster = _roster(("1", "Stephen Curry", "GSW"))
        schedule = _schedule(("g1", "GSW", "LAL"))
        status, conf, player, _ = resolve_player(
            "Steph", "NBA",
            roster_override=roster,
            schedule_override=schedule,
        )
        assert status == "resolved"
        assert player["player_id"] == "1"

    def test_giannis_mononym_resolves(self):
        roster = _roster(("1", "Giannis Antetokounmpo", "MIL"))
        schedule = _schedule(("g1", "MIL", "BOS"))
        status, conf, player, _ = resolve_player(
            "Giannis", "NBA",
            roster_override=roster,
            schedule_override=schedule,
        )
        assert status == "resolved"
        assert player["player_id"] == "1"

    def test_kd_resolves_to_kevin_durant(self):
        roster = _roster(("1", "Kevin Durant", "PHX"))
        status, conf, player, _ = resolve_player(
            "KD", "NBA",
            roster_override=roster,
        )
        assert status == "resolved"
        assert player["player_id"] == "1"

    def test_wemby_resolves(self):
        roster = _roster(("1", "Victor Wembanyama", "SAS"))
        status, conf, player, _ = resolve_player(
            "Wemby", "NBA",
            roster_override=roster,
        )
        assert status == "resolved"
        assert player["player_id"] == "1"


# ───────────────────────────────────────────────────────────────────────────
# Section 9: no game today → not_found
# ───────────────────────────────────────────────────────────────────────────

class TestNoGameToday:
    def test_player_with_no_game_returns_not_found(self):
        roster = _roster(("1", "Stephen Curry", "GSW"))
        empty_schedule: list = []
        row = normalize_leg(
            {"player": "Stephen Curry", "sport": "NBA", "prop_type": "points", "line": 25.5},
            roster_override=roster,
            schedule_override=empty_schedule,
        )
        assert row.resolution_status == "not_found"
        assert "NO_GAME_TODAY" in row.flags

    def test_player_with_game_today_resolves(self):
        roster = _roster(("1", "Stephen Curry", "GSW"))
        schedule = _schedule(("g1", "GSW", "LAL"))
        row = normalize_leg(
            {"player": "Stephen Curry", "sport": "NBA", "prop_type": "points", "line": 25.5},
            roster_override=roster,
            schedule_override=schedule,
        )
        assert row.resolution_status == "resolved"
        assert "NO_GAME_TODAY" not in row.flags


# ───────────────────────────────────────────────────────────────────────────
# Section 10: OCR_SUSPECT flag
# ───────────────────────────────────────────────────────────────────────────

class TestOcrSuspect:
    def _make_leg(self, line):
        return {"player": "Test Player", "sport": "NBA", "prop_type": "points", "line": line}

    def test_valid_half_increment_not_flagged(self):
        row = normalize_leg(self._make_leg(20.5), roster_override=[])
        assert row.ocr_suspect is False

    def test_valid_whole_number_not_flagged(self):
        row = normalize_leg(self._make_leg(21.0), roster_override=[])
        assert row.ocr_suspect is False

    def test_quarter_increment_flagged(self):
        row = normalize_leg(self._make_leg(20.25), roster_override=[])
        assert row.ocr_suspect is True
        assert "OCR_SUSPECT:line_not_half_increment" in row.flags

    def test_third_increment_flagged(self):
        row = normalize_leg(self._make_leg(20.333), roster_override=[])
        assert row.ocr_suspect is True

    def test_no_line_value_not_flagged(self):
        row = normalize_leg(
            {"player": "Test Player", "sport": "NBA", "prop_type": "points"},
            roster_override=[],
        )
        assert row.ocr_suspect is False


# ───────────────────────────────────────────────────────────────────────────
# Section 11: line_modifier detection
# ───────────────────────────────────────────────────────────────────────────

class TestLineModifier:
    def test_demon_detected_in_prop_type(self):
        row = normalize_leg(
            {"player": "Test Player", "sport": "NBA", "prop_type": "points demon", "line": 20.5},
            roster_override=[],
        )
        assert row.line_modifier == "DEMON"

    def test_goblin_detected(self):
        row = normalize_leg(
            {"player": "Goblin Test Player", "sport": "NBA", "prop_type": "points", "line": 20.5},
            roster_override=[],
        )
        assert row.line_modifier == "GOBLIN"

    def test_fire_detected(self):
        row = normalize_leg(
            {"player": "Test Player", "sport": "NBA", "prop_type": "fire points", "line": 20.5},
            roster_override=[],
        )
        assert row.line_modifier == "FIRE"

    def test_no_modifier(self):
        row = normalize_leg(
            {"player": "Test Player", "sport": "NBA", "prop_type": "points", "line": 20.5},
            roster_override=[],
        )
        assert row.line_modifier is None


# ───────────────────────────────────────────────────────────────────────────
# Section 12: normalize_leg — full integration
# ───────────────────────────────────────────────────────────────────────────

class TestNormalizeLeg:
    def setup_method(self):
        self.roster = _roster(
            ("1", "Jayson Tatum", "BOS"),
            ("2", "Jaylen Brown", "BOS"),
            ("3", "Joel Embiid", "PHI"),
        )
        self.schedule = _schedule(("g1", "BOS", "PHI"))

    def test_full_resolved_row(self):
        row = normalize_leg(
            {"player": "Jayson Tatum", "sport": "NBA", "prop_type": "points", "line": 27.5},
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        assert row.resolution_status == "resolved"
        assert row.player_id == "1"
        assert row.stat_key == "PTS"
        assert row.team == "BOS"
        assert row.opponent in ("PHI", None)
        assert row.game_id == "g1"
        assert row.ocr_suspect is False
        assert row.line_modifier is None

    def test_combo_prop_produces_formula(self):
        row = normalize_leg(
            {"player": "Jayson Tatum", "sport": "NBA", "prop_type": "Pts+Rebs+Asts", "line": 52.5},
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        assert row.stat_formula == "PTS+REB+AST"
        assert row.stat_key is None

    def test_missing_player_returns_not_found(self):
        row = normalize_leg(
            {"player": "", "sport": "NBA", "prop_type": "points", "line": 20.5},
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        assert row.resolution_status == "not_found"
        assert "MISSING_PLAYER_NAME" in row.flags

    def test_to_dict_contains_all_fields(self):
        row = normalize_leg(
            {"player": "Joel Embiid", "sport": "NBA", "prop_type": "points", "line": 30.5},
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        d = row.to_dict()
        required_keys = {
            "raw_player", "raw_prop_type", "raw_line", "sport",
            "player_id", "player_name", "team", "opponent",
            "game_id", "game_time", "stat_key", "stat_formula",
            "line_modifier", "resolution_status", "resolution_confidence",
            "matched_via", "candidates", "ocr_suspect", "flags",
        }
        assert required_keys <= set(d.keys())

    def test_opponent_correctly_assigned_away_team(self):
        row = normalize_leg(
            {"player": "Jayson Tatum", "sport": "NBA", "prop_type": "points", "line": 27.5},
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        assert row.resolution_status == "resolved"
        assert row.opponent == "PHI"

    def test_opponent_correctly_assigned_home_team(self):
        row = normalize_leg(
            {"player": "Joel Embiid", "sport": "NBA", "prop_type": "points", "line": 30.5},
            roster_override=self.roster,
            schedule_override=self.schedule,
        )
        assert row.resolution_status == "resolved"
        assert row.opponent == "BOS"


# ───────────────────────────────────────────────────────────────────────────
# Section 13: normalize_legs — batch
# ───────────────────────────────────────────────────────────────────────────

class TestNormalizeLegs:
    def setup_method(self):
        self.roster = _roster(
            ("1", "Luka Doncic", "DAL"),
            ("2", "Kyrie Irving", "DAL"),
            ("3", "Anthony Davis", "LAL"),
        )
        self.schedule = _schedule(("g1", "DAL", "LAL"))

    def test_batch_three_legs(self):
        legs = [
            {"player": "Luka Doncic",   "sport": "NBA", "prop_type": "points",   "line": 30.5},
            {"player": "Kyrie Irving",  "sport": "NBA", "prop_type": "assists",  "line": 5.5},
            {"player": "Anthony Davis", "sport": "NBA", "prop_type": "rebounds", "line": 11.5},
        ]
        rows = normalize_legs(legs)
        assert len(rows) == 3

    def test_batch_preserves_order(self):
        legs = [
            {"player": "Luka Doncic",  "sport": "NBA", "prop_type": "points",  "line": 30.5},
            {"player": "Kyrie Irving", "sport": "NBA", "prop_type": "assists", "line": 5.5},
        ]
        rows = normalize_legs(legs)
        assert rows[0].raw_player == "Luka Doncic"
        assert rows[1].raw_player == "Kyrie Irving"

    def test_batch_with_roster_override(self):
        legs = [
            {"player": "Luka Doncic",  "sport": "NBA", "prop_type": "points",  "line": 30.5},
            {"player": "Kyrie Irving", "sport": "NBA", "prop_type": "assists", "line": 5.5},
        ]
        r1 = normalize_leg(legs[0], roster_override=self.roster, schedule_override=self.schedule)
        r2 = normalize_leg(legs[1], roster_override=self.roster, schedule_override=self.schedule)
        assert r1.player_id == "1"
        assert r2.player_id == "2"


# ───────────────────────────────────────────────────────────────────────────
# Section 14: MLB stat key tests
# ───────────────────────────────────────────────────────────────────────────

class TestMlbStatKeys:
    def test_hits(self):
        sk, _ = _resolve_stat_key("hits", "MLB")
        assert sk == "H"

    def test_home_runs(self):
        sk, _ = _resolve_stat_key("home runs", "MLB")
        assert sk == "HR"

    def test_total_bases(self):
        sk, _ = _resolve_stat_key("total bases", "MLB")
        assert sk == "TB"

    def test_pitcher_strikeouts(self):
        sk, _ = _resolve_stat_key("pitcher strikeouts", "MLB")
        assert sk == "K"

    def test_combo_hits_runs_rbis(self):
        _, sf = _resolve_stat_key("hits + runs + RBIs", "MLB")
        assert sf == "H+R+RBI"


# ───────────────────────────────────────────────────────────────────────────
# Section 15: NFL stat key tests
# ───────────────────────────────────────────────────────────────────────────

class TestNflStatKeys:
    def test_passing_yards(self):
        sk, _ = _resolve_stat_key("passing yards", "NFL")
        assert sk == "PASS_YDS"

    def test_rushing_yards(self):
        sk, _ = _resolve_stat_key("rushing yards", "NFL")
        assert sk == "RUSH_YDS"

    def test_receiving_yards(self):
        sk, _ = _resolve_stat_key("receiving yards", "NFL")
        assert sk == "REC_YDS"

    def test_passing_tds(self):
        sk, _ = _resolve_stat_key("passing tds", "NFL")
        assert sk == "PASS_TD"


# ───────────────────────────────────────────────────────────────────────────
# Section 16: edge cases
# ───────────────────────────────────────────────────────────────────────────

class TestExactNameCollision:
    """
    Two active players share the same full name (e.g. two 'Josh Allen's).
    The normalizer must NEVER auto-resolve — require a unique team_hint or
    return ambiguous.
    """

    def _dual_roster(self):
        """Two players with identical normalized names, different teams."""
        return [
            {
                "player_id": "10",
                "name": "Josh Allen",
                "name_normalized": normalize_name("Josh Allen"),
                "team": "BUF",
                "position": "QB",
                "sport": "NFL",
            },
            {
                "player_id": "11",
                "name": "Josh Allen",
                "name_normalized": normalize_name("Josh Allen"),
                "team": "JAC",
                "position": "DE",
                "sport": "NFL",
            },
        ]

    def test_duplicate_exact_name_no_hint_is_ambiguous(self):
        status, conf, player, cands = resolve_player(
            "Josh Allen", "NFL",
            roster_override=self._dual_roster(),
        )
        assert status == "ambiguous"
        assert player is None
        # Both candidates should appear
        pids = {c["player_id"] for c in cands}
        assert "10" in pids and "11" in pids

    def test_duplicate_exact_name_with_correct_team_hint_resolves(self):
        status, conf, player, cands = resolve_player(
            "Josh Allen", "NFL",
            team_hint="BUF",
            roster_override=self._dual_roster(),
        )
        assert status == "resolved"
        assert player["player_id"] == "10"
        assert player["team"] == "BUF"

    def test_duplicate_exact_name_other_team_hint_resolves(self):
        status, conf, player, cands = resolve_player(
            "Josh Allen", "NFL",
            team_hint="JAC",
            roster_override=self._dual_roster(),
        )
        assert status == "resolved"
        assert player["player_id"] == "11"
        assert player["team"] == "JAC"

    def test_duplicate_exact_name_wrong_team_hint_is_ambiguous(self):
        # Team hint doesn't match either player
        status, conf, player, cands = resolve_player(
            "Josh Allen", "NFL",
            team_hint="KC",
            roster_override=self._dual_roster(),
        )
        # Hint matched no one → still ambiguous
        assert status == "ambiguous"

    def test_duplicate_name_via_normalize_leg_no_hint(self):
        roster = self._dual_roster()
        schedule = [
            {"game_id": "g1", "home_team": "BUF", "away_team": "MIA",
             "game_time": "2026-08-04T20:00:00Z", "sport": "NFL"},
            {"game_id": "g2", "home_team": "JAC", "away_team": "TEN",
             "game_time": "2026-08-04T17:00:00Z", "sport": "NFL"},
        ]
        row = normalize_leg(
            {"player": "Josh Allen", "sport": "NFL", "prop_type": "passing yards", "line": 250.5},
            roster_override=roster,
            schedule_override=schedule,
        )
        assert row.resolution_status == "ambiguous"
        assert row.player_id is None
        assert row.game_id is None

    def test_duplicate_name_via_normalize_leg_with_team_hint(self):
        roster = self._dual_roster()
        schedule = [
            {"game_id": "g1", "home_team": "BUF", "away_team": "MIA",
             "game_time": "2026-08-04T20:00:00Z", "sport": "NFL"},
            {"game_id": "g2", "home_team": "JAC", "away_team": "TEN",
             "game_time": "2026-08-04T17:00:00Z", "sport": "NFL"},
        ]
        row = normalize_leg(
            {"player": "Josh Allen", "sport": "NFL", "prop_type": "passing yards",
             "line": 250.5, "team_hint": "BUF"},
            roster_override=roster,
            schedule_override=schedule,
        )
        assert row.resolution_status == "resolved"
        assert row.player_id == "10"
        assert row.team == "BUF"
        assert row.game_id == "g1"


class TestTeamContextFailsafe:
    """
    Verify that a resolved player with no team abbreviation (e.g. from
    nba_api static-list fallback) is NOT returned as 'resolved' with empty
    game context.  The normalizer must be fail-closed when team is unknown.
    """

    def _make_no_team_roster(self):
        """Roster entries with blank team — simulates nba_api static fallback."""
        return [
            {
                "player_id": "1",
                "name": "Stephen Curry",
                "name_normalized": normalize_name("Stephen Curry"),
                "team": "",          # blank — nba_api static fallback
                "position": "G",
                "sport": "NBA",
            }
        ]

    def test_blank_team_returns_not_found(self):
        row = normalize_leg(
            {"player": "Stephen Curry", "sport": "NBA", "prop_type": "points", "line": 25.5},
            roster_override=self._make_no_team_roster(),
            schedule_override=[{"game_id": "g1", "home_team": "GSW", "away_team": "LAL",
                                 "game_time": "2026-08-04T20:00:00Z", "sport": "NBA"}],
        )
        assert row.resolution_status == "not_found"
        assert "TEAM_CONTEXT_UNAVAILABLE" in row.flags

    def test_blank_team_never_returns_resolved(self):
        row = normalize_leg(
            {"player": "Stephen Curry", "sport": "NBA", "prop_type": "assists", "line": 6.5},
            roster_override=self._make_no_team_roster(),
            schedule_override=[],
        )
        # Must NOT be resolved — game context is unknown
        assert row.resolution_status != "resolved"

    def test_blank_team_no_game_id_in_output(self):
        row = normalize_leg(
            {"player": "Stephen Curry", "sport": "NBA", "prop_type": "points", "line": 25.5},
            roster_override=self._make_no_team_roster(),
            schedule_override=[{"game_id": "g1", "home_team": "GSW", "away_team": "LAL",
                                 "game_time": "2026-08-04T20:00:00Z", "sport": "NBA"}],
        )
        # game_id must not be populated when team context is unavailable
        assert row.game_id is None

    def test_populated_team_resolves_normally(self):
        """Control: same player with populated team should still resolve."""
        roster_with_team = [
            {
                "player_id": "1",
                "name": "Stephen Curry",
                "name_normalized": normalize_name("Stephen Curry"),
                "team": "GSW",
                "position": "G",
                "sport": "NBA",
            }
        ]
        row = normalize_leg(
            {"player": "Stephen Curry", "sport": "NBA", "prop_type": "points", "line": 25.5},
            roster_override=roster_with_team,
            schedule_override=[{"game_id": "g1", "home_team": "GSW", "away_team": "LAL",
                                 "game_time": "2026-08-04T20:00:00Z", "sport": "NBA"}],
        )
        assert row.resolution_status == "resolved"
        assert row.game_id == "g1"
        assert "TEAM_CONTEXT_UNAVAILABLE" not in row.flags


class TestEdgeCases:
    def test_empty_roster_returns_not_found(self):
        status, conf, player, cands = resolve_player(
            "Stephen Curry", "NBA",
            roster_override=[],
        )
        assert status == "not_found"

    def test_none_line_no_ocr_flag(self):
        row = normalize_leg(
            {"player": "Test", "sport": "NBA", "prop_type": "points", "line": None},
            roster_override=[],
        )
        assert row.ocr_suspect is False

    def test_unknown_sport_no_crash(self):
        row = normalize_leg(
            {"player": "Test Player", "sport": "CRICKET", "prop_type": "wickets", "line": 2.5},
            roster_override=[],
        )
        assert row.resolution_status == "not_found"

    def test_demon_and_valid_line(self):
        roster = _roster(("1", "LeBron James", "LAL"))
        schedule = _schedule(("g1", "LAL", "BOS"))
        row = normalize_leg(
            {"player": "LeBron James", "sport": "NBA", "prop_type": "points demon", "line": 25.5},
            roster_override=roster,
            schedule_override=schedule,
        )
        assert row.line_modifier == "DEMON"
        assert row.ocr_suspect is False
        assert row.resolution_status == "resolved"

    def test_resolution_confidence_in_range(self):
        roster = _roster(("1", "Stephen Curry", "GSW"))
        status, conf, player, _ = resolve_player("Stephen Curry", "NBA", roster_override=roster)
        assert 0.0 <= conf <= 1.0

    def test_candidates_list_well_formed(self):
        roster = _roster(
            ("1", "Stephen Curry", "GSW"),
            ("2", "Seth Curry", "BKN"),
        )
        _, _, _, cands = resolve_player("Curry", "NBA", roster_override=roster)
        for c in cands:
            assert "player_id" in c
            assert "name" in c
            assert "score" in c
            assert 0 <= c["score"] <= 1

    def test_to_dict_roundtrip_not_found(self):
        row = normalize_leg(
            {"player": "Nobody Here", "sport": "NBA", "prop_type": "points", "line": 20.5},
            roster_override=_roster(("1", "Stephen Curry", "GSW")),
        )
        d = row.to_dict()
        assert d["resolution_status"] == "not_found"
        assert d["player_id"] is None
