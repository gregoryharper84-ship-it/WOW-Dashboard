"""
gate_engine/normalizer.py

9-step player/prop resolution pipeline.  Converts raw OCR leg data from
/analyze-board into canonical NormalizedRow dicts the pipeline can score.

Resolution pipeline (per spec):
  1. Text normalisation (diacritics, suffixes, OCR substitutions)
  2. Team/platform context extraction from visual hint
  3. Roster lookup (nba_api / MLB Stats API / ESPN via roster_cache)
  4. Exact match
  5. Fuzzy match (rapidfuzz, thresholds 0.85 / 0.65)
  6. Team disambiguation for collisions
  7. Game / opponent resolution from today's schedule
  8. Stat key mapping (prop_type → stat_key / stat_formula)
  9. Confidence / status stamping
"""

from __future__ import annotations

import datetime
import logging
import re
import uuid
from typing import Any, Optional

import requests
from rapidfuzz import fuzz, process as rfprocess

from gate_engine.roster_cache import (
    get_roster,
    normalize_name,
    normalize_ocr,
    NICKNAMES,
)

logger = logging.getLogger(__name__)

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
        "blocks":               "BLK",
        "3-pt made":            "FG3M",
        "threes":               "FG3M",
        "three pointers made":  "FG3M",
        "free throws made":     "FTM",
        "turnovers":            "TOV",
        # combo — use stat_formula
        "pts+rebs+asts":        {"stat_formula": "PTS+REB+AST"},
        "points+rebounds+assists": {"stat_formula": "PTS+REB+AST"},
        "pts+rebs":             {"stat_formula": "PTS+REB"},
        "pts+asts":             {"stat_formula": "PTS+AST"},
        "rebs+asts":            {"stat_formula": "REB+AST"},
        "fantasy score":        {"stat_formula": "FANTASY"},
    },
    "WNBA": {
        "points":               "PTS",
        "pts":                  "PTS",
        "rebounds":             "REB",
        "assists":              "AST",
        "steals":               "STL",
        "blocks":               "BLK",
        "pts+rebs+asts":        {"stat_formula": "PTS+REB+AST"},
        "fantasy score":        {"stat_formula": "FANTASY"},
    },
    "MLB": {
        "hits":                 "H",
        "hitter hits":          "H",
        "pitcher strikeouts":   "SO",
        "strikeouts":           "SO",
        "total bases":          "TB",
        "runs scored":          "R",
        "rbis":                 "RBI",
        "rbi":                  "RBI",
        "hitter strikeouts":    "SO",
        "hits allowed":         "H_allowed",
        "earned runs":          "ER",
        "walks allowed":        "BB",
        "h+r+rbi":              {"stat_formula": "H+R+RBI"},
        "pitcher fantasy score":{"stat_formula": "PITCHER_FANTASY"},
    },
    "NFL": {
        "passing yards":        "PASS_YDS",
        "rushing yards":        "RUSH_YDS",
        "receiving yards":      "REC_YDS",
        "receptions":           "REC",
        "touchdowns":           "TD",
        "passing touchdowns":   "PASS_TD",
        "interceptions":        "INT",
        "fantasy score":        {"stat_formula": "FANTASY"},
    },
    "NHL": {
        "shots on goal":        "SOG",
        "goals":                "G",
        "assists":              "A",
        "points":               "PTS",
        "saves":                "SV",
        "goalie saves":         "SV",
    },
}

# PrizePicks line modifier keywords
_DEMON_KEYWORDS = {"demon", "devil"}
_GOBLIN_KEYWORDS = {"goblin", "gremlin"}

# Standard line increments per platform for sanity-checking
_LINE_INCREMENTS: dict[str, float] = {
    "prizepicks": 0.5,
    "underdog":   0.5,
    "draftkings": 0.5,
    "fanduel":    0.5,
}


# ---------------------------------------------------------------------------
# Schedule / game lookup
# ---------------------------------------------------------------------------

_ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_MLB_API   = "https://statsapi.mlb.com/api/v1"

_SPORT_ESPN_PATHS: dict[str, tuple[str, str]] = {
    "NBA":  ("basketball", "nba"),
    "WNBA": ("basketball", "wnba"),
    "NFL":  ("football",   "nfl"),
    "NHL":  ("hockey",     "nhl"),
}


def _schedule_espn(sport: str, team_abbr: str,
                   target_date: Optional[str] = None) -> Optional[dict]:
    """
    Returns {"game_id", "game_time", "opponent"} for team_abbr on target_date,
    or None when no game is found.
    """
    paths = _SPORT_ESPN_PATHS.get(sport)
    if not paths:
        return None
    s_path, l_path = paths
    date_str = target_date or datetime.date.today().isoformat()

    try:
        url = f"{_ESPN_BASE}/{s_path}/{l_path}/scoreboard"
        resp = requests.get(url, params={"dates": date_str.replace("-", "")},
                            timeout=8)
        if resp.status_code != 200:
            return None
        events = resp.json().get("events", [])
        abbr_upper = team_abbr.upper()
        for ev in events:
            competitors = ev.get("competitions", [{}])[0].get("competitors", [])
            teams = [c.get("team", {}).get("abbreviation", "").upper()
                     for c in competitors]
            if abbr_upper in teams:
                opponent = next(
                    (t for t in teams if t != abbr_upper), ""
                )
                start_time = ev.get("date", "")
                game_id    = str(ev.get("id", ""))
                return {
                    "game_id":   game_id,
                    "game_time": start_time,
                    "opponent":  opponent,
                }
    except Exception as exc:
        logger.debug("schedule_espn %s/%s: %s", sport, team_abbr, exc)
    return None


def _schedule_mlb(team_abbr: str,
                  target_date: Optional[str] = None) -> Optional[dict]:
    """MLB schedule via statsapi.mlb.com"""
    date_str = target_date or datetime.date.today().isoformat()
    try:
        resp = requests.get(
            f"{_MLB_API}/schedule",
            params={"sportId": 1, "date": date_str,
                    "hydrate": "team", "fields":
                    "dates,games,gamePk,status,teams,away,home,team,abbreviation,gameDate"},
            timeout=8,
            headers={"User-Agent": "WOW/1.0"},
        )
        if resp.status_code != 200:
            return None
        abbr_upper = team_abbr.upper()
        for date_entry in resp.json().get("dates", []):
            for game in date_entry.get("games", []):
                away_abbr = game.get("teams", {}).get("away", {}).get("team", {}).get("abbreviation", "").upper()
                home_abbr = game.get("teams", {}).get("home", {}).get("team", {}).get("abbreviation", "").upper()
                if abbr_upper in (away_abbr, home_abbr):
                    opponent = home_abbr if abbr_upper == away_abbr else away_abbr
                    return {
                        "game_id":   str(game.get("gamePk", "")),
                        "game_time": game.get("gameDate", ""),
                        "opponent":  opponent,
                    }
    except Exception as exc:
        logger.debug("schedule_mlb %s: %s", team_abbr, exc)
    return None


def _get_game(sport: str, team_abbr: str,
              target_date: Optional[str] = None) -> Optional[dict]:
    if not team_abbr:
        return None
    if sport == "MLB":
        return _schedule_mlb(team_abbr, target_date)
    return _schedule_espn(sport, team_abbr, target_date)


# ---------------------------------------------------------------------------
# Core resolution logic
# ---------------------------------------------------------------------------

def _resolve_player(
    raw_name: str,
    sport: str,
    team_hint: Optional[str] = None,
) -> dict:
    """
    Returns:
      {
        player_id, player_name_resolved, team_abbr,
        resolution_status, resolution_confidence,
        matched_via, candidates, resolution_notes,
      }
    """
    # Step 1 — text normalisation (OCR substitutions on raw input)
    ocr_norm  = normalize_ocr(raw_name)

    # Check nickname dictionary first
    expanded = NICKNAMES.get(ocr_norm) or NICKNAMES.get(ocr_norm.split()[0] if ocr_norm else "")

    # Step 3 — roster lookup
    roster = get_roster(sport)
    if not roster:
        return _not_found(raw_name, "roster_unavailable",
                          "Roster could not be loaded for " + sport)

    # Build searchable name list
    names_norm = [r["name_norm"] for r in roster]

    # Step 4 — exact match (prefer nickname expansion if available)
    queries = [ocr_norm]
    if expanded:
        queries.insert(0, expanded)

    for query in queries:
        exact_hits = [rec for rec in roster if rec["name_norm"] == query]
        if exact_hits:
            if len(exact_hits) == 1:
                return _resolved(exact_hits[0], "roster_exact", 1.0, raw_name)
            # Multiple exact matches (name collision) — try team hint
            if team_hint:
                th = team_hint.upper()
                team_exact = [r for r in exact_hits if r["team_abbr"] == th]
                if len(team_exact) == 1:
                    return _resolved(team_exact[0], "team_disambiguated", 1.0, raw_name)
            # Still tied → ambiguous
            candidates = [{"player_id": r["player_id"], "name": r["name_raw"],
                           "team": r["team_abbr"], "score": 1.0} for r in exact_hits]
            return _ambiguous(raw_name, candidates,
                              f"Exact name match but {len(exact_hits)} players share this name; "
                              f"team hint required to disambiguate")

    # Step 5 — fuzzy match
    hits = rfprocess.extract(
        ocr_norm,
        names_norm,
        scorer=fuzz.token_set_ratio,
        limit=5,
        score_cutoff=65,
    )

    if not hits:
        return _not_found(raw_name, "no_match", f"No fuzzy match ≥65 for '{raw_name}'")

    # Build candidate list
    candidates = []
    for name_match, score, idx in hits:
        rec   = roster[idx]
        score_norm = score / 100.0
        candidates.append({
            "player_id": rec["player_id"],
            "name":      rec["name_raw"],
            "team":      rec["team_abbr"],
            "score":     round(score_norm, 3),
        })

    top_score = hits[0][1] / 100.0
    top_rec   = roster[hits[0][2]]

    # Step 6 — team disambiguation when multiple candidates ≥ threshold
    high_cands = [(n, s, i) for n, s, i in hits if s / 100.0 >= 0.65]

    if len(high_cands) > 1 and team_hint:
        th = team_hint.upper()
        team_match = [
            (n, s, i) for n, s, i in high_cands
            if roster[i]["team_abbr"] == th
        ]
        if len(team_match) == 1:
            top_rec   = roster[team_match[0][2]]
            top_score = team_match[0][1] / 100.0
            candidates = [c for c in candidates
                          if c["player_id"] == top_rec["player_id"]] + \
                         [c for c in candidates
                          if c["player_id"] != top_rec["player_id"]]
            if top_score >= 0.85:
                return _resolved(top_rec, "team_disambiguated", top_score, raw_name)
            return _ambiguous(raw_name, candidates,
                              f"Team hint '{th}' reduced to 1 but score {top_score:.2f} < 0.85")

    # Step 5 continued — apply score thresholds
    if top_score >= 0.85:
        if len(high_cands) > 1:
            # Multiple ≥ 0.85 without team resolution → ambiguous
            top_two_scores = [s / 100.0 for _, s, _ in high_cands[:2]]
            if top_two_scores[1] >= 0.85:
                return _ambiguous(raw_name, candidates,
                                  "Multiple candidates ≥0.85; need team hint to disambiguate")
        return _resolved(top_rec, "roster_fuzzy", top_score, raw_name)

    if top_score >= 0.65:
        return _ambiguous(raw_name, candidates,
                          f"Best fuzzy score {top_score:.2f} is in 0.65–0.85 ambiguous range")

    return _not_found(raw_name, "low_confidence",
                      f"Best fuzzy score {top_score:.2f} < 0.65 threshold")


def _resolved(rec: dict, matched_via: str, score: float, raw: str) -> dict:
    return {
        "player_id":           rec["player_id"],
        "player_name_resolved": rec["name_raw"],
        "team_abbr":           rec["team_abbr"],
        "position":            rec.get("position", ""),
        "resolution_status":   "resolved",
        "resolution_confidence": round(score, 3),
        "matched_via":         matched_via,
        "candidates":          [],
        "resolution_notes":    f"matched '{raw}' → '{rec['name_raw']}'",
    }


def _ambiguous(raw: str, candidates: list, note: str) -> dict:
    return {
        "player_id":           None,
        "player_name_resolved": None,
        "team_abbr":           candidates[0]["team"] if candidates else "",
        "position":            "",
        "resolution_status":   "ambiguous",
        "resolution_confidence": candidates[0]["score"] if candidates else 0.0,
        "matched_via":         "fuzzy_ambiguous",
        "candidates":          candidates,
        "resolution_notes":    note,
    }


def _not_found(raw: str, reason: str, note: str) -> dict:
    return {
        "player_id":           None,
        "player_name_resolved": None,
        "team_abbr":           "",
        "position":            "",
        "resolution_status":   "not_found",
        "resolution_confidence": 0.0,
        "matched_via":         reason,
        "candidates":          [],
        "resolution_notes":    note,
    }


# ---------------------------------------------------------------------------
# Stat key mapping
# ---------------------------------------------------------------------------

def _map_stat_key(prop_type: str, sport: str) -> dict:
    """
    Returns {"stat_key": "PTS"} or {"stat_formula": "PTS+REB+AST"}.
    Falls back to {"stat_key": None, "flags": ["UNKNOWN_PROP_TYPE"]} on miss.
    """
    sport_map = _STAT_KEY_MAP.get(sport.upper(), {})
    key = prop_type.lower().strip()
    result = sport_map.get(key)
    if result is None:
        return {"stat_key": None, "stat_formula": None,
                "flags": ["UNKNOWN_PROP_TYPE"]}
    if isinstance(result, dict):
        return {"stat_key": None, "stat_formula": result.get("stat_formula"),
                "flags": []}
    return {"stat_key": result, "stat_formula": None, "flags": []}


def _detect_line_modifier(platform: str, player_name_raw: str,
                          prop_type_raw: str) -> str:
    """Return 'demon' | 'goblin' | 'standard'."""
    combined = (platform + " " + player_name_raw + " " + prop_type_raw).lower()
    if any(k in combined for k in _DEMON_KEYWORDS):
        return "demon"
    if any(k in combined for k in _GOBLIN_KEYWORDS):
        return "goblin"
    return "standard"


def _sanity_check_line(line_value: float, platform: str) -> list[str]:
    """Return ['OCR_SUSPECT'] if line value looks wrong for platform."""
    flags: list[str] = []
    if line_value is None:
        return ["OCR_SUSPECT"]
    increment = _LINE_INCREMENTS.get(platform.lower(), 0.5)
    # Line should be a multiple of increment
    remainder = round(line_value % increment, 6)
    if remainder not in (0.0, increment):
        flags.append("OCR_SUSPECT")
    # Sanity bounds — no prop line should be negative or astronomically large
    if line_value < 0 or line_value > 500:
        flags.append("OCR_SUSPECT")
    return flags


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize_legs(
    extracted_legs: list[dict],
    target_date: Optional[str] = None,
    platform_hint: Optional[str] = None,
) -> list[dict]:
    """
    Convert a list of ExtractedLeg dicts (from /analyze-board) into
    NormalizedRow dicts ready for enrichment + pipeline scoring.

    ExtractedLeg fields expected:
      leg_id, player_name, sport, prop_type, side, line_value,
      platform, ocr_confidence, team (optional), extraction_notes (optional)
    """
    results = []
    for leg in extracted_legs:
        results.append(_normalize_one(leg, target_date, platform_hint))
    return results


def _normalize_one(leg: dict, target_date: Optional[str],
                   platform_hint: Optional[str]) -> dict:
    leg_id       = leg.get("leg_id") or str(uuid.uuid4())
    raw_name     = (leg.get("player_name") or leg.get("player") or "").strip()
    sport        = (leg.get("sport") or "").upper().strip()
    prop_type    = (leg.get("prop_type") or leg.get("prop") or "").strip()
    side         = (leg.get("side") or "over").upper()
    line_value   = leg.get("line_value") or leg.get("line")
    platform     = (leg.get("platform") or platform_hint or "").lower().strip()
    team_hint    = (leg.get("team") or "").upper().strip() or None
    ocr_conf     = leg.get("ocr_confidence")
    extr_notes   = leg.get("extraction_notes") or ""

    flags: list[str] = []

    # Low OCR confidence flag
    if ocr_conf is not None and float(ocr_conf) < 0.80:
        flags.append("OCR_LOW_CONFIDENCE")

    # Line sanity check
    try:
        line_float = float(line_value) if line_value is not None else None
    except (TypeError, ValueError):
        line_float = None
        flags.append("OCR_SUSPECT")

    if line_float is not None:
        flags.extend(_sanity_check_line(line_float, platform))

    # Step 8 — stat key mapping (before resolution, since it's sport-only)
    stat_map  = _map_stat_key(prop_type, sport) if sport else \
                {"stat_key": None, "stat_formula": None, "flags": ["UNKNOWN_SPORT"]}
    flags.extend(stat_map.get("flags", []))

    # Line modifier
    line_modifier = _detect_line_modifier(platform, raw_name, prop_type)

    # Steps 1-6 — player resolution
    if not sport:
        res = _not_found(raw_name, "unknown_sport",
                         "Sport could not be determined from OCR output")
    else:
        res = _resolve_player(raw_name, sport, team_hint)

    # Step 7 — game / schedule lookup (only if player resolved)
    game_info: Optional[dict] = None
    if res["resolution_status"] == "resolved" and res["team_abbr"]:
        game_info = _get_game(sport, res["team_abbr"], target_date)
        if game_info is None:
            # Player resolves but no game today — downgrade to not_found
            res["resolution_status"] = "not_found"
            res["resolution_notes"]  = (
                f"Player resolved ({res['player_name_resolved']}) "
                f"but no {sport} game found for {res['team_abbr']} "
                f"on {target_date or datetime.date.today().isoformat()}"
            )
            flags.append("NO_GAME_TODAY")

    row: dict[str, Any] = {
        "leg_id":               leg_id,
        # Resolution fields
        "player_id":            res.get("player_id"),
        "player_name_raw":      raw_name,
        "player_name_resolved": res.get("player_name_resolved"),
        "team":                 res.get("team_abbr") or "",
        "opponent":             game_info["opponent"]  if game_info else None,
        "game_id":              game_info["game_id"]   if game_info else None,
        "game_time":            game_info["game_time"] if game_info else None,
        # Prop fields
        "stat_key":             stat_map.get("stat_key"),
        "stat_formula":         stat_map.get("stat_formula"),
        "line_value":           line_float,
        "line_modifier":        line_modifier,
        "side":                 side,
        "sport":                sport,
        "platform":             platform,
        # Resolution metadata
        "resolution_status":    res["resolution_status"],
        "resolution_confidence": res["resolution_confidence"],
        "matched_via":          res.get("matched_via"),
        "candidates":           res.get("candidates", []),
        "resolution_notes":     res.get("resolution_notes", ""),
        # Flags
        "flags":                list(dict.fromkeys(flags)),  # dedupe
        "ocr_confidence":       ocr_conf,
        "extraction_notes":     extr_notes,
    }
    return row
