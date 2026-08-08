"""
gate_engine/normalizer.py

9-step player/prop resolution pipeline. Converts raw OCR leg data from
/analyze-board into canonical NormalizedRow dicts the pipeline can score.

Resolution pipeline (per spec):
  1. Text normalisation (diacritics, suffixes, OCR substitutions)
  2. Nickname expansion
  3. Roster lookup (nba_api / MLB Stats API / ESPN via roster_cache)
  4. Exact match (with duplicate full-name collision detection)
  5. Fuzzy match (rapidfuzz, thresholds 0.85 / 0.65)
  6. Team disambiguation for collisions
  7. Game / opponent resolution from today's schedule
  8. Stat key mapping (prop_type → stat_key / stat_formula)
  9. Confidence / status stamping

The NormalizedRow dataclass is the primary output type.  It also supports
dict-style access via __getitem__ so downstream code that expects plain dicts
(e.g. /analyze-and-score) works without conversion.

normalize_legs() accepts legs in both the "player_name / line_value / leg_id"
format used by /analyze-board and the "player / line" format used by tests.
"""
from __future__ import annotations

import datetime
import logging
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator, Optional

import requests

from gate_engine.roster_cache import (
    NICKNAMES,
    _fetch_espn_schedule,
    _fetch_mlb_schedule,
    expand_nickname,
    game_for_team,
    get_roster,
    normalize_name,
    normalize_ocr,
    teams_playing_today,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fuzzy matching — rapidfuzz
# ---------------------------------------------------------------------------
try:
    from rapidfuzz import fuzz as _rfuzz, process as rfprocess  # type: ignore
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _rfuzz = None   # type: ignore
    rfprocess = None  # type: ignore
    _RAPIDFUZZ_AVAILABLE = False


def _fuzzy_score(a: str, b: str) -> float:
    """Return a [0, 1] similarity score between two normalized name strings."""
    if _RAPIDFUZZ_AVAILABLE:
        return _rfuzz.token_set_ratio(a, b) / 100.0
    la, lb = len(a), len(b)
    if la == 0 and lb == 0:
        return 1.0
    if la == 0 or lb == 0:
        return 0.0
    common = sum(1 for c in set(a) if c in b)
    return (2 * common) / (la + lb)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
CONFIDENCE_AUTO_ACCEPT  = 0.85
CONFIDENCE_AMBIGUOUS_LO = 0.65

# ---------------------------------------------------------------------------
# Stat-key mappings per sport
# ---------------------------------------------------------------------------

_STAT_KEY_MAP: dict[str, dict[str, Any]] = {
    "NBA": {
        "points":               "PTS",
        "pts":                  "PTS",
        "rebounds":             "REB",
        "rebs":                 "REB",
        "reb":                  "REB",
        "assists":              "AST",
        "ast":                  "AST",
        "steals":               "STL",
        "stl":                  "STL",
        "blocks":               "BLK",
        "blk":                  "BLK",
        "3-pt made":            "3PM",
        "threes":               "3PM",
        "three pointers made":  "3PM",
        "3pt made":             "3PM",
        "3pm":                  "3PM",
        "free throws made":     "FTM",
        "turnovers":            "TOV",
        "tov":                  "TOV",
        "minutes":              "MIN",
        "min":                  "MIN",
        "double double":        "DD",
        "triple double":        "TD",
        # combo handled via _COMBO_PROP_PATTERNS
    },
    "WNBA": {
        "points":               "PTS",
        "pts":                  "PTS",
        "rebounds":             "REB",
        "reb":                  "REB",
        "assists":              "AST",
        "ast":                  "AST",
        "steals":               "STL",
        "blocks":               "BLK",
        "threes":               "3PM",
        "three pointers made":  "3PM",
    },
    "MLB": {
        "hits":                 "H",
        "h":                    "H",
        "hitter hits":          "H",
        "home runs":            "HR",
        "hr":                   "HR",
        "rbis":                 "RBI",
        "rbi":                  "RBI",
        "runs":                 "R",
        "r":                    "R",
        "runs scored":          "R",
        "stolen bases":         "SB",
        "sb":                   "SB",
        "total bases":          "TB",
        "tb":                   "TB",
        "strikeouts":           "K",
        "k":                    "K",
        "pitcher strikeouts":   "K",
        "hitter strikeouts":    "K",
        "walks":                "BB",
        "bb":                   "BB",
        "earned runs":          "ER",
        "er":                   "ER",
        "hits allowed":         "H_allowed",
        "walks allowed":        "BB",
        "innings pitched":      "IP",
        "ip":                   "IP",
        "pitching outs":        "OUTS",
        # ── WOW-PATCH-2026-08-06-PROP-TYPE-MAPPING-GAP ──────────────────
        # Section 18.4 MLB 1st-Inning Pitches Thrown display-label aliases.
        # All variants resolve to 1IP_PITCHES_THROWN so the route_registry
        # (PROP_TYPE_REQUIRED_GATES) and the 1IP specialist routing (_1ip_patterns
        # in app.py) both recognise the prop without DATA_CONTRACT_FAIL.
        # Any label not in this table continues to return UNKNOWN_PROP_TYPE —
        # no guessing is introduced.
        "1st inn. pitches thrown":   "1IP_PITCHES_THROWN",
        "1st inning pitches thrown": "1IP_PITCHES_THROWN",
        "1st inning pitches":        "1IP_PITCHES_THROWN",
        "1st inn pitches":           "1IP_PITCHES_THROWN",  # "1st Inn Pitches" (no period)
        "first inning pitches thrown": "1IP_PITCHES_THROWN",
        "first inning pitches":      "1IP_PITCHES_THROWN",
        "first inning pitch count":  "1IP_PITCHES_THROWN",
        "1st inning pitch count":    "1IP_PITCHES_THROWN",
        "pitches thrown 1st inning": "1IP_PITCHES_THROWN",
        "pitches thrown 1st":        "1IP_PITCHES_THROWN",
        "1ip pitches thrown":        "1IP_PITCHES_THROWN",
        "1ip pitches":               "1IP_PITCHES_THROWN",
        # ── WOW-PATCH-2026-08-06-MLB-PLATE-APPEARANCES-COVERAGE ──────────
        # Section 18.9 MLB Plate Appearances Props display-label aliases.
        # All variants resolve to MLB_PLATE_APPEARANCES so the mlb_pa_gate
        # and model registry both recognise the prop without DATA_CONTRACT_FAIL.
        "plate appearances":             "MLB_PLATE_APPEARANCES",
        "plate appearance":              "MLB_PLATE_APPEARANCES",
        "plate_appearances":             "MLB_PLATE_APPEARANCES",
        "pa":                            "MLB_PLATE_APPEARANCES",
        "plate app":                     "MLB_PLATE_APPEARANCES",
        "plate app.":                    "MLB_PLATE_APPEARANCES",
        "total plate appearances":       "MLB_PLATE_APPEARANCES",
        "total pa":                      "MLB_PLATE_APPEARANCES",
        "mlb plate appearances":         "MLB_PLATE_APPEARANCES",
    },
    "NFL": {
        "passing yards":        "PASS_YDS",
        "pass yds":             "PASS_YDS",
        "pass yards":           "PASS_YDS",
        "rushing yards":        "RUSH_YDS",
        "rush yds":             "RUSH_YDS",
        "rush yards":           "RUSH_YDS",
        "receiving yards":      "REC_YDS",
        "rec yards":            "REC_YDS",
        "recv yards":           "REC_YDS",
        "receptions":           "REC",
        "rec":                  "REC",
        "catches":              "REC",
        "targets":              "TARGETS",
        "pass attempts":        "PASS_ATT",
        "pass att":             "PASS_ATT",
        "touchdowns":           "TD",
        "td":                   "TD",
        "anytime td":           "ANYTIME_TD",
        "anytime touchdown":    "ANYTIME_TD",
        "passing touchdowns":   "PASS_TD",
        "passing tds":          "PASS_TD",
        "pass tds":             "PASS_TD",
        "pass td":              "PASS_TD",
        "rushing touchdowns":   "RUSH_TD",
        "rushing tds":          "RUSH_TD",
        "rush td":              "RUSH_TD",
        "receiving touchdowns": "REC_TD",
        "receiving tds":        "REC_TD",
        "rec td":               "REC_TD",
        "pass completions":     "PASS_CMP",
        "completions":          "PASS_CMP",
        "interceptions":        "INT",
        "int":                  "INT",
        "tackles":              "TACKLE",
        "tackle":               "TACKLE",
        "sacks":                "SACK",
        "sack":                 "SACK",
        "kicker points":        "KICK_PTS",
        "fantasy points":       "FPTS",
        "fantasy points ppr":   "FPTS_PPR",
    },
    "TENNIS": {
        "fantasy score":        "FANTASY_SCORE",
        "fantasy":              "FANTASY_SCORE",
        "fpts":                 "FANTASY_SCORE",
        "games won":            "GAMES_WON",
        "games":                "GAMES_WON",
        # Total Games (match-level) — both players' games combined
        "total games":          "TOTAL_GAMES",
        "total_games":          "TOTAL_GAMES",
        "match total games":    "TOTAL_GAMES",
        "match games":          "TOTAL_GAMES",
        "game total":           "TOTAL_GAMES",
        "aces":                 "ACES",
        "ace":                  "ACES",
        "double faults":        "DOUBLE_FAULTS",
        "double fault":         "DOUBLE_FAULTS",
        "df":                   "DOUBLE_FAULTS",
    },
    "NHL": {
        "goals":                "G",
        "g":                    "G",
        "assists":              "A",
        "a":                    "A",
        "points":               "PTS",
        "shots on goal":        "SOG",
        "sog":                  "SOG",
        "saves":                "SV",
        "sv":                   "SV",
        "goalie saves":         "SV",
        "plus minus":           "PLUSMINUS",
        "+/-":                  "PLUSMINUS",
        "penalty minutes":      "PIM",
        "pim":                  "PIM",
    },
}

# Combo prop regex patterns (longer patterns must come first)
_COMBO_PROP_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"pts\s*\+\s*rebs?\s*\+\s*asts?", re.I),              "PTS+REB+AST"),
    (re.compile(r"points\s*\+\s*rebounds?\s*\+\s*assists?", re.I),    "PTS+REB+AST"),
    (re.compile(r"p\+r\+a", re.I),                                     "PTS+REB+AST"),
    (re.compile(r"\bpra\b", re.I),                                     "PTS+REB+AST"),
    (re.compile(r"pts\s*\+\s*rebs?\s*\+\s*asts?", re.I),              "PTS+REB+AST"),
    (re.compile(r"pts\+rebs\+asts", re.I),                             "PTS+REB+AST"),
    (re.compile(r"pts\s*\+\s*rebs?(?!\s*\+\s*asts?)", re.I),          "PTS+REB"),
    (re.compile(r"points\s*\+\s*rebounds?(?!\s*\+\s*assists?)", re.I), "PTS+REB"),
    (re.compile(r"pts\s*\+\s*asts?", re.I),                           "PTS+AST"),
    (re.compile(r"rebs?\s*\+\s*asts?", re.I),                         "REB+AST"),
    (re.compile(r"fantasy\s*score", re.I),                             "FANTASY_SCORE"),
    (re.compile(r"fantasy", re.I),                                     "FANTASY"),
    (re.compile(r"hits?\s*\+\s*runs?\s*\+\s*rbis?", re.I),            "H+R+RBI"),
    (re.compile(r"h\+r\+rbi", re.I),                                   "H+R+RBI"),
    (re.compile(r"hits?\s*\+\s*rbis?", re.I),                         "H+RBI"),
    (re.compile(r"strikeouts?\s*\+\s*walks?", re.I),                  "K+BB"),
    (re.compile(r"pass(?:ing)?\s*\+\s*rush(?:ing)?", re.I),           "PASS_YDS+RUSH_YDS"),
    (re.compile(r"rec(?:eiv(?:ing)?)?\s*\+\s*rush(?:ing)?", re.I),   "REC_YDS+RUSH_YDS"),
    (re.compile(r"goals?\s*\+\s*assists?", re.I),                     "G+A"),
    (re.compile(r"pitcher\s*fantasy\s*score", re.I),                  "PITCHER_FANTASY"),
]

# Global single-stat aliases
_GLOBAL_STAT_ALIASES: dict[str, str] = {
    "pts": "PTS", "reb": "REB", "ast": "AST", "blk": "BLK", "stl": "STL",
}

# Line modifier keywords
_DEMON_KEYWORDS   = {"demon", "devil"}
_GOBLIN_KEYWORDS  = {"goblin", "gremlin"}
_FIRE_KEYWORDS    = {"fire"}
_POWERPLAY_KW     = {"powerplay"}

# Standard platform line increments for sanity-checking
_LINE_INCREMENTS: dict[str, float] = {
    "prizepicks": 0.5, "underdog": 0.5, "draftkings": 0.5, "fanduel": 0.5,
}
_LINE_INCREMENT  = 0.5
_LINE_TOLERANCE  = 0.01


# ---------------------------------------------------------------------------
# Stat key mapping (two public APIs: dict-based and tuple-based)
# ---------------------------------------------------------------------------

def _map_stat_key(prop_type: str, sport: str) -> dict:
    """
    Dict-based stat key mapping used by incoming tests and /analyze-and-score.
    Returns {"stat_key": ..., "stat_formula": ..., "flags": [...]}.
    """
    # Check combo first
    for pattern, formula in _COMBO_PROP_PATTERNS:
        if pattern.search(prop_type.strip()):
            return {"stat_key": None, "stat_formula": formula, "flags": []}

    sport_map = _STAT_KEY_MAP.get(sport.upper(), {})
    key = prop_type.lower().strip()
    if key in sport_map:
        val = sport_map[key]
        if isinstance(val, dict):
            return {"stat_key": None, "stat_formula": val.get("stat_formula"), "flags": []}
        return {"stat_key": val, "stat_formula": None, "flags": []}

    if key in _GLOBAL_STAT_ALIASES:
        return {"stat_key": _GLOBAL_STAT_ALIASES[key], "stat_formula": None, "flags": []}

    # Cross-sport lookup
    for sp, smap in _STAT_KEY_MAP.items():
        if key in smap:
            val = smap[key]
            if isinstance(val, dict):
                return {"stat_key": None, "stat_formula": val.get("stat_formula"), "flags": []}
            return {"stat_key": val, "stat_formula": None, "flags": []}

    return {"stat_key": None, "stat_formula": None, "flags": ["UNKNOWN_PROP_TYPE"]}


def _resolve_stat_key(prop_type_raw: str, sport: str) -> tuple[Optional[str], Optional[str]]:
    """Tuple-based stat key mapping used by NormalizedRow API."""
    result = _map_stat_key(prop_type_raw, sport)
    return result.get("stat_key"), result.get("stat_formula")


# ---------------------------------------------------------------------------
# Line sanity check
# ---------------------------------------------------------------------------

def _sanity_check_line(line_value: float, platform: str = "") -> list[str]:
    """Return ['OCR_SUSPECT'] if line value looks wrong for platform."""
    flags: list[str] = []
    if line_value is None:
        return ["OCR_SUSPECT"]
    increment = _LINE_INCREMENTS.get((platform or "").lower(), 0.5)
    remainder = round(line_value % increment, 6)
    if remainder not in (0.0, increment):
        flags.append("OCR_SUSPECT")
    if line_value < 0 or line_value > 500:
        flags.append("OCR_SUSPECT")
    return flags


def _is_sane_line(line_value: float) -> bool:
    """Return True if line_value is a whole multiple of 0.5."""
    remainder = abs(line_value % _LINE_INCREMENT)
    return remainder < _LINE_TOLERANCE or abs(remainder - _LINE_INCREMENT) < _LINE_TOLERANCE


# ---------------------------------------------------------------------------
# Line modifier detection (supports 2-arg and 3-arg calling conventions)
# ---------------------------------------------------------------------------

def _detect_line_modifier(arg1: str, arg2: str, arg3: Optional[str] = None) -> Any:
    """
    Detect PrizePicks line modifier.

    2-arg form (our NormalizedRow API):
        _detect_line_modifier(raw_prop_type, raw_player) → "DEMON" | "GOBLIN" | None

    3-arg form (incoming dict API):
        _detect_line_modifier(platform, player_name, prop_type) → "demon" | "goblin" | "standard"
    """
    if arg3 is None:
        # 2-arg form: arg1=prop_type, arg2=player
        combined = f"{arg1} {arg2}".lower()
        if any(k in combined for k in _DEMON_KEYWORDS):
            return "DEMON"
        if any(k in combined for k in _GOBLIN_KEYWORDS):
            return "GOBLIN"
        if any(k in combined for k in _FIRE_KEYWORDS):
            return "FIRE"
        if any(k in combined for k in _POWERPLAY_KW):
            return "POWERPLAY"
        return None
    else:
        # 3-arg form: arg1=platform, arg2=player, arg3=prop_type
        combined = f"{arg1} {arg2} {arg3}".lower()
        if any(k in combined for k in _DEMON_KEYWORDS):
            return "demon"
        if any(k in combined for k in _GOBLIN_KEYWORDS):
            return "goblin"
        return "standard"


# ---------------------------------------------------------------------------
# Schedule lookup (module-level _get_game so incoming tests can mock it)
# ---------------------------------------------------------------------------

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_MLB_SCHED_API = "https://statsapi.mlb.com/api/v1"

_SPORT_ESPN_PATHS: dict[str, tuple[str, str]] = {
    "NBA":  ("basketball", "nba"),
    "WNBA": ("basketball", "wnba"),
    "NFL":  ("football",   "nfl"),
    "NHL":  ("hockey",     "nhl"),
}


def _get_game(sport: str, team_abbr: str,
              target_date: Optional[str] = None) -> Optional[dict]:
    """
    Returns {"game_id", "game_time", "opponent"} for team_abbr on target_date,
    or None when no game is found.

    Module-level so it can be patched in tests:
        patch("gate_engine.normalizer._get_game", return_value=...)
    """
    if not team_abbr:
        return None
    sport_up = sport.upper()
    date_obj: Optional[date] = None
    if target_date:
        try:
            date_obj = date.fromisoformat(str(target_date)[:10])
        except ValueError:
            pass

    # Use roster_cache schedule fetchers
    if sport_up == "MLB":
        games = _fetch_mlb_schedule(date_obj)
    else:
        games = _fetch_espn_schedule(sport_up, date_obj)

    abbr_upper = team_abbr.upper()
    for g in games:
        home = (g.get("home_team") or "").upper()
        away = (g.get("away_team") or "").upper()
        if abbr_upper in (home, away):
            opponent = away if abbr_upper == home else home
            return {
                "game_id":   g.get("game_id", ""),
                "game_time": g.get("game_time", ""),
                "opponent":  opponent,
            }
    return None


# ---------------------------------------------------------------------------
# NormalizedRow dataclass (primary output type)
# ---------------------------------------------------------------------------

@dataclass
class NormalizedRow(Mapping):
    """
    NormalizedRow is both a typed dataclass (attribute access: row.player_name)
    AND a read-only Mapping (row['player_name'], row.get('x'), 'x' in row,
    dict(row), **row, for k in row). This fixes callers that treat a
    NormalizedRow like a plain dict — e.g. row.get("resolution_status") or
    {**row, "extra": 1} — which previously raised AttributeError/TypeError
    because only __getitem__ was implemented. Mapping supplies get/keys/
    items/values/__contains__/__eq__ from __getitem__ + __iter__ + __len__.
    """
    # Input echo
    raw_player:    str
    raw_prop_type: str
    raw_line:      Optional[float]
    sport:         str

    # Resolution output
    player_id:   Optional[str] = None
    player_name: Optional[str] = None
    team:        Optional[str] = None
    opponent:    Optional[str] = None
    game_id:     Optional[str] = None
    game_time:   Optional[str] = None
    stat_key:    Optional[str] = None
    stat_formula:Optional[str] = None
    line_modifier:Optional[str] = None

    resolution_status:    str   = "not_found"
    resolution_confidence:float = 0.0
    matched_via:   Optional[str] = None
    candidates:    list[dict]   = field(default_factory=list)
    resolution_notes: str       = ""

    # Flags
    ocr_suspect: bool       = False
    flags:       list[str]  = field(default_factory=list)

    # Optional incoming fields (echoed from input)
    leg_id:        Optional[str] = None
    platform:      Optional[str] = None
    ocr_confidence:Optional[float] = None

    def __getitem__(self, key: str) -> Any:
        """Support dict-style access: row['resolution_status']."""
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        """Support iteration/dict(): for key in row / dict(row) / **row."""
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def to_dict(self) -> dict:
        team_abbr = (self.team or "").upper()
        return {
            # Canonical fields
            "raw_player":             self.raw_player,
            "raw_prop_type":          self.raw_prop_type,
            "raw_line":               self.raw_line,
            "sport":                  self.sport,
            "player_id":              self.player_id,
            "player_name":            self.player_name,
            "player_name_raw":        self.raw_player,
            "player_name_resolved":   self.player_name,
            "team":                   self.team,
            "team_abbr":              team_abbr or self.team,
            "opponent":               self.opponent,
            "game_id":                self.game_id,
            "game_time":              self.game_time,
            "stat_key":               self.stat_key,
            "stat_formula":           self.stat_formula,
            "line_modifier":          self.line_modifier,
            "line_value":             self.raw_line,
            "resolution_status":      self.resolution_status,
            "resolution_confidence":  self.resolution_confidence,
            "matched_via":            self.matched_via,
            "candidates":             self.candidates,
            "resolution_notes":       self.resolution_notes,
            "ocr_suspect":            self.ocr_suspect,
            "flags":                  self.flags,
            # Incoming format fields
            "leg_id":           self.leg_id,
            "platform":         self.platform,
            "ocr_confidence":   self.ocr_confidence,
        }


# ---------------------------------------------------------------------------
# Internal roster scoring helpers
# ---------------------------------------------------------------------------

def _name_norm_field(player: dict) -> str:
    """Return the normalized name from a roster record, supporting both field names."""
    return player.get("name_normalized") or player.get("name_norm") or ""


def _team_field(player: dict) -> str:
    """Return team abbreviation from a roster record, supporting both field names."""
    return (player.get("team") or player.get("team_abbr") or "").upper()


def _candidates_from_roster(
    query_normalized: str,
    roster: list[dict],
    top_n: int = 5,
) -> list[tuple[float, dict]]:
    scored: list[tuple[float, dict]] = []
    for player in roster:
        s = _fuzzy_score(query_normalized, _name_norm_field(player))
        scored.append((s, player))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_n]


def _apply_team_hint(
    team_hint: Optional[str],
    candidates: list[tuple[float, dict]],
) -> Optional[tuple[float, dict]]:
    if not team_hint or not candidates:
        return None
    hint_up = team_hint.upper()
    matching = [(s, p) for s, p in candidates if _team_field(p) == hint_up]
    if len(matching) == 1:
        return matching[0]
    return None


# ---------------------------------------------------------------------------
# Player resolution (NormalizedRow API — our canonical implementation)
# ---------------------------------------------------------------------------

def resolve_player(
    raw_name: str,
    sport: str,
    team_hint: Optional[str] = None,
    target_date: Optional[date] = None,
    roster_override: Optional[list[dict]] = None,
    schedule_override: Optional[list[dict]] = None,
) -> tuple[str, float, Optional[dict], list[dict]]:
    """
    Resolve a raw OCR player name to a canonical roster entry.
    Returns (resolution_status, confidence, matched_player_or_None, candidates).
    """
    name_lower = raw_name.strip().lower()
    nickname_expansion = expand_nickname(name_lower)
    if nickname_expansion:
        query_norm = normalize_name(nickname_expansion)
        matched_via_prefix: Optional[str] = "nickname"
    else:
        query_norm = normalize_name(raw_name)
        matched_via_prefix = None

    roster = roster_override if roster_override is not None else get_roster(sport, target_date)
    if not roster:
        return "not_found", 0.0, None, []

    top_candidates = _candidates_from_roster(query_norm, roster, top_n=5)
    if not top_candidates:
        return "not_found", 0.0, None, []

    best_score, best_player = top_candidates[0]

    candidate_list = [
        {"player_id": p["player_id"],
         "name": p.get("name") or p.get("name_raw", ""),
         "team": _team_field(p),
         "score": round(s, 3)}
        for s, p in top_candidates if s >= 0.5
    ]

    # --- Exact / near-exact with full-name duplicate collision detection ---
    if best_score >= 0.98 or query_norm == _name_norm_field(best_player):
        exact_norm = _name_norm_field(best_player)
        duplicates = [
            p for p in roster
            if _name_norm_field(p) == exact_norm
            and p["player_id"] != best_player["player_id"]
        ]
        if duplicates:
            all_exact = [best_player] + duplicates
            if team_hint:
                hint_up = team_hint.upper()
                matches = [p for p in all_exact if _team_field(p) == hint_up]
                if len(matches) == 1:
                    cands = [{"player_id": p["player_id"],
                               "name": p.get("name") or p.get("name_raw", ""),
                               "team": _team_field(p), "score": 1.0}
                              for p in all_exact]
                    mvia = matched_via_prefix or "exact+team_hint"
                    return "resolved", 1.0, {**matches[0], "_matched_via": mvia}, cands
            # Still tied → ambiguous
            cands = [{"player_id": p["player_id"],
                       "name": p.get("name") or p.get("name_raw", ""),
                       "team": _team_field(p), "score": 1.0}
                      for p in all_exact]
            return "ambiguous", 1.0, None, cands

        mvia = matched_via_prefix or "exact"
        score = best_score if best_score < 1.0 else 1.0
        return "resolved", score, {**best_player, "_matched_via": mvia}, candidate_list

    # --- Collision check: same surname, different teams ---
    if len(top_candidates) >= 2:
        second_score, second_player = top_candidates[1]
        if second_score >= CONFIDENCE_AMBIGUOUS_LO:
            best_last   = (_name_norm_field(best_player).split() or [""])[-1]
            second_last = (_name_norm_field(second_player).split() or [""])[-1]
            if best_last == second_last and (best_score - second_score) < 0.05:
                resolved = _apply_team_hint(team_hint, top_candidates[:2])
                if resolved:
                    r_score, r_player = resolved
                    mvia = matched_via_prefix or "fuzzy+team_hint"
                    return "resolved", r_score, {**r_player, "_matched_via": mvia}, candidate_list
                return "ambiguous", best_score, None, candidate_list

    # --- Fuzzy auto-accept ---
    if best_score >= CONFIDENCE_AUTO_ACCEPT:
        mvia = matched_via_prefix or "roster_fuzzy"
        return "resolved", best_score, {**best_player, "_matched_via": mvia}, candidate_list

    # --- Ambiguous band ---
    if best_score >= CONFIDENCE_AMBIGUOUS_LO:
        if team_hint:
            resolved = _apply_team_hint(team_hint, top_candidates[:3])
            if resolved:
                r_score, r_player = resolved
                mvia = matched_via_prefix or "team_disambiguated"
                return "resolved", r_score, {**r_player, "_matched_via": mvia}, candidate_list
        return "ambiguous", best_score, None, candidate_list

    return "not_found", best_score, None, candidate_list


# ---------------------------------------------------------------------------
# Dict-based resolution helpers (compatible with incoming test mocks)
# ---------------------------------------------------------------------------

def _resolved_dict(rec: dict, matched_via: str, score: float, raw: str) -> dict:
    name = rec.get("name") or rec.get("name_raw", "")
    return {
        "player_id":            rec["player_id"],
        "player_name_resolved": name,
        "team_abbr":            _team_field(rec),
        "position":             rec.get("position", ""),
        "resolution_status":    "resolved",
        "resolution_confidence": round(score, 3),
        "matched_via":          matched_via,
        "candidates":           [],
        "resolution_notes":     f"matched '{raw}' → '{name}'",
    }


def _ambiguous_dict(raw: str, candidates: list, note: str) -> dict:
    return {
        "player_id":            None,
        "player_name_resolved": None,
        "team_abbr":            candidates[0]["team"] if candidates else "",
        "position":             "",
        "resolution_status":    "ambiguous",
        "resolution_confidence": candidates[0]["score"] if candidates else 0.0,
        "matched_via":          "fuzzy_ambiguous",
        "candidates":           candidates,
        "resolution_notes":     note,
    }


def _not_found_dict(raw: str, reason: str, note: str) -> dict:
    return {
        "player_id":            None,
        "player_name_resolved": None,
        "team_abbr":            "",
        "position":             "",
        "resolution_status":    "not_found",
        "resolution_confidence": 0.0,
        "matched_via":          reason,
        "candidates":           [],
        "resolution_notes":     note,
    }


def _resolve_player_dict(raw_name: str, sport: str,
                         team_hint: Optional[str] = None) -> dict:
    """
    Dict-based player resolution used by normalize_legs() dict path.
    Delegates to resolve_player() but returns a dict in the incoming format.
    """
    status, conf, player, candidates = resolve_player(
        raw_name, sport, team_hint=team_hint,
    )
    if status == "resolved" and player:
        name = player.get("name") or player.get("name_raw", "")
        return {
            "player_id":            player["player_id"],
            "player_name_resolved": name,
            "team_abbr":            _team_field(player),
            "position":             player.get("position", ""),
            "resolution_status":    "resolved",
            "resolution_confidence": round(conf, 3),
            "matched_via":          player.get("_matched_via", "roster_exact"),
            "candidates":           candidates,
            "resolution_notes":     f"matched '{raw_name}' → '{name}'",
        }
    if status == "ambiguous":
        return _ambiguous_dict(
            raw_name, candidates,
            f"Best score {conf:.2f} is ambiguous; team hint required",
        )
    return _not_found_dict(
        raw_name, "no_match", f"No match ≥0.65 for '{raw_name}'",
    )


# ---------------------------------------------------------------------------
# normalize_leg — NormalizedRow API (used by /normalize-legs endpoint + tests)
# ---------------------------------------------------------------------------

def normalize_leg(
    leg: dict[str, Any],
    target_date: Optional[date] = None,
    roster_override: Optional[list[dict]] = None,
    schedule_override: Optional[list[dict]] = None,
) -> NormalizedRow:
    """
    Normalize a single extracted leg dict into a NormalizedRow.

    Accepts both field conventions:
      player / player_name   — OCR player name
      line / line_value      — numeric line
      sport                  — NBA / WNBA / MLB / NFL / NHL
      prop_type / prop       — prop type string
      team / team_hint       — team abbreviation (for collision resolution)
      leg_id                 — optional leg identifier (echoed)
      platform               — optional platform string
      ocr_confidence         — optional OCR confidence [0–1]
    """
    raw_player    = str(leg.get("player") or leg.get("player_name") or "").strip()
    sport         = str(leg.get("sport") or "").strip().upper()
    raw_prop_type = str(leg.get("prop_type") or leg.get("prop") or "").strip()
    raw_line_raw  = leg.get("line") if leg.get("line") is not None else leg.get("line_value")
    team_hint     = leg.get("team_hint") or leg.get("team")
    leg_id        = leg.get("leg_id")
    platform      = (leg.get("platform") or "").lower().strip()
    ocr_conf      = leg.get("ocr_confidence")

    try:
        raw_line = float(raw_line_raw) if raw_line_raw is not None else None
    except (TypeError, ValueError):
        raw_line = None

    row = NormalizedRow(
        raw_player=raw_player,
        raw_prop_type=raw_prop_type,
        raw_line=raw_line,
        sport=sport,
        leg_id=leg_id,
        platform=platform or None,
        ocr_confidence=float(ocr_conf) if ocr_conf is not None else None,
    )

    # OCR confidence flag
    if ocr_conf is not None and float(ocr_conf) < 0.80:
        row.flags.append("OCR_LOW_CONFIDENCE")

    # Line modifier (2-arg form)
    modifier = _detect_line_modifier(raw_prop_type, raw_player)
    if modifier:
        row.line_modifier = modifier

    # Stat key / combo formula
    stat_key, stat_formula = _resolve_stat_key(raw_prop_type, sport)
    row.stat_key     = stat_key
    row.stat_formula = stat_formula

    # OCR line sanity
    if raw_line is not None:
        extra_flags = _sanity_check_line(raw_line, platform)
        if "OCR_SUSPECT" in extra_flags:
            row.ocr_suspect = True
            row.flags.append("OCR_SUSPECT:line_not_half_increment")

    # Unknown / empty sport
    if not sport:
        row.resolution_status = "not_found"
        row.flags.append("UNKNOWN_SPORT")
        return row

    # Empty player name
    if not raw_player:
        row.resolution_status = "not_found"
        row.flags.append("MISSING_PLAYER_NAME")
        return row

    # Player resolution
    resolution_status, confidence, matched_player, candidates = resolve_player(
        raw_player, sport,
        team_hint=team_hint,
        target_date=target_date,
        roster_override=roster_override,
        schedule_override=schedule_override,
    )

    row.resolution_confidence = round(confidence, 3)
    row.candidates = candidates

    if resolution_status == "not_found":
        row.resolution_status = "not_found"
        return row

    if resolution_status == "ambiguous":
        row.resolution_status = "ambiguous"
        return row

    # Stamp player fields
    row.player_id   = matched_player.get("player_id")
    row.player_name = matched_player.get("name") or matched_player.get("name_raw")
    row.team        = _team_field(matched_player) or None
    row.matched_via = matched_player.get("_matched_via")

    # Fail-safe: team context must be known to look up game
    player_team = row.team
    if not player_team:
        row.resolution_status = "not_found"
        row.flags.append("TEAM_CONTEXT_UNAVAILABLE")
        return row

    # Schedule / game lookup
    game: Optional[dict] = None
    if schedule_override is not None:
        team_up = player_team.upper()
        for g in schedule_override:
            if (g.get("home_team", "").upper() == team_up
                    or g.get("away_team", "").upper() == team_up):
                game = g
                break
        if game is None:
            row.resolution_status = "not_found"
            row.flags.append("NO_GAME_TODAY")
            return row
    else:
        # Use module-level _get_game (patchable in tests)
        date_str = target_date.isoformat() if target_date else None
        game_info = _get_game(sport, player_team, date_str)
        if game_info is None:
            row.resolution_status = "not_found"
            row.resolution_notes  = (
                f"Player resolved ({row.player_name}) but no {sport} game "
                f"found for {player_team} on {date_str or date.today().isoformat()}"
            )
            row.flags.append("NO_GAME_TODAY")
            return row
        # Convert _get_game dict (has "opponent") to schedule-like dict
        row.game_id   = game_info.get("game_id")
        row.game_time = game_info.get("game_time")
        row.opponent  = game_info.get("opponent")
        row.resolution_status = "resolved"
        return row

    # schedule_override path: game is set
    row.game_id   = game.get("game_id")
    row.game_time = game.get("game_time")
    team_up = player_team.upper()
    if game.get("home_team", "").upper() == team_up:
        row.opponent = game.get("away_team")
    else:
        row.opponent = game.get("home_team")

    row.resolution_status = "resolved"
    return row


# ---------------------------------------------------------------------------
# normalize_legs — batch API (NormalizedRow list, pre-warms caches)
# ---------------------------------------------------------------------------

def normalize_legs(
    legs: list[dict[str, Any]],
    target_date: Optional[Any] = None,
    platform_hint: Optional[str] = None,
) -> list[NormalizedRow]:
    """
    Normalize a list of ExtractedLeg dicts into NormalizedRow objects.

    Accepts legs in both field conventions (player_name/line_value and player/line).
    Rosters are pre-warmed once per unique sport.
    platform_hint is applied to legs that don't supply their own platform field.
    """
    # Parse target_date
    target_date_obj: Optional[date] = None
    if target_date is not None:
        if isinstance(target_date, date):
            target_date_obj = target_date
        else:
            try:
                target_date_obj = date.fromisoformat(str(target_date)[:10])
            except ValueError:
                pass

    # Pre-warm caches
    sports = {(leg.get("sport") or "").strip().upper() for leg in legs if leg.get("sport")}
    for sport in sports:
        if sport:
            get_roster(sport, target_date_obj)

    results = []
    for leg in legs:
        # Apply platform_hint if leg doesn't have its own
        if platform_hint and not leg.get("platform"):
            leg = {**leg, "platform": platform_hint}
        results.append(normalize_leg(leg, target_date=target_date_obj))
    return results
