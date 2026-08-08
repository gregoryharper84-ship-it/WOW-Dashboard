"""
gate_engine/balldontlie/types.py
WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS

Data contracts for the BallDontLie acquisition layer.

Source role: TRUSTED_STRUCTURED_STATS
  - Grade A- (direct API with timestamp; below official league feeds;
    above B-grade stat-site reconstruction)
  - Every value retains full provenance (endpoint, player/game/team IDs,
    retrieval timestamp, effective date, freshness, null status, conflict status)
  - Null fields are preserved exactly — never imputed
  - SOURCE_CONFLICT emitted when BDL disagrees materially with a higher-priority source
  - can_execute=False unconditional

can_execute=False unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# BDL tier vocabulary
# ---------------------------------------------------------------------------

class BDLTier:
    FREE      = "FREE"       # /v1/teams, players, games, stats, season_averages
    STARTER   = "STARTER"    # adds more history, live scores
    ALL_STAR  = "ALL_STAR"   # adds injuries, standings, odds, player props
    GOAT      = "GOAT"       # adds pitch data, advanced metrics, full play-by-play
    UNKNOWN   = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------------------
# BDL response/acquisition status vocabulary
# ---------------------------------------------------------------------------

class BDLStatus:
    OK                = "OK"
    AUTH_REQUIRED     = "AUTH_REQUIRED"
    AUTH_FAILED       = "AUTH_FAILED"
    RATE_LIMITED      = "RATE_LIMITED"
    NOT_IN_TIER       = "NOT_IN_TIER"
    ENDPOINT_404      = "ENDPOINT_404"
    TIMEOUT           = "TIMEOUT"
    HTTP_ERROR        = "HTTP_ERROR"
    PARSE_ERROR       = "PARSE_ERROR"
    NO_DATA           = "NO_DATA"
    DATA_UNOBTAINABLE = "DATA_UNOBTAINABLE"
    STALE             = "STALE"
    EVENT_UNRESOLVED  = "EVENT_IDENTITY_UNRESOLVED"
    SOURCE_CONFLICT   = "SOURCE_CONFLICT"
    CORROBORATED      = "CORROBORATED"


# Source grade for BDL — TRUSTED_STRUCTURED_STATS
BDL_SOURCE_GRADE: str = "A-"
BDL_SOURCE_TYPE:  str = "balldontlie_api"
BDL_SOURCE_NAME:  str = "api.balldontlie.io"

# Freshness threshold: data older than this is STALE
BDL_STALE_HOURS: float = 6.0

# Material discrepancy threshold for SOURCE_CONFLICT detection
BDL_CONFLICT_THRESHOLD: float = 0.15   # >15% difference relative to existing value


# ---------------------------------------------------------------------------
# Provenance record
# ---------------------------------------------------------------------------

@dataclass
class BDLProvenance:
    """
    Full provenance carried on every BDL-derived value.

    All fields populated at fetch time. Null fields are listed in null_fields.
    conflict_status is set by the reconciliation engine, not by the fetcher.
    """
    source:             str        = BDL_SOURCE_NAME
    source_type:        str        = BDL_SOURCE_TYPE
    source_grade:       str        = BDL_SOURCE_GRADE
    endpoint:           str        = ""          # e.g. "/wnba/v1/stats"
    sport:              str | None = None        # "NBA" | "WNBA" | "MLB"
    player_id:          str | None = None
    player_name:        str | None = None
    game_id:            str | None = None
    team_id:            str | None = None
    retrieved_at:       str        = ""          # ISO 8601 UTC
    effective_date:     str | None = None        # game/season date
    freshness_hours:    float | None = None
    bdl_tier_detected:  str        = BDLTier.UNKNOWN
    null_fields:        list[str]  = field(default_factory=list)
    conflict_status:    str        = BDLStatus.OK
    endpoint_available: bool       = True
    acquisition_status: str        = BDLStatus.OK
    acquisition_notes:  list[str]  = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source":             self.source,
            "source_type":        self.source_type,
            "source_grade":       self.source_grade,
            "endpoint":           self.endpoint,
            "sport":              self.sport,
            "player_id":          self.player_id,
            "player_name":        self.player_name,
            "game_id":            self.game_id,
            "team_id":            self.team_id,
            "retrieved_at":       self.retrieved_at,
            "effective_date":     self.effective_date,
            "freshness_hours":    self.freshness_hours,
            "bdl_tier_detected":  self.bdl_tier_detected,
            "null_fields":        self.null_fields,
            "conflict_status":    self.conflict_status,
            "endpoint_available": self.endpoint_available,
            "acquisition_status": self.acquisition_status,
            "acquisition_notes":  self.acquisition_notes,
        }


# ---------------------------------------------------------------------------
# Single BDL HTTP response wrapper
# ---------------------------------------------------------------------------

@dataclass
class BDLResponse:
    """Result of one BDL HTTP call."""
    status:   str                     = BDLStatus.OK
    data:     list[dict[str, Any]]    = field(default_factory=list)
    meta:     dict[str, Any]          = field(default_factory=dict)
    raw:      dict[str, Any]          = field(default_factory=dict)
    endpoint: str                     = ""
    tier:     str                     = BDLTier.UNKNOWN
    notes:    list[str]               = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == BDLStatus.OK

    @property
    def auth_blocked(self) -> bool:
        return self.status in (BDLStatus.AUTH_REQUIRED, BDLStatus.AUTH_FAILED)

    @property
    def tier_blocked(self) -> bool:
        return self.status == BDLStatus.NOT_IN_TIER


# ---------------------------------------------------------------------------
# Per-game row (universal across sports, with nulls preserved)
# ---------------------------------------------------------------------------

@dataclass
class BDLGameRow:
    """
    One per-game stat row from BDL — universal schema.

    Fields specific to a sport that are absent in BDL for another sport are
    left as None. Callers must check None before using.
    Null fields are documented in provenance.null_fields.
    Season averages are NEVER placed in BDLGameRow — only game-level rows.
    """
    provenance:     BDLProvenance = field(default_factory=BDLProvenance)

    # Universal identity
    game_date:      str | None = None   # YYYY-MM-DD
    season:         int | None = None
    opponent_team:  str | None = None
    home_away:      str | None = None   # "home" | "away"
    is_dnp:         bool       = False

    # NBA / WNBA fields
    min:            float | None = None   # minutes played (normalized from MM:SS)
    pts:            float | None = None
    reb:            float | None = None
    ast:            float | None = None
    stl:            float | None = None
    blk:            float | None = None
    tov:            float | None = None
    fga:            float | None = None
    fgm:            float | None = None
    fg3a:           float | None = None
    fg3m:           float | None = None
    fta:            float | None = None
    ftm:            float | None = None
    oreb:           float | None = None
    dreb:           float | None = None
    pf:             float | None = None
    # Advanced (tier-dependent)
    usage_rate:     float | None = None
    net_rating:     float | None = None
    off_rating:     float | None = None
    def_rating:     float | None = None

    # MLB pitching fields
    outs_recorded:  int   | None = None   # raw outs (divide by 3 for IP)
    ip:             float | None = None   # innings pitched (derived)
    batters_faced:  int   | None = None
    k:              int   | None = None   # strikeouts
    bb:             int   | None = None   # walks
    h:              int   | None = None   # hits allowed
    er:             int   | None = None   # earned runs
    hr:             int   | None = None   # home runs allowed
    pitch_count:    int   | None = None
    # GOAT pitch data (GOAT tier only)
    avg_velocity:   float | None = None
    zone_rate:      float | None = None
    chase_rate:     float | None = None
    whiff_rate:     float | None = None
    contact_rate:   float | None = None
    xwoba:          float | None = None
    pitch_mix:      dict[str, float] | None = None

    # MLB batting fields
    ab:             int   | None = None
    hits:           int   | None = None
    rbi:            int   | None = None
    obp:            float | None = None
    slg:            float | None = None
    ba:             float | None = None

    def stat_value(self, stat_key: str) -> float | None:
        """Return the float value for a WOW canonical stat key."""
        mapping = {
            "PTS": self.pts,
            "REB": self.reb,
            "AST": self.ast,
            "STL": self.stl,
            "BLK": self.blk,
            "TOV": self.tov,
            "FGA": self.fga,
            "FG3A": self.fg3a,
            "FG3M": self.fg3m,
            "PTS+REB+AST": None if any(
                v is None for v in (self.pts, self.reb, self.ast)
            ) else (self.pts or 0) + (self.reb or 0) + (self.ast or 0),
            "PTS+REB": None if any(
                v is None for v in (self.pts, self.reb)
            ) else (self.pts or 0) + (self.reb or 0),
            "PTS+AST": None if any(
                v is None for v in (self.pts, self.ast)
            ) else (self.pts or 0) + (self.ast or 0),
            "REB+AST": None if any(
                v is None for v in (self.reb, self.ast)
            ) else (self.reb or 0) + (self.ast or 0),
            "MIN": self.min,
            "IP":  self.ip,
            "OUTS": float(self.outs_recorded) if self.outs_recorded is not None else None,
            "K":   float(self.k) if self.k is not None else None,
            "BB":  float(self.bb) if self.bb is not None else None,
            "H":   float(self.h) if self.h is not None else None,
            "PC":  float(self.pitch_count) if self.pitch_count is not None else None,
            "BF":  float(self.batters_faced) if self.batters_faced is not None else None,
            "AB":  float(self.ab) if self.ab is not None else None,
            "HITS": float(self.hits) if self.hits is not None else None,
        }
        return mapping.get(stat_key.upper())

    def to_wow_box_score_row(self) -> dict[str, Any]:
        """Return a WOW canonical box_score_log row (list[dict] element)."""
        row: dict[str, Any] = {
            "game_date":   self.game_date,
            "opponent":    self.opponent_team,
            "home_away":   self.home_away,
            "is_dnp":      self.is_dnp,
            "provenance":  self.provenance.to_dict(),
        }
        # Include all non-None stat fields
        for attr, key in [
            ("min", "min"), ("pts", "pts"), ("reb", "reb"), ("ast", "ast"),
            ("stl", "stl"), ("blk", "blk"), ("tov", "tov"),
            ("fga", "fga"), ("fgm", "fgm"), ("fg3a", "fg3a"), ("fg3m", "fg3m"),
            ("fta", "fta"), ("ftm", "ftm"), ("oreb", "oreb"), ("dreb", "dreb"),
            ("usage_rate", "usage_rate"), ("net_rating", "net_rating"),
            ("outs_recorded", "outs_recorded"), ("ip", "ip"),
            ("batters_faced", "batters_faced"), ("k", "k"),
            ("bb", "bb"), ("h", "h"), ("er", "er"), ("hr", "hr"),
            ("pitch_count", "pitch_count"), ("avg_velocity", "avg_velocity"),
            ("zone_rate", "zone_rate"), ("chase_rate", "chase_rate"),
            ("whiff_rate", "whiff_rate"), ("contact_rate", "contact_rate"),
            ("xwoba", "xwoba"), ("pitch_mix", "pitch_mix"),
            ("ab", "ab"), ("hits", "hits"), ("rbi", "rbi"),
            ("obp", "obp"), ("slg", "slg"), ("ba", "ba"),
        ]:
            val = getattr(self, attr)
            if val is not None:
                row[key] = val
        return row


# ---------------------------------------------------------------------------
# Full player package (what the acquisition layer returns for one player)
# ---------------------------------------------------------------------------

@dataclass
class BDLPlayerPackage:
    """
    Complete BDL data package for one player/game context.

    game_rows: chronological game rows (oldest first for L5/L10 construction)
    acquisition_status: overall status for this package
    """
    player_id:          str | None          = None
    player_name:        str | None          = None
    sport:              str | None          = None
    acquisition_status: str                 = BDLStatus.OK
    game_rows:          list[BDLGameRow]    = field(default_factory=list)
    season_averages:    dict[str, Any]      = field(default_factory=dict)  # never used as game log
    injuries:           list[dict[str, Any]] = field(default_factory=list)
    odds_props:         list[dict[str, Any]] = field(default_factory=list)
    provenance:         BDLProvenance       = field(default_factory=BDLProvenance)
    notes:              list[str]           = field(default_factory=list)

    @property
    def qualified_rows(self) -> list[BDLGameRow]:
        """Non-DNP rows only, most recent first."""
        return [r for r in reversed(self.game_rows) if not r.is_dnp]

    def wow_game_log(self, stat_key: str, n: int = 10) -> list[float]:
        """
        WOW canonical game_log: list of float stat values, most recent first.
        Constructs from verified chronological game records only.
        Season averages are NEVER used here.
        """
        values: list[float] = []
        for row in self.qualified_rows[:n]:
            v = row.stat_value(stat_key)
            if v is not None:
                values.append(round(v, 1))
        return values

    def wow_box_score_log(self, n: int = 10) -> list[dict[str, Any]]:
        """WOW canonical box_score_log: list[dict], most recent first."""
        return [r.to_wow_box_score_row() for r in self.qualified_rows[:n]]

    def minutes_stats(self) -> dict[str, float | None]:
        """
        Compute minutes distribution inputs for WOW WNBA usage model.
        Returns: mean, variance, cv (coefficient of variation), role_stability.
        Computed from underlying verified game logs — WOW uncertainty engine
        controls the posterior/scenario model.
        """
        mins = [r.min for r in self.qualified_rows[:15] if r.min is not None and r.min >= 1.0]
        if not mins:
            return {"mean": None, "variance": None, "cv": None, "role_stability": None, "n": 0}
        mean = sum(mins) / len(mins)
        if len(mins) >= 2:
            var = sum((m - mean) ** 2 for m in mins) / len(mins)
        else:
            var = (mean * 0.20) ** 2
        sd  = var ** 0.5
        cv  = sd / mean if mean > 0 else None
        # Role stability: fraction of games within ±20% of mean
        stable = sum(1 for m in mins if abs(m - mean) / max(mean, 1.0) <= 0.20)
        role_stability = stable / len(mins) if mins else None
        return {
            "mean":           round(mean, 2),
            "variance":       round(var, 3),
            "cv":             round(cv, 3) if cv is not None else None,
            "role_stability": round(role_stability, 3) if role_stability is not None else None,
            "n":              len(mins),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id":          self.player_id,
            "player_name":        self.player_name,
            "sport":              self.sport,
            "acquisition_status": self.acquisition_status,
            "game_count":         len(self.game_rows),
            "qualified_game_count": len(self.qualified_rows),
            "notes":              self.notes,
            "provenance":         self.provenance.to_dict(),
        }
