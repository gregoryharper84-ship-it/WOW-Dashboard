from __future__ import annotations

"""Strict-pregame MLB V2 live feature hydrator.

Heavy historical/current-season state is built offline and shipped as a hashed
artifact.  Request-time work is intentionally light: load the local state, fetch
only the target-date MLB schedule/probable pitchers, and build the 41 market-free
features.  The state cutoff must equal the target game date, which guarantees no
same-day outcome can enter the feature vector.
"""

import gzip
import json
import re
import threading
from datetime import date
from pathlib import Path
from typing import Any

import requests

from mlb_v2_features import FEATURE_NAMES, build_feature_vector, starter_summary, team_summary
from mlb_v2_incremental import advance_state_to_target

can_execute: bool = False
can_approve_bets: bool = False

_STATE_PATH = Path(__file__).resolve().parent / "mlb_v2_artifacts" / "mlb_v2_pregame_state.json.gz"
_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
_state_lock = threading.Lock()
_state_cache: dict[str, Any] | None = None

MLB_ID_TO_RETRO = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHN",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KCA", 119: "LAN", 120: "WAS", 121: "NYN", 133: "OAK",
    134: "PIT", 135: "SDN", 136: "SEA", 137: "SFN", 138: "SLN",
    139: "TBA", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CHA", 146: "MIA", 147: "NYA", 158: "MIL",
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


_TEAM_ALIASES: dict[str, str] = {}


def _aliases(retro: str, *names: str) -> None:
    for n in (retro, *names):
        _TEAM_ALIASES[_norm(n)] = retro


_aliases("LAA", "Los Angeles Angels", "LA Angels", "Angels")
_aliases("ARI", "Arizona Diamondbacks", "Diamondbacks", "Dbacks")
_aliases("BAL", "Baltimore Orioles", "Orioles")
_aliases("BOS", "Boston Red Sox", "Red Sox")
_aliases("CHN", "Chicago Cubs", "Cubs", "CHC")
_aliases("CIN", "Cincinnati Reds", "Reds")
_aliases("CLE", "Cleveland Guardians", "Guardians")
_aliases("COL", "Colorado Rockies", "Rockies")
_aliases("DET", "Detroit Tigers", "Tigers")
_aliases("HOU", "Houston Astros", "Astros")
_aliases("KCA", "Kansas City Royals", "Royals", "KC", "KCR")
_aliases("LAN", "Los Angeles Dodgers", "LA Dodgers", "Dodgers", "LAD")
_aliases("WAS", "Washington Nationals", "Nationals", "WSH")
_aliases("NYN", "New York Mets", "NY Mets", "Mets", "NYM")
_aliases("OAK", "Athletics", "Oakland Athletics", "A's", "As")
_aliases("PIT", "Pittsburgh Pirates", "Pirates")
_aliases("SDN", "San Diego Padres", "Padres", "SD", "SDP")
_aliases("SEA", "Seattle Mariners", "Mariners")
_aliases("SFN", "San Francisco Giants", "Giants", "SF", "SFG")
_aliases("SLN", "St. Louis Cardinals", "St Louis Cardinals", "Cardinals", "STL")
_aliases("TBA", "Tampa Bay Rays", "Rays", "TB", "TBR")
_aliases("TEX", "Texas Rangers", "Rangers")
_aliases("TOR", "Toronto Blue Jays", "Blue Jays")
_aliases("MIN", "Minnesota Twins", "Twins")
_aliases("PHI", "Philadelphia Phillies", "Phillies")
_aliases("ATL", "Atlanta Braves", "Braves")
_aliases("CHA", "Chicago White Sox", "White Sox", "CWS", "CHW")
_aliases("MIA", "Miami Marlins", "Marlins")
_aliases("NYA", "New York Yankees", "NY Yankees", "Yankees", "NYY")
_aliases("MIL", "Milwaukee Brewers", "Brewers")


def canonical_team(value: Any) -> str | None:
    return _TEAM_ALIASES.get(_norm(value))


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    s = str(value).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _load_state() -> dict[str, Any]:
    global _state_cache
    if _state_cache is not None:
        return _state_cache
    with _state_lock:
        if _state_cache is not None:
            return _state_cache
        if not _STATE_PATH.exists():
            raise RuntimeError("MLB_V2_STATE_ARTIFACT_MISSING")
        with gzip.open(_STATE_PATH, "rt", encoding="utf-8") as f:
            state = json.load(f)
        for collection in ("team_hist", "pitcher_hist"):
            for rows in (state.get(collection) or {}).values():
                for row in rows:
                    row["date"] = date.fromisoformat(row["date"])
        _state_cache = state
        return state


def _target_date(row: dict[str, Any], enrichment: dict[str, Any]) -> date:
    for source in (row, enrichment):
        for key in ("game_date", "event_date", "slate_date", "date", "start_time", "commence_time"):
            d = _parse_date(source.get(key))
            if d is not None:
                return d
    return date.today()


def _candidate_teams(row: dict[str, Any], enrichment: dict[str, Any]) -> tuple[str | None, str | None]:
    candidate = (
        row.get("team") or row.get("player") or row.get("participant")
        or enrichment.get("team") or enrichment.get("candidate_team")
    )
    opponent = (
        row.get("opponent") or row.get("opponent_team")
        or enrichment.get("opponent") or enrichment.get("opponent_team")
    )
    return canonical_team(candidate), canonical_team(opponent)


def _schedule(target: date) -> list[dict[str, Any]]:
    resp = requests.get(
        _SCHEDULE_URL,
        params={"sportId": 1, "gameTypes": "R", "date": target.isoformat(), "hydrate": "probablePitcher"},
        timeout=12,
        headers={"User-Agent": "WOW-MLB-V2/20260827"},
    )
    resp.raise_for_status()
    payload = resp.json()
    out: list[dict[str, Any]] = []
    for day in payload.get("dates", []):
        for g in day.get("games", []):
            if g.get("gameType") != "R":
                continue
            home = g.get("teams", {}).get("home", {})
            away = g.get("teams", {}).get("away", {})
            hid = int((home.get("team") or {}).get("id") or 0)
            aid = int((away.get("team") or {}).get("id") or 0)
            hp = home.get("probablePitcher") or {}
            ap = away.get("probablePitcher") or {}
            out.append({
                "gamePk": int(g.get("gamePk") or 0),
                "game_date": str(g.get("officialDate") or target.isoformat()),
                "abstract_state": (g.get("status") or {}).get("abstractGameState"),
                "detailed_state": (g.get("status") or {}).get("detailedState"),
                "home_team": MLB_ID_TO_RETRO.get(hid),
                "away_team": MLB_ID_TO_RETRO.get(aid),
                "home_starter_id": f"mlbam:{int(hp['id'])}" if hp.get("id") else None,
                "away_starter_id": f"mlbam:{int(ap['id'])}" if ap.get("id") else None,
                "home_starter_name": hp.get("fullName"),
                "away_starter_name": ap.get("fullName"),
            })
    # Schedule endpoint can duplicate a gamePk; canonicalize here too.
    dedup = {g["gamePk"]: g for g in out if g["gamePk"]}
    return list(dedup.values())


def _match_game(row: dict[str, Any], enrichment: dict[str, Any], games: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    direct_ids = [row.get("gamePk"), row.get("game_pk"), row.get("event_id"), enrichment.get("gamePk"), enrichment.get("game_pk")]
    for raw in direct_ids:
        try:
            gid = int(str(raw).replace("MLBAM", ""))
        except (TypeError, ValueError):
            continue
        matches = [g for g in games if g["gamePk"] == gid]
        if len(matches) == 1:
            return matches[0], []
    cand, opp = _candidate_teams(row, enrichment)
    if not cand or not opp:
        return None, ["MLB_V2_TEAM_IDENTITY_UNRESOLVED"]
    matches = [g for g in games if {g.get("home_team"), g.get("away_team")} == {cand, opp}]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, ["MLB_V2_DOUBLEHEADER_AMBIGUOUS:gamePk_required"]
    return None, [f"MLB_V2_SCHEDULE_MATCH_NOT_FOUND:{cand}_vs_{opp}"]


def hydrate_mlb_v2_enrichment(row: dict[str, Any], enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    enrichment = dict(enrichment or {})
    if isinstance(enrichment.get("mlb_v2_feature_vector"), list):
        return enrichment
    target = _target_date(row, enrichment)
    try:
        state = _load_state()
    except Exception as exc:
        enrichment["mlb_v2_hydration"] = {"status": "NOT_READY", "blockers": [str(exc)], "can_execute": False}
        return enrichment

    blockers: list[str] = []
    refresh_meta: dict[str, Any] = {
        "status": "NOT_NEEDED",
        "from_cutoff": str(state.get("cutoff_exclusive") or ""),
        "to_cutoff": target.isoformat(),
        "days_advanced": 0,
        "games_added": 0,
        "source": "BUNDLED_OR_ALREADY_ADVANCED_STATE",
    }
    cutoff = str(state.get("cutoff_exclusive") or "")
    # If this Render process is carrying an older bundled cutoff, catch it up
    # transactionally from official final MLB results strictly before target.
    # The state lock serializes refreshes across concurrent scoring requests.
    if cutoff and cutoff < target.isoformat():
        try:
            with _state_lock:
                refresh_meta = advance_state_to_target(state, target)
        except Exception as exc:
            enrichment["mlb_v2_hydration"] = {
                "status": "NOT_READY",
                "blockers": [f"MLB_V2_INCREMENTAL_REFRESH_FAILED:{type(exc).__name__}:{exc}"],
                "state_refresh": refresh_meta,
                "strict_prior_date_only": True,
                "same_day_results_used": False,
                "can_execute": False,
            }
            return enrichment
    cutoff = str(state.get("cutoff_exclusive") or "")
    if cutoff != target.isoformat():
        blockers.append(f"MLB_V2_STATE_STALE:cutoff_exclusive={cutoff}:target={target.isoformat()}")
    if not bool(state.get("strict_prior_date_only")):
        blockers.append("MLB_V2_STATE_LEAKAGE_ATTESTATION_MISSING")
    if blockers:
        enrichment["mlb_v2_hydration"] = {
            "status": "NOT_READY",
            "blockers": blockers,
            "state_refresh": refresh_meta,
            "can_execute": False,
        }
        return enrichment

    try:
        games = _schedule(target)
    except Exception as exc:
        enrichment["mlb_v2_hydration"] = {"status": "NOT_READY", "blockers": [f"MLB_V2_SCHEDULE_FETCH_FAILED:{type(exc).__name__}"], "can_execute": False}
        return enrichment
    game, match_blockers = _match_game(row, enrichment, games)
    if game is None:
        enrichment["mlb_v2_hydration"] = {"status": "NOT_READY", "blockers": match_blockers, "can_execute": False}
        return enrichment
    if game.get("abstract_state") != "Preview":
        blockers.append(f"MLB_V2_PREGAME_ONLY:state={game.get('abstract_state')}")
    if not game.get("home_starter_id") or not game.get("away_starter_id"):
        blockers.append("MLB_V2_PROBABLE_STARTER_IDENTITY_MISSING")

    home_code = game.get("home_team")
    away_code = game.get("away_team")
    team_hist = state.get("team_hist") or {}
    pitcher_hist = state.get("pitcher_hist") or {}
    elo = state.get("elo") or {}
    hh = team_hist.get(home_code) or []
    ah = team_hist.get(away_code) or []
    if len(hh) < 10 or len(ah) < 10:
        blockers.append("MLB_V2_INSUFFICIENT_TEAM_HISTORY")
    if blockers:
        enrichment["mlb_v2_hydration"] = {"status": "NOT_READY", "blockers": blockers, "game": game, "can_execute": False}
        return enrichment

    try:
        ht = team_summary(hh, target, True)
        at = team_summary(ah, target, False)
        hs_hist = pitcher_hist.get(game["home_starter_id"]) or []
        as_hist = pitcher_hist.get(game["away_starter_id"]) or []
        hs = starter_summary(hs_hist, target)
        ass = starter_summary(as_hist, target)
        features = build_feature_vector(ht, at, hs, ass, float(elo.get(home_code, 1500.0)), float(elo.get(away_code, 1500.0)))
    except Exception as exc:
        enrichment["mlb_v2_hydration"] = {"status": "NOT_READY", "blockers": [str(exc)], "game": game, "can_execute": False}
        return enrichment

    enrichment["mlb_v2_feature_vector"] = features
    enrichment["mlb_v2_hydration"] = {
        "status": "FEATURES_READY",
        "blockers": [],
        "gamePk": game["gamePk"],
        "game_date": target.isoformat(),
        "home_team": home_code,
        "away_team": away_code,
        "home_starter_id": game["home_starter_id"],
        "away_starter_id": game["away_starter_id"],
        "home_starter_name": game.get("home_starter_name"),
        "away_starter_name": game.get("away_starter_name"),
        "home_prior_starts": hs["prior_starts"],
        "away_prior_starts": ass["prior_starts"],
        "feature_count": len(features),
        "strict_prior_date_only": True,
        "same_day_results_used": False,
        "state_results_through": state.get("results_through"),
        "state_refresh": refresh_meta,
        "can_execute": False,
    }
    enrichment["hydration_profile"] = "MLB_MONEYLINE_V2_ROLLING"
    enrichment["sample_size"] = min(len(hh), len(ah))
    enrichment["probable_starter_verified"] = True
    # Do not overstate a probable starter as a confirmed lineup lock.  Existing
    # uncertainty logic may widen the interval until a stronger confirmation arrives.
    enrichment.setdefault("starter_confirmed", False)
    return enrichment


def reset_state_cache_for_tests() -> None:
    global _state_cache
    with _state_lock:
        _state_cache = None
