"""
gate_engine/balldontlie/nba_wnba.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

NBA and WNBA acquisition adapters for BallDontLie.

Provides:
  fetch_player_package(player_id, sport, season, n_games)
    → BDLPlayerPackage with full game log, box scores, injuries, odds/props

Capability-aware:
  - Always queries /v1/stats or /wnba/v1/stats for game logs (FREE+ tier)
  - Queries /v1/player_injuries (ALL_STAR+ tier only)
  - Queries odds/props endpoints only when tier exposes them
  - Never assumes a field exists because another sport or tier exposes it

Null fields are preserved exactly — never imputed.
Season averages are available separately but NEVER used as game logs.

can_execute=False unconditional.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

from gate_engine.balldontlie.client import (
    BDL_NBA_BASE,
    BDL_WNBA_BASE,
    _get,
    fetch_all,
    detect_tier,
    endpoint_available_for_tier,
    credentials_available,
    _now_utc,
)
from gate_engine.balldontlie.types import (
    BDLPlayerPackage,
    BDLProvenance,
    BDLStatus,
    BDLTier,
    BDL_SOURCE_NAME,
    BDL_SOURCE_GRADE,
    BDL_SOURCE_TYPE,
)
from gate_engine.balldontlie.normalizer import normalize_nba_wnba_row

_STATS_ENDPOINT = {
    "NBA":  f"{BDL_NBA_BASE}/stats",
    "WNBA": f"{BDL_WNBA_BASE}/stats",
}
_PLAYERS_ENDPOINT = {
    "NBA":  f"{BDL_NBA_BASE}/players",
    "WNBA": f"{BDL_WNBA_BASE}/players",
}
_INJURY_ENDPOINT_NBA = f"{BDL_NBA_BASE}/player_injuries"
_SEASON_AVG_ENDPOINT = {
    "NBA":  f"{BDL_NBA_BASE}/season_averages",
    "WNBA": f"{BDL_WNBA_BASE}/season_averages",
}


def fetch_player_package(
    player_id:  str,
    sport:      str,
    season:     int | None = None,
    n_games:    int        = 15,
    target_date: str | None = None,
) -> BDLPlayerPackage:
    """
    Fetch a complete BDL player data package for one NBA/WNBA player.

    Parameters
    ----------
    player_id   : BDL player ID string
    sport       : "NBA" | "WNBA"
    season      : season year (defaults to current based on target_date or today)
    n_games     : how many games of history to request
    target_date : ISO date string for season inference

    Returns BDLPlayerPackage — never raises.
    """
    sport_upper = sport.upper().strip()
    if sport_upper not in ("NBA", "WNBA"):
        return BDLPlayerPackage(
            player_id          = player_id,
            sport              = sport_upper,
            acquisition_status = BDLStatus.DATA_UNOBTAINABLE,
            notes              = [f"unsupported_sport:{sport_upper}"],
        )

    if not credentials_available():
        return BDLPlayerPackage(
            player_id          = player_id,
            sport              = sport_upper,
            acquisition_status = BDLStatus.AUTH_REQUIRED,
            notes              = ["balldontlie_secret_not_set"],
        )

    retrieved_at = _now_utc()
    tier         = detect_tier()
    stats_url    = _STATS_ENDPOINT[sport_upper]
    per_page     = min(n_games + 5, 25)

    if season is None:
        date_str = target_date or datetime.now(timezone.utc).date().isoformat()
        try:
            season = int(date_str[:4])
        except (ValueError, AttributeError):
            season = datetime.now(timezone.utc).year

    # ── Game stats ────────────────────────────────────────────────────────────
    stats_resp = _get(stats_url, {
        "player_ids[]": player_id,
        "seasons[]":    season,
        "per_page":     per_page,
    })

    if stats_resp.auth_blocked:
        return BDLPlayerPackage(
            player_id=player_id, sport=sport_upper,
            acquisition_status=BDLStatus.AUTH_FAILED,
            notes=[f"auth_failed:{stats_resp.status}"],
        )
    if stats_resp.tier_blocked:
        return BDLPlayerPackage(
            player_id=player_id, sport=sport_upper,
            acquisition_status=BDLStatus.NOT_IN_TIER,
            notes=["endpoint_not_in_active_tier"],
        )
    if stats_resp.status == BDLStatus.RATE_LIMITED:
        return BDLPlayerPackage(
            player_id=player_id, sport=sport_upper,
            acquisition_status=BDLStatus.RATE_LIMITED,
            notes=["rate_limited_429"],
        )

    notes: list[str] = [f"tier_detected:{tier}"]

    if not stats_resp.ok or not stats_resp.data:
        status = BDLStatus.NO_DATA if stats_resp.ok else stats_resp.status
        return BDLPlayerPackage(
            player_id=player_id, sport=sport_upper,
            acquisition_status=status,
            notes=notes + [f"stats_status:{stats_resp.status}"],
        )

    # Normalize and sort chronologically (oldest first for correct L5/L10 indexing)
    raw_rows = stats_resp.data
    raw_rows.sort(key=lambda r: (r.get("game") or {}).get("date") or "", reverse=False)

    game_rows = []
    for raw in raw_rows:
        row = normalize_nba_wnba_row(
            raw_row      = raw,
            sport        = sport_upper,
            bdl_tier     = tier,
            retrieved_at = retrieved_at,
            endpoint     = stats_url,
        )
        game_rows.append(row)

    notes.append(f"game_rows_normalized:{len(game_rows)}")

    # ── Injuries (ALL_STAR+ tier) ─────────────────────────────────────────────
    injuries: list[dict[str, Any]] = []
    if sport_upper == "NBA" and endpoint_available_for_tier(BDLTier.ALL_STAR):
        inj_resp = _get(_INJURY_ENDPOINT_NBA, {
            "player_ids[]": player_id,
            "per_page": 5,
        })
        if inj_resp.ok:
            injuries = inj_resp.data
            notes.append(f"injuries_fetched:{len(injuries)}")
        else:
            notes.append(f"injuries_status:{inj_resp.status}")
    else:
        notes.append("injuries:tier_not_available")

    # ── Odds / player props (ALL_STAR+ tier) ──────────────────────────────────
    odds_props: list[dict[str, Any]] = []
    # Props endpoint is tier-gated; probe before calling
    props_url = f"{BDL_NBA_BASE}/player_props"
    if endpoint_available_for_tier(BDLTier.ALL_STAR):
        props_resp = _get(props_url, {
            "player_ids[]": player_id,
            "per_page": 10,
        })
        if props_resp.ok:
            odds_props = props_resp.data
            notes.append(f"props_fetched:{len(odds_props)}")
        else:
            notes.append(f"props_status:{props_resp.status}")
    else:
        notes.append("props:tier_not_available")

    # Season averages are fetched separately but flagged as NOT for game_log use
    season_averages: dict[str, Any] = {}
    avg_resp = _get(_SEASON_AVG_ENDPOINT[sport_upper], {
        "player_ids[]": player_id,
        "season": season,
    })
    if avg_resp.ok and avg_resp.data:
        season_averages = avg_resp.data[0]
        season_averages["_NOTE"] = (
            "SEASON_AVERAGES — must NOT be used as game log values "
            "or placed in wow_game_log/box_score_log"
        )
        notes.append("season_averages_fetched")

    prov = BDLProvenance(
        source            = BDL_SOURCE_NAME,
        source_type       = BDL_SOURCE_TYPE,
        source_grade      = BDL_SOURCE_GRADE,
        endpoint          = stats_url,
        sport             = sport_upper,
        player_id         = str(player_id),
        retrieved_at      = retrieved_at,
        bdl_tier_detected = tier,
        acquisition_status = BDLStatus.OK,
        acquisition_notes  = notes,
    )

    return BDLPlayerPackage(
        player_id          = str(player_id),
        sport              = sport_upper,
        acquisition_status = BDLStatus.OK,
        game_rows          = game_rows,
        season_averages    = season_averages,
        injuries           = injuries,
        odds_props         = odds_props,
        provenance         = prov,
        notes              = notes,
    )


def search_player(
    name:  str,
    sport: str,
) -> tuple[str | None, bool | None, str]:
    """
    Search for a player by name in BDL.

    Returns (player_id, is_active, status_label).
    Never raises.
    """
    sport_upper = sport.upper().strip()
    base = BDL_WNBA_BASE if sport_upper == "WNBA" else BDL_NBA_BASE
    url  = f"{base}/players"

    if not credentials_available():
        return None, None, BDLStatus.AUTH_REQUIRED

    resp = _get(url, {"search": name, "per_page": 5})
    if not resp.ok:
        return None, None, resp.status

    players = resp.data
    if not players:
        return None, None, "not-found"

    # Best name match
    name_lower = name.lower()
    best = players[0]
    for p in players:
        fn = (p.get("first_name") or "").lower()
        ln = (p.get("last_name") or "").lower()
        full = f"{fn} {ln}".strip()
        if name_lower in full or full in name_lower:
            best = p
            break

    pid    = str(best.get("id") or "")
    active = best.get("is_active")
    if active is None:
        active = str(best.get("status", "")).lower() in ("active", "")
    return pid, bool(active), BDLStatus.OK
