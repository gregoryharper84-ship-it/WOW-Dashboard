"""
gate_engine/balldontlie/mlb.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

MLB acquisition adapters for BallDontLie.

Provides:
  fetch_pitcher_package(player_id, season, n_games)  — pitching workload history
  fetch_batter_package(player_id, season, n_games)   — batting game logs
  fetch_game_lineups(game_id)                        — confirmed/probable lineups
  fetch_team_injuries(team_id)                       — MLB team injuries

GOAT pitch data (velocity, zone_rate, chase_rate, whiff_rate, contact_rate,
pitch_mix, xwOBA) is fetched only when the GOAT tier endpoint is available.
GOAT metrics are structured ML specialist inputs — they do NOT replace official
starter confirmation, weather, manager-leash context, bullpen availability, or
other required WOW evidence.

Outs → IP conversion: ip = (full_innings) + (outs_remainder / 10)
e.g. 7 outs = 2.1 IP (2 full innings + 1 out)

can_execute=False unconditional.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

from gate_engine.balldontlie.client import (
    BDL_MLB_BASE,
    _get,
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
from gate_engine.balldontlie.normalizer import (
    normalize_mlb_pitching_row,
    normalize_mlb_batting_row,
)

_STATS_URL    = f"{BDL_MLB_BASE}/stats"
_PLAYERS_URL  = f"{BDL_MLB_BASE}/players"
_GAMES_URL    = f"{BDL_MLB_BASE}/games"
_LINEUPS_URL  = f"{BDL_MLB_BASE}/game_lineups"
_INJURIES_URL = f"{BDL_MLB_BASE}/player_injuries"
_GOAT_URL     = f"{BDL_MLB_BASE}/game_innings"    # GOAT tier endpoint


def fetch_pitcher_package(
    player_id:    str,
    season:       int | None = None,
    n_games:      int        = 10,
    target_date:  str | None = None,
    include_goat: bool       = True,
) -> BDLPlayerPackage:
    """
    Fetch MLB pitching workload history for one pitcher.

    Populates BDLGameRow with: outs_recorded, ip, batters_faced, k, bb, h,
    er, pitch_count, and (if GOAT tier) GOAT pitch metrics.

    Returns BDLPlayerPackage — never raises.
    """
    if not credentials_available():
        return BDLPlayerPackage(
            player_id=player_id, sport="MLB",
            acquisition_status=BDLStatus.AUTH_REQUIRED,
            notes=["balldontlie_secret_not_set"],
        )

    retrieved_at = _now_utc()
    tier         = detect_tier()
    notes: list[str] = [f"tier_detected:{tier}"]

    if season is None:
        date_str = target_date or datetime.now(timezone.utc).date().isoformat()
        try:
            season = int(date_str[:4])
        except (ValueError, AttributeError):
            season = datetime.now(timezone.utc).year

    # ── Game-level pitching stats ─────────────────────────────────────────────
    stats_resp = _get(_STATS_URL, {
        "player_ids[]": player_id,
        "seasons[]":    season,
        "type":         "pitching",
        "per_page":     min(n_games + 5, 25),
    })

    if stats_resp.auth_blocked:
        return BDLPlayerPackage(player_id=player_id, sport="MLB",
                                acquisition_status=BDLStatus.AUTH_FAILED,
                                notes=[f"auth_failed:{stats_resp.status}"])
    if stats_resp.status == BDLStatus.RATE_LIMITED:
        return BDLPlayerPackage(player_id=player_id, sport="MLB",
                                acquisition_status=BDLStatus.RATE_LIMITED,
                                notes=["rate_limited"])

    if not stats_resp.ok or not stats_resp.data:
        return BDLPlayerPackage(player_id=player_id, sport="MLB",
                                acquisition_status=stats_resp.status or BDLStatus.NO_DATA,
                                notes=notes + [f"stats_status:{stats_resp.status}"])

    # Sort chronologically (oldest first)
    raw_rows = stats_resp.data
    raw_rows.sort(key=lambda r: (r.get("game") or {}).get("date") or "", reverse=False)

    game_rows = []
    for raw in raw_rows:
        row = normalize_mlb_pitching_row(
            raw_row      = raw,
            bdl_tier     = tier,
            retrieved_at = retrieved_at,
            endpoint     = _STATS_URL,
        )
        game_rows.append(row)

    notes.append(f"pitching_rows_normalized:{len(game_rows)}")

    # ── GOAT pitch data — enrich existing rows when tier is GOAT ─────────────
    if include_goat and endpoint_available_for_tier(BDLTier.GOAT):
        _enrich_goat_data(game_rows, player_id, season, retrieved_at)
        notes.append("goat_pitch_data_enriched")
    else:
        notes.append("goat_pitch_data:tier_not_available")

    prov = BDLProvenance(
        source            = BDL_SOURCE_NAME,
        source_type       = BDL_SOURCE_TYPE,
        source_grade      = BDL_SOURCE_GRADE,
        endpoint          = _STATS_URL,
        sport             = "MLB",
        player_id         = str(player_id),
        retrieved_at      = retrieved_at,
        bdl_tier_detected = tier,
        acquisition_status = BDLStatus.OK,
        acquisition_notes  = notes,
    )

    return BDLPlayerPackage(
        player_id          = str(player_id),
        sport              = "MLB",
        acquisition_status = BDLStatus.OK,
        game_rows          = game_rows,
        provenance         = prov,
        notes              = notes,
    )


def fetch_batter_package(
    player_id:   str,
    season:      int | None = None,
    n_games:     int        = 10,
    target_date: str | None = None,
) -> BDLPlayerPackage:
    """
    Fetch MLB batting game logs for one batter.

    Returns BDLPlayerPackage — never raises.
    """
    if not credentials_available():
        return BDLPlayerPackage(
            player_id=player_id, sport="MLB",
            acquisition_status=BDLStatus.AUTH_REQUIRED,
            notes=["balldontlie_secret_not_set"],
        )

    retrieved_at = _now_utc()
    tier         = detect_tier()
    notes: list[str] = [f"tier_detected:{tier}"]

    if season is None:
        date_str = target_date or datetime.now(timezone.utc).date().isoformat()
        try:
            season = int(date_str[:4])
        except (ValueError, AttributeError):
            season = datetime.now(timezone.utc).year

    stats_resp = _get(_STATS_URL, {
        "player_ids[]": player_id,
        "seasons[]":    season,
        "type":         "hitting",
        "per_page":     min(n_games + 5, 25),
    })

    if not stats_resp.ok or not stats_resp.data:
        return BDLPlayerPackage(player_id=player_id, sport="MLB",
                                acquisition_status=stats_resp.status or BDLStatus.NO_DATA,
                                notes=notes + [f"stats_status:{stats_resp.status}"])

    raw_rows = stats_resp.data
    raw_rows.sort(key=lambda r: (r.get("game") or {}).get("date") or "", reverse=False)

    game_rows = [
        normalize_mlb_batting_row(r, bdl_tier=tier,
                                  retrieved_at=retrieved_at, endpoint=_STATS_URL)
        for r in raw_rows
    ]
    notes.append(f"batting_rows_normalized:{len(game_rows)}")

    prov = BDLProvenance(
        source=BDL_SOURCE_NAME, source_type=BDL_SOURCE_TYPE, source_grade=BDL_SOURCE_GRADE,
        endpoint=_STATS_URL, sport="MLB", player_id=str(player_id),
        retrieved_at=retrieved_at, bdl_tier_detected=tier,
        acquisition_status=BDLStatus.OK, acquisition_notes=notes,
    )
    return BDLPlayerPackage(
        player_id=str(player_id), sport="MLB",
        acquisition_status=BDLStatus.OK,
        game_rows=game_rows, provenance=prov, notes=notes,
    )


def fetch_game_lineups(game_id: str) -> dict[str, Any]:
    """
    Fetch confirmed/probable MLB lineups for a game.

    Returns a dict with home/away lineup lists and acquisition status.
    BDL lineups cannot override a stronger official contradiction — this
    data must be reconciled against official starter data before use.
    Returns empty dict with DATA_UNOBTAINABLE status on failure.
    """
    if not credentials_available():
        return {
            "acquisition_status": BDLStatus.AUTH_REQUIRED,
            "game_id": game_id,
            "note": "balldontlie_secret_not_set",
        }

    resp = _get(f"{_LINEUPS_URL}", {"game_id": game_id, "per_page": 50})
    if not resp.ok:
        return {
            "acquisition_status": resp.status,
            "game_id": game_id,
            "note": f"bdl_lineups_unavailable:{resp.status}",
        }

    return {
        "acquisition_status": BDLStatus.OK,
        "game_id": game_id,
        "lineups": resp.data,
        "note": (
            "BDL lineups are not authoritative — must be reconciled against "
            "official MLB starter/lineup confirmations before model entry"
        ),
        "retrieved_at": _now_utc(),
        "source": BDL_SOURCE_NAME,
        "source_grade": BDL_SOURCE_GRADE,
    }


def fetch_team_injuries(team_id: str) -> dict[str, Any]:
    """
    Fetch MLB team injury list (ALL_STAR+ tier).

    Returns structured injury data or DATA_UNOBTAINABLE.
    """
    if not credentials_available():
        return {"acquisition_status": BDLStatus.AUTH_REQUIRED, "team_id": team_id}

    if not endpoint_available_for_tier(BDLTier.ALL_STAR):
        return {
            "acquisition_status": BDLStatus.NOT_IN_TIER,
            "team_id": team_id,
            "note": "injuries_endpoint_requires_all_star_tier",
        }

    resp = _get(_INJURIES_URL, {"team_ids[]": team_id, "per_page": 25})
    if not resp.ok:
        return {"acquisition_status": resp.status, "team_id": team_id}

    return {
        "acquisition_status": BDLStatus.OK,
        "team_id": team_id,
        "injuries": resp.data,
        "retrieved_at": _now_utc(),
        "source": BDL_SOURCE_NAME,
    }


# ---------------------------------------------------------------------------
# Internal GOAT enrichment
# ---------------------------------------------------------------------------

def _enrich_goat_data(
    game_rows:   list,
    player_id:   str,
    season:      int,
    retrieved_at: str,
) -> None:
    """
    Try to enrich existing BDLGameRow objects with GOAT pitch metrics.
    Mutates in place. Called only when GOAT tier is confirmed available.
    """
    resp = _get(_GOAT_URL, {
        "player_ids[]": player_id,
        "seasons[]":    season,
        "per_page":     25,
    })
    if not resp.ok or not resp.data:
        return

    # Index GOAT rows by game_id for fast lookup
    goat_by_game: dict[str, dict] = {}
    for g in resp.data:
        gid = str((g.get("game") or {}).get("id") or g.get("game_id") or "")
        if gid:
            goat_by_game[gid] = g

    for row in game_rows:
        gid = row.provenance.game_id or ""
        if gid not in goat_by_game:
            continue
        g = goat_by_game[gid]
        # Only overwrite if the GOAT field is present and non-null
        if row.avg_velocity is None:
            row.avg_velocity = _safe_float(g.get("avg_velocity") or g.get("velocity"))
        if row.zone_rate is None:
            row.zone_rate = _safe_float(g.get("zone_rate") or g.get("z_pct"))
        if row.chase_rate is None:
            row.chase_rate = _safe_float(g.get("chase_rate") or g.get("chase_pct"))
        if row.whiff_rate is None:
            row.whiff_rate = _safe_float(g.get("whiff_rate") or g.get("whiff_pct"))
        if row.contact_rate is None:
            row.contact_rate = _safe_float(g.get("contact_rate"))
        if row.xwoba is None:
            row.xwoba = _safe_float(g.get("xwoba") or g.get("expected_woba"))
        if row.pitch_mix is None and isinstance(g.get("pitch_mix"), dict):
            row.pitch_mix = g["pitch_mix"]


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
