"""
WOW-PATCH-2026-08-16-MONEYLINE-TEAM-ACQUISITION
WOW-PATCH-2026-08-17-WNBA-TENNIS-ML-LANES (extends this module)

Acquires auditable non-market team probability components for supported
MONEYLINE_V1 sports before the row reaches the independence gate.

Supported sports
----------------
NBA   — BallDontLie /v1/standings (season win-percentage)
MLB   — MLB Stats API /api/v1/standings (public, no key required)
WNBA  — BallDontLie /wnba/v1/standings + row-derived fields (WNBA_ML_V1)
ATP/WTA/TENNIS — row-derived fields + ESPN best-effort (TENNIS_MATCH_WINNER_V1)

Hydration profiles
------------------
NBA/MLB    → MONEYLINE_V1 (generic team-data acquisition)
WNBA       → WNBA_ML_V1  (never touches player-prop game_log/box_score_log)
ATP/WTA    → TENNIS_MATCH_WINNER_V1

Important: The WNBA_Enrichment_Key_Contract (game_log / box_score_log) is
scoped to WNBA player-prop rows ONLY. This module MUST NOT read or write
those fields for WNBA moneyline rows.

Output fields (populated in enrichment dict)
--------------------------------------------
home_win_pct    float  Season win-percentage for the home team (0.0–1.0)
away_win_pct    float  Season win-percentage for the away team (0.0–1.0)
home_power      float  = home_win_pct (sport_model.py power-rating alias)
away_power      float  = away_win_pct
team_acq_source str    Data source identifier for audit trail
hydration_profile str  Profile ID consumed by pipeline typed-failure reporter

Invariants
----------
- can_execute = False
- Never derives probability from sportsbook odds
- WNBA_ML path never reads game_log or box_score_log
- Fail-closed: exceptions return None; caller emits explicit blocker
- Partial acquisition is honest — returns data even when some fields missing
- Timeout: 5 seconds per HTTP call; returns None on timeout
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import json
from datetime import datetime, timezone
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

can_execute = False
PATCH_ID    = "WOW-PATCH-2026-08-16-MONEYLINE-TEAM-ACQUISITION"

_HTTP_TIMEOUT = 5  # seconds

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def acquire_team_data(
    row: dict[str, Any],
    sport: str,
) -> dict[str, float | str] | None:
    """
    Fetch non-market team data for a moneyline row.

    Dispatches to a sport-specific acquisition function — each profile is
    separate so today's schema omission does not become tomorrow's schema
    collision when new sports are added.

    NBA/MLB → MONEYLINE_V1 generic acquisition (home/away_win_pct, power)
    WNBA    → WNBA_ML_V1 acquisition (BDL WNBA standings + row-derived)
    ATP/WTA/TENNIS → TENNIS_MATCH_WINNER_V1 (row-derived + ESPN best-effort)

    Returns a dict with acquisition data or None when unavailable.
    NEVER touches game_log / box_score_log for WNBA rows.
    """
    sport = sport.upper()
    team     = (row.get("team") or row.get("player") or "").strip()
    opponent = (row.get("opponent") or "").strip()

    if not team or not opponent:
        logger.debug("team_acquisition: team or opponent missing — skipping")
        return None

    if sport == "NBA":
        return _acquire_nba(team, opponent)
    if sport == "MLB":
        return _acquire_mlb(team, opponent)
    if sport == "WNBA":
        return _acquire_wnba_ml(team, opponent, row)
    if sport in ("ATP", "WTA", "TENNIS"):
        return _acquire_tennis_match(team, opponent, row)

    # Unsupported sport — caller handles
    return None


# ---------------------------------------------------------------------------
# NBA — BallDontLie /v1/standings
# ---------------------------------------------------------------------------

def _acquire_nba(team: str, opponent: str) -> dict | None:
    try:
        from gate_engine.balldontlie.client import fetch_all as _bdl_fetch_all
        resp = _bdl_fetch_all(
            "https://api.balldontlie.io/v1/standings",
            params={"season": _current_nba_season()},
            max_pages=1,
        )
        if not resp.ok or not resp.data:
            return None

        standings: dict[str, float] = {}  # abbr/name → win_pct
        for entry in resp.data:
            t = entry.get("team") or {}
            name  = (t.get("full_name") or t.get("name") or "").lower()
            abbr  = (t.get("abbreviation") or "").upper()
            wins  = int(entry.get("wins") or 0)
            losses = int(entry.get("losses") or 0)
            total = wins + losses
            if total == 0:
                continue
            wp = wins / total
            if name:
                standings[name] = wp
            if abbr:
                standings[abbr] = wp

        home_wp = _fuzzy_lookup(team, standings)
        away_wp = _fuzzy_lookup(opponent, standings)

        if home_wp is None or away_wp is None:
            logger.debug(
                "nba_team_acq: no standings match for team=%r opp=%r", team, opponent
            )
            return None

        return {
            "home_win_pct":    home_wp,
            "away_win_pct":    away_wp,
            "home_power":      home_wp,
            "away_power":      away_wp,
            "team_acq_source": "balldontlie_nba_standings",
        }
    except Exception as exc:
        logger.warning("NBA team acquisition failed: %s", exc)
        return None


def _current_nba_season() -> int:
    """Return start year of current/most recent NBA season."""
    import datetime
    today = datetime.date.today()
    # NBA season starts in October; season year = calendar year of season start
    return today.year if today.month >= 10 else today.year - 1


# ---------------------------------------------------------------------------
# MLB — MLB Stats API (public, no key)
# ---------------------------------------------------------------------------

_MLB_STANDINGS_URL = (
    "https://statsapi.mlb.com/api/v1/standings"
    "?leagueId=103,104&hydrate=team&season={season}&standingsTypes=regularSeason"
)


def _acquire_mlb(team: str, opponent: str) -> dict | None:
    try:
        import datetime
        season = datetime.date.today().year
        url    = _MLB_STANDINGS_URL.format(season=season)
        data   = _http_get_json(url)
        if not data:
            return None

        standings: dict[str, float] = {}
        for record in data.get("records") or []:
            for entry in record.get("teamRecords") or []:
                t    = entry.get("team") or {}
                name = (t.get("name") or "").lower()
                abbr = (t.get("abbreviationOrCode") or t.get("abbreviation") or "").upper()
                wins   = int((entry.get("wins") or 0))
                losses = int((entry.get("losses") or 0))
                total  = wins + losses
                if total == 0:
                    continue
                wp = wins / total
                if name:
                    standings[name] = wp
                if abbr:
                    standings[abbr] = wp

        home_wp = _fuzzy_lookup(team, standings)
        away_wp = _fuzzy_lookup(opponent, standings)

        if home_wp is None or away_wp is None:
            logger.debug(
                "mlb_team_acq: no standings match for team=%r opp=%r", team, opponent
            )
            return None

        return {
            "home_win_pct":    home_wp,
            "away_win_pct":    away_wp,
            "home_power":      home_wp,
            "away_power":      away_wp,
            "team_acq_source": "mlb_stats_api_standings",
        }
    except Exception as exc:
        logger.warning("MLB team acquisition failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# WNBA Moneyline — WNBA_ML_V1 hydration profile
# ---------------------------------------------------------------------------

# Fields the WNBA_ML_V1 specialist needs for a full independent probability.
# The structured failure reporter uses this list to compute missing_fields[].
WNBA_ML_V1_REQUIRED_FIELDS: tuple[str, ...] = (
    "home_win_pct", "away_win_pct",
    "offensive_rating", "defensive_rating",
    "pace", "rest_days",
)


class WnbaMoneylineHydration(TypedDict, total=False):
    """Structured WNBA_ML_V1 acquisition outcome.

    This is deliberately an enrichment-compatible mapping so existing callers
    can persist its provenance unchanged.  ``hydration_status`` is the typed
    availability discriminator: only ``ACQUIRED`` carries every required
    model input; ``UNAVAILABLE`` carries no partial model data.
    """

    hydration_profile: str
    hydration_status: str
    eligible_for_model: bool
    retryable: bool
    unavailable_reason: str
    missing_fields: list[str]
    team_acq_source: str
    team_acq_retrieved_at: str
    data_timestamp: str
    home_win_pct: float
    away_win_pct: float
    home_power: float
    away_power: float
    offensive_rating: float
    defensive_rating: float
    pace: float
    rest_days: float


def _wnba_numeric(value: Any, *, minimum: float, maximum: float | None = None) -> float | None:
    """Parse one required WNBA model input without coercing booleans."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < minimum or (maximum is not None and parsed > maximum):
        return None
    return parsed


def _wnba_unavailable(
    *,
    source: str,
    retrieved_at: str,
    missing_fields: list[str],
    reason: str,
) -> WnbaMoneylineHydration:
    """Return the explicit fail-closed outcome for incomplete WNBA hydration."""
    return {
        "hydration_profile": "WNBA_ML_V1",
        "hydration_status": "UNAVAILABLE",
        "eligible_for_model": False,
        "retryable": True,
        "unavailable_reason": reason,
        "missing_fields": sorted(set(missing_fields)),
        "team_acq_source": source,
        "team_acq_retrieved_at": retrieved_at,
        "data_timestamp": retrieved_at,
    }


def validate_wnba_ml_hydration(
    candidate: dict[str, Any] | None,
    *,
    source: str | None = None,
    retrieved_at: str | None = None,
) -> WnbaMoneylineHydration:
    """Normalize one supplied or acquired WNBA_ML_V1 packet fail-closed."""
    candidate = candidate if isinstance(candidate, dict) else {}
    resolved_source = str(source or candidate.get("team_acq_source") or "").strip()
    resolved_retrieved_at = str(
        retrieved_at
        or candidate.get("team_acq_retrieved_at")
        or candidate.get("data_timestamp")
        or ""
    ).strip()
    if candidate.get("hydration_status") not in (None, "ACQUIRED"):
        return _wnba_unavailable(
            source=resolved_source or "wnba_ml_v1:declared_unavailable",
            retrieved_at=resolved_retrieved_at,
            missing_fields=list(WNBA_ML_V1_REQUIRED_FIELDS),
            reason=str(candidate.get("unavailable_reason") or "WNBA_ML_V1_DECLARED_UNAVAILABLE"),
        )

    validators = {
        "home_win_pct": lambda value: _wnba_numeric(value, minimum=0.0, maximum=1.0),
        "away_win_pct": lambda value: _wnba_numeric(value, minimum=0.0, maximum=1.0),
        "offensive_rating": lambda value: _wnba_numeric(value, minimum=0.0),
        "defensive_rating": lambda value: _wnba_numeric(value, minimum=0.0),
        "pace": lambda value: _wnba_numeric(value, minimum=0.0),
        "rest_days": lambda value: _wnba_numeric(value, minimum=0.0),
    }
    normalized: dict[str, float] = {}
    missing_fields: list[str] = []
    for field, validator in validators.items():
        value = validator(candidate.get(field))
        if value is None:
            missing_fields.append(field)
        else:
            normalized[field] = value
    if not resolved_source:
        missing_fields.append("team_acq_source")
    if not resolved_retrieved_at:
        missing_fields.append("team_acq_retrieved_at")
    if missing_fields:
        return _wnba_unavailable(
            source=resolved_source or "wnba_ml_v1:provenance_unavailable",
            retrieved_at=resolved_retrieved_at,
            missing_fields=missing_fields,
            reason="WNBA_ML_V1_REQUIRED_INPUTS_UNAVAILABLE",
        )
    return {
        "hydration_profile": "WNBA_ML_V1",
        "hydration_status": "ACQUIRED",
        "eligible_for_model": True,
        "retryable": False,
        "team_acq_source": resolved_source,
        "team_acq_retrieved_at": resolved_retrieved_at,
        "data_timestamp": resolved_retrieved_at,
        **normalized,
        "home_power": normalized["home_win_pct"],
        "away_power": normalized["away_win_pct"],
    }

# Fields the TENNIS_MATCH_WINNER_V1 specialist needs.
TENNIS_ML_V1_REQUIRED_FIELDS: tuple[str, ...] = (
    "surface", "surface_adjusted_form",
    "hold_rate", "break_rate",
    "service_points_won", "return_points_won",
)


def _current_wnba_season() -> int:
    """Return current WNBA season year (starts May)."""
    import datetime
    today = datetime.date.today()
    return today.year if today.month >= 5 else today.year - 1


def _acquire_wnba_ml(
    team: str,
    opponent: str,
    row: dict[str, Any],
) -> WnbaMoneylineHydration:
    """
    WNBA_ML_V1 hydration profile.

    Sources (in order):
      1. BallDontLie WNBA standings → home_win_pct, away_win_pct
      2. Row-derived fields → rest_days, home_away, pace, offensive/defensive
         rating, and other enrichment fields already supplied by GPT.

    HARD CONSTRAINT: never reads or writes game_log / box_score_log.
    Those keys belong to the WNBA_Enrichment_Key_Contract (player-prop scope).
    """
    retrieved_at = datetime.now(timezone.utc).isoformat()
    result: dict[str, Any] = {}
    source = "wnba_ml_v1:bdl_standings_unavailable"

    # Step 1: BallDontLie WNBA standings → win_pct
    try:
        from gate_engine.balldontlie.client import fetch_all as _bdl_fetch_all
        resp = _bdl_fetch_all(
            "https://api.balldontlie.io/wnba/v1/standings",
            params={"season": _current_wnba_season()},
            max_pages=1,
        )
        if resp.ok and resp.data:
            standings: dict[str, float] = {}
            for entry in resp.data:
                t     = entry.get("team") or {}
                name  = (t.get("full_name") or t.get("name") or "").lower()
                abbr  = (t.get("abbreviation") or "").upper()
                wins   = int(entry.get("wins") or 0)
                losses = int(entry.get("losses") or 0)
                total  = wins + losses
                if total == 0:
                    continue
                wp = wins / total
                if name:
                    standings[name] = wp
                if abbr:
                    standings[abbr] = wp
            home_wp = _fuzzy_lookup(team, standings)
            away_wp = _fuzzy_lookup(opponent, standings)
            if home_wp is not None:
                result["home_win_pct"] = home_wp
            if away_wp is not None:
                result["away_win_pct"] = away_wp
            source = "wnba_ml_v1:bdl_wnba_standings"
        else:
            source = "wnba_ml_v1:bdl_standings_unavailable"
    except Exception as exc:
        logger.warning("WNBA_ML_V1 BDL standings failed: %s", exc)
        source = "wnba_ml_v1:bdl_error"

    # Step 2: Row-derived enrichment fields (read-only; never fabricate)
    # Explicitly excludes game_log / box_score_log (player-prop contract).
    _PLAYER_PROP_FORBIDDEN = frozenset({"game_log", "box_score_log"})
    _WNBA_ML_ROW_FIELDS = (
        "rest_days", "home_away", "travel_state", "pace",
        "offensive_rating", "defensive_rating",
        "matchup_features", "blowout_script_inputs",
        "foul_trouble_rotation_risk", "injury_report",
        "projected_or_confirmed_starters", "expected_rotation",
        "team_minutes_usage_state",
    )
    for field in _WNBA_ML_ROW_FIELDS:
        if field in _PLAYER_PROP_FORBIDDEN:
            continue  # paranoia guard
        val = row.get(field)
        if val is not None:
            result[field] = val

    # A caller may supply a complete non-market standings pair only when the
    # provider did not resolve it.  Never borrow a market-implied probability
    # and never synthesize the opposing side.
    for field in ("home_win_pct", "away_win_pct"):
        if field not in result and row.get(field) is not None:
            result[field] = row.get(field)
            if source != "wnba_ml_v1:bdl_wnba_standings":
                source = "wnba_ml_v1:row_validated_non_market_inputs"

    return validate_wnba_ml_hydration(
        result,
        source=source,
        retrieved_at=retrieved_at,
    )


# ---------------------------------------------------------------------------
# Tennis Match Winner — TENNIS_MATCH_WINNER_V1 hydration profile
# ---------------------------------------------------------------------------

def _acquire_tennis_match(
    team: str,
    opponent: str,  # noqa: ARG001
    row: dict[str, Any],
) -> dict | None:
    """
    TENNIS_MATCH_WINNER_V1 hydration profile.

    Sources (in order):
      1. Row-supplied fields (surface, best_of_format, hold/break rates, etc.)
      2. Surface inference from event name / venue strings
      3. ESPN athlete API (best-effort; fail-closed)

    Tennis rows use team=player_name, opponent=opponent_player_name.
    No player-prop contract fields are involved.
    """
    result: dict[str, Any] = {
        "hydration_profile": "TENNIS_MATCH_WINNER_V1",
    }

    # Step 1: Row-supplied non-market fields
    _TENNIS_ROW_FIELDS = (
        "surface", "best_of_format",
        "surface_adjusted_form", "hold_rate", "break_rate",
        "service_points_won", "return_points_won",
        "recent_opponent_quality", "fatigue",
        "travel", "recent_match_load", "injury_fitness_status",
        "retirement_risk", "home_elo", "away_elo",
        "rest_days", "h2h_win_rate", "h2h_win_pct",
    )
    for field in _TENNIS_ROW_FIELDS:
        val = row.get(field)
        if val is not None:
            result[field] = val

    # Step 2: Surface inference from event name / venue (fallback)
    if "surface" not in result:
        for src in ("event", "event_name", "venue", "tournament"):
            raw = (row.get(src) or "").lower()
            if "clay" in raw:
                result["surface"] = "clay"
                break
            if "grass" in raw or "wimbledon" in raw:
                result["surface"] = "grass"
                break
            if "hard" in raw or "open" in raw or "indoor" in raw:
                result["surface"] = "hard"
                break

    # Step 3: ESPN athlete stats (best-effort; fail-closed)
    if team:
        try:
            espn_stats = _fetch_tennis_player_stats_espn(team)
            if espn_stats:
                for k, v in espn_stats.items():
                    result.setdefault(k, v)   # row fields take precedence
        except Exception as exc:
            logger.debug("TENNIS_ML_V1 ESPN attempt failed for %r: %s", team, exc)

    result.setdefault("team_acq_source", "tennis_match_winner_v1:row_derived+espn_attempt")

    has_model_data = any(
        k in result
        for k in ("surface", "hold_rate", "home_elo", "surface_adjusted_form",
                  "h2h_win_rate", "h2h_win_pct")
    )
    return result if has_model_data else None


def _fetch_tennis_player_stats_espn(player_name: str) -> dict | None:
    """
    Best-effort ESPN API lookup for tennis player hold / break rates.
    Returns None on any failure; never raises.
    """
    try:
        import urllib.parse as _up
        name_enc = _up.quote(player_name)
        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/tennis/"
            f"atp/athletes?search={name_enc}&limit=3"
        )
        data = _http_get_json(url)
        if not data:
            return None
        athletes = data.get("athletes") or []
        if not athletes:
            return None
        stats = (athletes[0].get("statistics") or {})
        result: dict[str, Any] = {}
        if stats.get("firstServePct"):
            try:
                result["service_points_won"] = float(stats["firstServePct"]) / 100.0
            except (TypeError, ValueError):
                pass
        if stats.get("breakPointsSaved"):
            try:
                result["hold_rate"] = float(stats["breakPointsSaved"]) / 100.0
            except (TypeError, ValueError):
                pass
        return result or None
    except Exception as exc:
        logger.debug("ESPN tennis lookup failed for %r: %s", player_name, exc)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fuzzy_lookup(query: str, standings: dict[str, float]) -> float | None:
    """
    Look up team win-pct by exact abbreviation or substring name match.
    Returns None when no match found.
    """
    q = query.strip().lower()
    if not q:
        return None
    # Exact key match (case-insensitive)
    if q in standings:
        return standings[q]
    q_upper = q.upper()
    if q_upper in standings:
        return standings[q_upper]
    # Abbreviation match: last 3 uppercase chars of query
    abbr = q_upper[-3:] if len(q_upper) >= 3 else q_upper
    if abbr in standings:
        return standings[abbr]
    # Substring: query contains a known name key
    for key, wp in standings.items():
        if key in q or q in key:
            return wp
    return None


def _http_get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "WOW-ScoringEngine/1.0"}, method="GET"
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("_http_get_json %s failed: %s", url, exc)
        return None
