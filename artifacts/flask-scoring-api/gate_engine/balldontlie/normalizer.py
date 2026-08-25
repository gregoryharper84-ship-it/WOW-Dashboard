"""
gate_engine/balldontlie/normalizer.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

Normalizes BDL raw API rows → WOW canonical schemas.

WOW canonical schemas:
  game_log:      list[float]          — stat values, most recent first
  box_score_log: list[dict[str, Any]] — richer rows, most recent first

Rules:
  - Nulls are preserved exactly; never imputed
  - Season averages are NEVER placed in game logs
  - Each row must have game_date + player_id + game_id to be chronologically
    verified (rows without game identity are excluded from L5/L10)
  - DNP rows (min < 1 for NBA/WNBA) are excluded from L5/L10 values
  - Minutes are normalized from "MM:SS" or plain float strings

can_execute=False unconditional.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

from gate_engine.balldontlie.types import (
    BDLGameRow,
    BDLProvenance,
    BDLStatus,
    BDL_SOURCE_NAME,
    BDL_SOURCE_GRADE,
    BDL_SOURCE_TYPE,
    BDLTier,
)


# ---------------------------------------------------------------------------
# Minute normalizer
# ---------------------------------------------------------------------------

def _parse_minutes(raw: Any) -> float | None:
    """Normalize BDL 'min' field from "MM:SS", "M:SS", or plain float."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("0", "0:00", "00:00"):
        return 0.0
    if ":" in s:
        parts = s.split(":")
        try:
            return float(parts[0]) + float(parts[1]) / 60.0
        except (ValueError, IndexError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# NBA / WNBA row normalizer
# ---------------------------------------------------------------------------

def normalize_nba_wnba_row(
    raw_row:           dict[str, Any],
    sport:             str,
    bdl_tier:          str            = BDLTier.UNKNOWN,
    retrieved_at:      str            = "",
    endpoint:          str            = "",
) -> BDLGameRow:
    """
    Normalize one NBA/WNBA stats row from BDL into a BDLGameRow.

    Null fields are explicitly tracked in provenance.null_fields.
    DNP detection: min < 1.0 or min is None → is_dnp=True.
    """
    null_fields: list[str] = []
    game_obj  = raw_row.get("game") or {}
    player_obj = raw_row.get("player") or {}
    team_obj  = raw_row.get("team") or {}

    player_id   = str(player_obj.get("id") or raw_row.get("player_id") or "")
    player_name = " ".join(filter(None, [
        player_obj.get("first_name"), player_obj.get("last_name")
    ])) or None

    game_id   = str(game_obj.get("id") or raw_row.get("game_id") or "")
    game_date = game_obj.get("date") or raw_row.get("date") or None
    if game_date and "T" in game_date:
        game_date = game_date[:10]

    # Home/away from game object
    team_id     = str(team_obj.get("id") or "")
    home_team_id = str(game_obj.get("home_team_id") or game_obj.get("home_team", {}).get("id") or "")
    away_team_id = str(game_obj.get("visitor_team_id") or game_obj.get("away_team_id") or
                       game_obj.get("visitor_team", {}).get("id") or "")
    if team_id and home_team_id:
        home_away = "home" if team_id == home_team_id else "away"
    else:
        home_away = None

    # Opponent team name
    if home_away == "home":
        opp_obj = game_obj.get("visitor_team") or {}
    elif home_away == "away":
        opp_obj = game_obj.get("home_team") or {}
    else:
        opp_obj = {}
    opponent = opp_obj.get("full_name") or opp_obj.get("abbreviation") or None

    # Minutes
    min_val = _parse_minutes(raw_row.get("min") or raw_row.get("minutes"))
    is_dnp  = (min_val is None or min_val < 1.0)
    if min_val is None:
        null_fields.append("min")

    # Stat fields
    def _extract(key: str, bdl_key: str | None = None) -> float | None:
        k = bdl_key or key
        v = _safe_float(raw_row.get(k))
        if v is None:
            null_fields.append(key)
        return v

    pts   = _extract("pts")
    reb   = _extract("reb")
    ast   = _extract("ast")
    stl   = _extract("stl")
    blk   = _extract("blk")
    tov   = _extract("tov", "turnover")
    fga   = _extract("fga")
    fgm   = _extract("fgm")
    fg3a  = _extract("fg3a")
    fg3m  = _extract("fg3m")
    fta   = _extract("fta")
    ftm   = _extract("ftm")
    oreb  = _extract("oreb")
    dreb  = _extract("dreb")
    pf    = _extract("pf")

    # Advanced (tier-dependent — absent in FREE tier)
    usage_rate  = _safe_float(raw_row.get("usage_rate") or raw_row.get("usg_pct"))
    net_rating  = _safe_float(raw_row.get("net_rating") or raw_row.get("net_rtg"))
    off_rating  = _safe_float(raw_row.get("off_rating") or raw_row.get("off_rtg"))
    def_rating  = _safe_float(raw_row.get("def_rating") or raw_row.get("def_rtg"))

    season = _safe_int(game_obj.get("season") or raw_row.get("season"))

    prov = BDLProvenance(
        source            = BDL_SOURCE_NAME,
        source_type       = BDL_SOURCE_TYPE,
        source_grade      = BDL_SOURCE_GRADE,
        endpoint          = endpoint,
        sport             = sport.upper(),
        player_id         = player_id or None,
        player_name       = player_name,
        game_id           = game_id or None,
        team_id           = team_id or None,
        retrieved_at      = retrieved_at or datetime.now(timezone.utc).isoformat(),
        effective_date    = game_date,
        bdl_tier_detected = bdl_tier,
        null_fields       = list(set(null_fields)),
        acquisition_status = BDLStatus.OK,
    )

    return BDLGameRow(
        provenance    = prov,
        game_date     = game_date,
        season        = season,
        opponent_team = opponent,
        home_away     = home_away,
        is_dnp        = is_dnp,
        min           = min_val,
        pts=pts, reb=reb, ast=ast, stl=stl, blk=blk,
        tov=tov, fga=fga, fgm=fgm, fg3a=fg3a, fg3m=fg3m,
        fta=fta, ftm=ftm, oreb=oreb, dreb=dreb, pf=pf,
        usage_rate=usage_rate, net_rating=net_rating,
        off_rating=off_rating, def_rating=def_rating,
    )


# ---------------------------------------------------------------------------
# MLB pitching row normalizer
# ---------------------------------------------------------------------------

def normalize_mlb_pitching_row(
    raw_row:      dict[str, Any],
    bdl_tier:     str  = BDLTier.UNKNOWN,
    retrieved_at: str  = "",
    endpoint:     str  = "",
) -> BDLGameRow:
    """
    Normalize one MLB pitching game row from BDL into a BDLGameRow.

    IP is derived from outs_recorded / 3.
    GOAT pitch metrics are only extracted when present (GOAT tier).
    """
    null_fields: list[str] = []
    game_obj   = raw_row.get("game") or {}
    player_obj = raw_row.get("player") or {}
    team_obj   = raw_row.get("team") or {}

    player_id   = str(player_obj.get("id") or raw_row.get("player_id") or "")
    player_name = " ".join(filter(None, [
        player_obj.get("first_name"), player_obj.get("last_name")
    ])) or None

    game_id   = str(game_obj.get("id") or raw_row.get("game_id") or "")
    game_date = (game_obj.get("date") or raw_row.get("date") or "")
    if game_date and "T" in game_date:
        game_date = game_date[:10]
    season = _safe_int(game_obj.get("season") or raw_row.get("season"))

    # Innings pitched from outs_recorded (canonical BDL MLB field)
    outs_raw = (
        raw_row.get("outs_pitched") or raw_row.get("outs_recorded") or
        raw_row.get("outs") or raw_row.get("ip_outs")
    )
    outs_recorded = _safe_int(outs_raw)
    ip: float | None = None
    if outs_recorded is not None:
        # whole_innings.remainder format (e.g. 6.1 = 6 innings + 1 out)
        full_inn = outs_recorded // 3
        remainder = outs_recorded % 3
        ip = round(full_inn + remainder / 10, 1)   # WOW canonical IP format
    else:
        # Try direct IP field as fallback
        ip_raw = raw_row.get("ip") or raw_row.get("innings_pitched")
        ip = _safe_float(ip_raw)
        if ip is None:
            null_fields.append("ip")
            null_fields.append("outs_recorded")

    def _int_field(keys: list[str], canonical_name: str) -> int | None:
        for k in keys:
            v = _safe_int(raw_row.get(k))
            if v is not None:
                return v
        null_fields.append(canonical_name)
        return None

    def _float_field(keys: list[str], canonical_name: str) -> float | None:
        for k in keys:
            v = _safe_float(raw_row.get(k))
            if v is not None:
                return v
        null_fields.append(canonical_name)
        return None

    bf  = _int_field(["batters_faced", "bf"], "batters_faced")
    k   = _int_field(["strikeouts", "k", "so"], "k")
    bb  = _int_field(["walks", "bb", "base_on_balls"], "bb")
    h   = _int_field(["hits_allowed", "h", "hits"], "h")
    er  = _int_field(["earned_runs", "er"], "er")
    hr  = _int_field(["home_runs_allowed", "hr"], "hr")
    pc  = _int_field(["pitch_count", "pitches", "np"], "pitch_count")

    # GOAT tier pitch data — absent in lower tiers
    avg_velocity  = _safe_float(raw_row.get("avg_velocity") or raw_row.get("velocity"))
    zone_rate     = _safe_float(raw_row.get("zone_rate") or raw_row.get("z_pct"))
    chase_rate    = _safe_float(raw_row.get("chase_rate") or raw_row.get("chase_pct"))
    whiff_rate    = _safe_float(raw_row.get("whiff_rate") or raw_row.get("whiff_pct"))
    contact_rate  = _safe_float(raw_row.get("contact_rate") or raw_row.get("contact_pct"))
    xwoba         = _safe_float(raw_row.get("xwoba") or raw_row.get("expected_woba"))
    pitch_mix_raw = raw_row.get("pitch_mix") or raw_row.get("pitch_usage")
    pitch_mix: dict[str, float] | None = None
    if isinstance(pitch_mix_raw, dict):
        pitch_mix = {k2: float(v2) for k2, v2 in pitch_mix_raw.items()
                     if _safe_float(v2) is not None}

    is_dnp = (outs_recorded is None or outs_recorded == 0) and (ip is None or ip == 0.0)

    prov = BDLProvenance(
        source            = BDL_SOURCE_NAME,
        source_type       = BDL_SOURCE_TYPE,
        source_grade      = BDL_SOURCE_GRADE,
        endpoint          = endpoint,
        sport             = "MLB",
        player_id         = player_id or None,
        player_name       = player_name,
        game_id           = game_id or None,
        retrieved_at      = retrieved_at or datetime.now(timezone.utc).isoformat(),
        effective_date    = game_date or None,
        bdl_tier_detected = bdl_tier,
        null_fields       = list(set(null_fields)),
        acquisition_status = BDLStatus.OK,
    )

    return BDLGameRow(
        provenance    = prov,
        game_date     = game_date or None,
        season        = season,
        is_dnp        = is_dnp,
        outs_recorded = outs_recorded,
        ip            = ip,
        batters_faced = bf,
        k             = k,
        bb            = bb,
        h             = h,
        er            = er,
        hr            = hr,
        pitch_count   = pc,
        avg_velocity  = avg_velocity,
        zone_rate     = zone_rate,
        chase_rate    = chase_rate,
        whiff_rate    = whiff_rate,
        contact_rate  = contact_rate,
        xwoba         = xwoba,
        pitch_mix     = pitch_mix,
    )


# ---------------------------------------------------------------------------
# MLB batting row normalizer
# ---------------------------------------------------------------------------

def normalize_mlb_batting_row(
    raw_row:      dict[str, Any],
    bdl_tier:     str  = BDLTier.UNKNOWN,
    retrieved_at: str  = "",
    endpoint:     str  = "",
) -> BDLGameRow:
    """Normalize one MLB batting game row from BDL into a BDLGameRow."""
    null_fields: list[str] = []
    game_obj   = raw_row.get("game") or {}
    player_obj = raw_row.get("player") or {}

    player_id   = str(player_obj.get("id") or raw_row.get("player_id") or "")
    player_name = " ".join(filter(None, [
        player_obj.get("first_name"), player_obj.get("last_name")
    ])) or None
    game_id   = str(game_obj.get("id") or raw_row.get("game_id") or "")
    game_date = (game_obj.get("date") or raw_row.get("date") or "")
    if game_date and "T" in game_date:
        game_date = game_date[:10]
    season = _safe_int(game_obj.get("season") or raw_row.get("season"))

    def _i(keys: list[str], name: str) -> int | None:
        for k in keys:
            v = _safe_int(raw_row.get(k))
            if v is not None:
                return v
        null_fields.append(name)
        return None

    def _f(keys: list[str], name: str) -> float | None:
        for k in keys:
            v = _safe_float(raw_row.get(k))
            if v is not None:
                return v
        null_fields.append(name)
        return None

    ab   = _i(["at_bats", "ab"], "ab")
    hits = _i(["hits", "h"], "hits")
    rbi  = _i(["rbi", "runs_batted_in"], "rbi")
    obp  = _f(["obp", "on_base_percentage"], "obp")
    slg  = _f(["slg", "slugging_percentage"], "slg")
    ba   = _f(["avg", "batting_average", "ba"], "ba")
    hr   = _i(["hr", "home_runs"], "hr")

    prov = BDLProvenance(
        source            = BDL_SOURCE_NAME,
        source_type       = BDL_SOURCE_TYPE,
        source_grade      = BDL_SOURCE_GRADE,
        endpoint          = endpoint,
        sport             = "MLB",
        player_id         = player_id or None,
        player_name       = player_name,
        game_id           = game_id or None,
        retrieved_at      = retrieved_at or datetime.now(timezone.utc).isoformat(),
        effective_date    = game_date or None,
        bdl_tier_detected = bdl_tier,
        null_fields       = list(set(null_fields)),
        acquisition_status = BDLStatus.OK,
    )

    return BDLGameRow(
        provenance = prov,
        game_date  = game_date or None,
        season     = season,
        is_dnp     = (ab is None or ab == 0),
        ab=ab, hits=hits, rbi=rbi, obp=obp, slg=slg, ba=ba, hr=hr,
    )
