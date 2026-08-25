"""
adapters.py — Source adapter interfaces and concrete implementations.

Abstract base: OpportunitySourceAdapter
Concrete adapters:
  BallDontLieAdapter      — event verification, player status, minutes from recent stats
  OddsApiAdapter          — exact player prop market odds via services.odds_api
  InternalStatsApiAdapter — per-minute component rates from enrichment game log
  SportsDataIOAdapter     — graceful no-op; key not yet authorized
  RotoWireAdapter         — graceful no-op; key not yet authorized

All adapters return a VendorPacket with a `data_provenance` field indicating whether
data came from a live HTTP call ("vendor_retrieved") or pre-supplied enrichment
("enrichment_provided").

Missing credentials → graceful no-op with request_status="auth-required".
Real HTTP calls use bounded timeouts (10 s) and surface all failure modes
(401, 429, 5xx, timeout, network error) as structured failure_reason strings.
"""
from __future__ import annotations

import os
import requests
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from .types import (
    AcquisitionStatus,
    ComponentOpportunityRates,
    LineupStatus,
    MinutesDistribution,
    VendorPacket,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BDL_NBA_BASE   = "https://api.balldontlie.io/v1"
_BDL_WNBA_BASE  = "https://api.balldontlie.io/wnba/v1"
_HTTP_TIMEOUT   = 10   # seconds — all outbound vendor calls

# BallDontLie stat field → component name
_BDL_NBA_STAT_FIELDS  = {"pts": "points", "reb": "rebounds", "ast": "assists", "min": "minutes"}
_BDL_WNBA_STAT_FIELDS = {"pts": "points", "reb": "rebounds", "ast": "assists", "min": "minutes"}

# Composite prop family → Odds API market key
_FAMILY_TO_ODDS_MARKET: dict[str, str] = {
    "pra":  "player_points_rebounds_assists",
    "p+r":  "player_points_rebounds",
    "r+a":  "player_rebounds_assists",
    "p+a":  "player_points_assists",
}

# Odds API sport key map (reuse same convention as services/odds_api.py)
_SPORT_KEY_MAP = {
    "nba":  "basketball_nba",
    "wnba": "basketball_wnba",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _unobtainable_packet(source: str, reason: str) -> VendorPacket:
    return VendorPacket(
        source          = source,
        retrieved_at    = _now_utc(),
        source_grade    = "C",
        request_status  = "auth-required",
        failure_reason  = reason,
        data_provenance = "auth-required",
    )


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] * 0.20 if values else 3.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance ** 0.5


def _minutes_from_bdl_games(game_stats: list[dict], source_label: str) -> MinutesDistribution | None:
    """
    Build a MinutesDistribution from BallDontLie game-stats rows.

    The `min` field in BDL can be "MM:SS" or a plain number.  We normalise
    both forms.  DNP rows (min = 0, "0:00", or None) are excluded.
    Uses the most-recent 10 qualifying games.
    """
    mins: list[float] = []
    for gs in game_stats[:15]:
        raw = gs.get("min") or gs.get("minutes")
        if raw is None:
            continue
        raw_s = str(raw).strip()
        if ":" in raw_s:
            parts = raw_s.split(":")
            try:
                m = float(parts[0]) + float(parts[1]) / 60.0
            except (ValueError, IndexError):
                continue
        else:
            try:
                m = float(raw_s)
            except ValueError:
                continue
        if m < 1.0:
            continue
        mins.append(m)
        if len(mins) >= 10:
            break

    if not mins:
        return None

    avg = sum(mins) / len(mins)
    std = _std(mins)
    return MinutesDistribution(
        low        = round(max(0.0, avg - std), 1),
        mode       = round(avg, 1),
        high       = round(avg + std, 1),
        confidence = min(0.85, 0.55 + 0.03 * len(mins)),
        source     = source_label,
    )


def _component_rates_from_bdl_games(game_stats: list[dict], source_label: str) -> ComponentOpportunityRates | None:
    """
    Compute per-minute component opportunity rates from BDL game-stats rows.
    """
    scoring_rates, reb_rates, ast_rates = [], [], []
    for gs in game_stats[:10]:
        raw_min = gs.get("min") or gs.get("minutes")
        if raw_min is None:
            continue
        raw_s = str(raw_min).strip()
        if ":" in raw_s:
            parts = raw_s.split(":")
            try:
                m = float(parts[0]) + float(parts[1]) / 60.0
            except (ValueError, IndexError):
                continue
        else:
            try:
                m = float(raw_s)
            except ValueError:
                continue
        if m < 1.0:
            continue
        pts = _safe_float(gs.get("pts") or gs.get("points"))
        reb = _safe_float(gs.get("reb") or gs.get("rebounds"))
        ast = _safe_float(gs.get("ast") or gs.get("assists"))
        if pts is not None:
            scoring_rates.append(pts / m)
        if reb is not None:
            reb_rates.append(reb / m)
        if ast is not None:
            ast_rates.append(ast / m)

    if not scoring_rates and not reb_rates and not ast_rates:
        return None

    def _avg(lst: list[float]) -> float | None:
        return sum(lst) / len(lst) if lst else None

    return ComponentOpportunityRates(
        scoring_per_min    = _avg(scoring_rates),
        rebounding_per_min = _avg(reb_rates),
        assisting_per_min  = _avg(ast_rates),
        source             = source_label,
    )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class OpportunitySourceAdapter(ABC):
    """Base class for all opportunity acquisition source adapters."""
    source_name:  str  = "unknown"
    source_grade: str  = "C"
    can_execute:  bool = False

    @abstractmethod
    def is_available(self) -> bool:
        """Returns True when credentials/config are present."""
        ...

    @abstractmethod
    def fetch(
        self,
        player: str,
        event_id: str | None,
        event_date: str | None,
        sport: str,
        prop_type: str | None,
        enrichment: dict[str, Any],
    ) -> VendorPacket:
        """Fetch opportunity data and return a typed VendorPacket."""
        ...

    def _empty_packet(self, status: str = "unavailable", reason: str = "") -> VendorPacket:
        return VendorPacket(
            source          = self.source_name,
            retrieved_at    = _now_utc(),
            source_grade    = self.source_grade,
            request_status  = status,
            failure_reason  = reason or f"{self.source_name} not available",
            data_provenance = "not-attempted",
        )


# ---------------------------------------------------------------------------
# BallDontLieAdapter — real HTTP calls to api.balldontlie.io
# ---------------------------------------------------------------------------

class BallDontLieAdapter(OpportunitySourceAdapter):
    """
    Adapter for BallDontLie API.

    Makes real HTTP calls to api.balldontlie.io to obtain:
      - Player ID from name search (needed for subsequent stats call)
      - Recent game stats (last 15 games) → minutes distribution + component rates
      - Active status from player object

    Two-step flow
    -------------
    1. GET /v1/players?search={name}&per_page=5  (or /wnba/v1/players)
       → resolve player_id, active status
    2. GET /v1/stats?player_ids[]={id}&seasons[]={year}&per_page=15  (or /wnba)
       → parse minutes and component rates from recent games

    Provenance: "vendor_retrieved" when either call succeeds; enrichment fields
    are used only as fallback labels, never as the primary data source.

    Source grade: A- (TRUSTED_STRUCTURED_STATS — direct API with timestamp,
    below official league feeds, above B-grade stat-site reconstruction).
    WOW-PATCH-2026-08-08-BALLDONTLIE-TRUSTED-STATS.

    API key env var: `balldontlie`
    Timeout: 10 s per call
    """
    source_name  = "balldontlie"
    source_grade = "A-"   # upgraded from B: TRUSTED_STRUCTURED_STATS direct API

    def is_available(self) -> bool:
        return bool(
            os.environ.get("balldontlie")
            or os.environ.get("BALLDONTLIE_API_KEY")
        )

    def _api_key(self) -> str:
        return (
            os.environ.get("balldontlie")
            or os.environ.get("BALLDONTLIE_API_KEY")
            or ""
        )

    def _bdl_get(self, url: str, params: dict) -> tuple[dict | None, str]:
        """
        Perform one GET request against BallDontLie.
        Returns (json_body, status_label) where status_label is one of:
          "ok", "auth-failed", "rate-limited", "http-{code}", "timeout", "error-{msg}"
        """
        key = self._api_key()
        if not key:
            return None, "auth-required"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": key},
                params=params,
                timeout=_HTTP_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            return None, "timeout"
        except Exception as exc:
            return None, f"error-{str(exc)[:40]}"

        if resp.status_code == 200:
            return resp.json(), "ok"
        if resp.status_code in (401, 403):
            return None, "auth-failed"
        if resp.status_code == 429:
            return None, "rate-limited"
        return None, f"http-{resp.status_code}"

    def _resolve_player_id(
        self, player: str, sport: str
    ) -> tuple[str | None, bool | None, str]:
        """
        GET /v1/players?search={name}&per_page=5 (NBA) or
        GET /wnba/v1/players?search={name}&per_page=5 (WNBA).

        Returns (player_id_str, is_active, status_label).
        """
        base = _BDL_WNBA_BASE if sport.lower() == "wnba" else _BDL_NBA_BASE
        body, status = self._bdl_get(
            f"{base}/players",
            {"search": player, "per_page": 5},
        )
        if body is None:
            return None, None, status
        players = body.get("data") or []
        if not players:
            return None, None, "not-found"
        # Use best name match (first result if single; otherwise closest)
        best = players[0]
        player_lower = player.lower()
        for p in players:
            fname = (p.get("first_name") or "").lower()
            lname = (p.get("last_name") or "").lower()
            full  = f"{fname} {lname}".strip()
            if player_lower in full or full in player_lower:
                best = p
                break
        pid    = str(best.get("id") or "")
        active = best.get("is_active")
        if active is None:
            status_field = str(best.get("status", "")).lower()
            active = status_field in ("active", "")
        return pid, bool(active), "ok"

    def _fetch_recent_stats(
        self, player_id: str, season: int, sport: str
    ) -> tuple[list[dict], str]:
        """
        GET /v1/stats?player_ids[]={id}&seasons[]={year}&per_page=15  (NBA)
        or /wnba/v1/stats?…  (WNBA).

        Returns (game_stats_list, status_label).
        """
        base = _BDL_WNBA_BASE if sport.lower() == "wnba" else _BDL_NBA_BASE
        body, status = self._bdl_get(
            f"{base}/stats",
            {
                "player_ids[]": player_id,
                "seasons[]":    season,
                "per_page":     15,
            },
        )
        if body is None:
            return [], status
        game_stats = body.get("data") or []
        # Sort most-recent first
        game_stats.sort(
            key=lambda g: (g.get("game") or {}).get("date") or g.get("game_date") or "",
            reverse=True,
        )
        return game_stats, "ok"

    def fetch(
        self,
        player: str,
        event_id: str | None,
        event_date: str | None,
        sport: str,
        prop_type: str | None,
        enrichment: dict[str, Any],
    ) -> VendorPacket:
        if not self.is_available():
            return _unobtainable_packet(self.source_name, "balldontlie API key not configured")

        sport_norm = (sport or "").lower()
        season = int((event_date or _now_utc())[:4])

        # Step 1: resolve player_id via live player search
        player_id, is_active, id_status = self._resolve_player_id(player, sport_norm)

        if id_status in ("auth-failed", "auth-required"):
            return VendorPacket(
                source          = self.source_name,
                retrieved_at    = _now_utc(),
                source_grade    = self.source_grade,
                request_status  = "auth-failed",
                failure_reason  = f"BallDontLie auth failed: {id_status}",
                data_provenance = "auth-required",
            )
        if id_status == "rate-limited":
            return self._empty_packet(
                status="rate-limited",
                reason=f"BallDontLie rate limit hit during player search for '{player}'",
            )
        if id_status == "timeout":
            return self._empty_packet(
                status="timeout",
                reason=f"BallDontLie player search timed out for '{player}'",
            )

        if player_id:
            # Step 2: fetch recent game stats
            game_stats, stats_status = self._fetch_recent_stats(player_id, season, sport_norm)
            source_label = f"api.balldontlie.io ({sport_norm.upper()}) player_id={player_id}"

            minutes_dist   = _minutes_from_bdl_games(game_stats, source_label)
            component_opp  = _component_rates_from_bdl_games(game_stats, source_label)

            # Lineup inference from is_active flag
            if is_active is None:
                lineup = LineupStatus.UNKNOWN
            elif is_active:
                lineup = LineupStatus.EXPECTED
            else:
                lineup = LineupStatus.UNCONFIRMED  # inactive / injured

            return VendorPacket(
                source               = self.source_name,
                retrieved_at         = _now_utc(),
                source_grade         = self.source_grade,
                request_status       = "success" if (minutes_dist or component_opp) else "empty",
                minutes_distribution = minutes_dist,
                component_opportunity= component_opp,
                lineup_status        = lineup,
                player_status        = "active" if is_active else ("inactive" if is_active is False else None),
                data_provenance      = "vendor_retrieved",
                raw                  = {
                    "player_id":    player_id,
                    "is_active":    is_active,
                    "id_status":    id_status,
                    "stats_status": stats_status,
                    "n_games":      len(game_stats),
                    "season":       season,
                },
            )

        # Player not found in BDL — return empty (not an error)
        return self._empty_packet(
            status="not-found",
            reason=f"BallDontLie: player '{player}' not found (status={id_status})",
        )


# ---------------------------------------------------------------------------
# OddsApiAdapter — real HTTP calls via services.odds_api
# ---------------------------------------------------------------------------

class OddsApiAdapter(OpportunitySourceAdapter):
    """
    Adapter for The Odds API — composite prop market exact-market evidence.

    Makes real HTTP calls via services.odds_api to obtain:
      - Player prop odds for the composite family (PRA, P+R, R+A, P+A)
      - Exact vs adjacent line detection by comparing the sportsbook line to
        the board line in the row
      - Minutes restriction probability derived from hold percentage

    Two-step flow
    -------------
    1. GET /sports/{sport_key}/events/{event_id}/odds
         params: markets=player_points_rebounds_assists (family-specific)
       → parse player outcomes to find the target player's line
    2. Compare sportsbook line to board line → EXACT / ADJACENT / PROXY
    3. Compute hold_pct from the two-sided prices as a restriction signal

    Provenance: "vendor_retrieved" when the Odds API call succeeds.
    Pre-supplied enrichment fields are not used as substitutes for live data.

    Credentials: ODDS_API_PAID_KEY → ODDS_API_FREE_KEY → ODDS_API_KEY (priority ladder)
    Timeout: handled by services/odds_api._get (15 s)
    """
    source_name  = "odds_api"
    source_grade = "A"

    def is_available(self) -> bool:
        return bool(
            os.environ.get("ODDS_API_PAID_KEY")
            or os.environ.get("ODDS_API_FREE_KEY")
            or os.environ.get("ODDS_API_KEY")
        )

    def _prop_family_to_market(self, prop_type: str | None) -> str | None:
        """Map a canonical prop family to an Odds API market key."""
        if not prop_type:
            return None
        # Canonicalize first so aliases like "pts+reb+ast" work
        try:
            from gate_engine.opportunity_acquisition.composite_simulator import (
                canonicalize_prop_family,
            )
            canonical = canonicalize_prop_family(prop_type)
        except (ImportError, ValueError):
            canonical = prop_type.lower().replace(" ", "")
        return _FAMILY_TO_ODDS_MARKET.get(canonical)

    def _find_player_outcomes(
        self,
        event_data: dict,
        player: str,
        market_key: str,
        board_line: float | None,
    ) -> dict:
        """
        Parse event_data (Odds API event odds response) to find this player's
        line across bookmakers.

        Returns:
          {
            "found": bool,
            "lines": [{"bookmaker": str, "side": str, "line": float, "price": int}],
            "hold_pct": float | None,
            "is_exact": bool | None,   # True=exact, False=adjacent, None=no match
          }
        """
        player_lower = player.lower()
        found_lines: list[dict] = []

        for bm in (event_data.get("bookmakers") or []):
            bm_key = bm.get("key", "")
            for market in (bm.get("markets") or []):
                if market.get("key") != market_key:
                    continue
                for outcome in (market.get("outcomes") or []):
                    desc = (outcome.get("description") or outcome.get("name") or "").lower()
                    if player_lower not in desc and desc not in player_lower:
                        # Exact or partial player name match
                        player_parts = player_lower.split()
                        if not any(part in desc for part in player_parts if len(part) > 2):
                            continue
                    point = _safe_float(outcome.get("point"))
                    if point is None:
                        continue
                    side_raw = (outcome.get("name") or "").upper()
                    side = "more" if side_raw in ("OVER", "MORE") else "less"
                    found_lines.append({
                        "bookmaker": bm_key,
                        "side":      side,
                        "line":      point,
                        "price":     outcome.get("price"),
                    })

        if not found_lines:
            return {"found": False, "lines": [], "hold_pct": None, "is_exact": None}

        # Compute hold from the most common line pair
        more_prices = [e["price"] for e in found_lines if e["side"] == "more" and e["price"]]
        less_prices = [e["price"] for e in found_lines if e["side"] == "less" and e["price"]]
        hold_pct: float | None = None
        if more_prices and less_prices:
            def _impl_prob(american: int) -> float:
                if american > 0:
                    return 100.0 / (american + 100.0)
                return abs(american) / (abs(american) + 100.0)
            avg_more = sum(more_prices) / len(more_prices)
            avg_less = sum(less_prices) / len(less_prices)
            hold_pct = round(
                _impl_prob(int(avg_more)) + _impl_prob(int(avg_less)) - 1.0,
                4,
            )

        # Exact vs adjacent vs proxy comparison against board line
        is_exact: bool | None = None
        if board_line is not None and found_lines:
            sb_lines = [e["line"] for e in found_lines]
            if sb_lines:
                median_sb = sorted(sb_lines)[len(sb_lines) // 2]
                diff = abs(median_sb - board_line)
                is_exact = diff < 0.26

        return {
            "found":    True,
            "lines":    found_lines[:10],
            "hold_pct": hold_pct,
            "is_exact": is_exact,
        }

    def fetch(
        self,
        player: str,
        event_id: str | None,
        event_date: str | None,
        sport: str,
        prop_type: str | None,
        enrichment: dict[str, Any],
    ) -> VendorPacket:
        if not self.is_available():
            return _unobtainable_packet(self.source_name, "Odds API key not configured")

        sport_norm = (sport or "").lower()
        sport_key  = _SPORT_KEY_MAP.get(sport_norm)
        if not sport_key:
            return self._empty_packet(
                status="skipped",
                reason=f"OddsApi: sport '{sport}' not supported for player props",
            )

        market_key = self._prop_family_to_market(prop_type)
        if not market_key:
            return self._empty_packet(
                status="skipped",
                reason=f"OddsApi: prop_type '{prop_type}' has no market key mapping",
            )

        if not event_id:
            return self._empty_packet(
                status="skipped",
                reason="OddsApi: no event_id provided; cannot fetch event-specific odds",
            )

        # Real HTTP call — reuse services.odds_api to share key resolution,
        # timeout, and quota-header parsing.
        try:
            from services.odds_api import get_player_props  # noqa: PLC0415
        except ImportError:
            return self._empty_packet(
                status="error",
                reason="services.odds_api not importable from adapter context",
            )

        event_data, api_status = get_player_props(sport_key, event_id, [market_key])

        if event_data is None:
            # Map known failure labels to request_status
            if "quota" in api_status.lower() or "429" in api_status:
                return self._empty_packet(status="rate-limited", reason=api_status)
            if "invalid" in api_status.lower() or "401" in api_status:
                return self._empty_packet(status="auth-failed", reason=api_status)
            if "timeout" in api_status.lower():
                return self._empty_packet(status="timeout", reason=api_status)
            return self._empty_packet(status="error", reason=api_status)

        # Board line for exact-market comparison
        board_line: float | None = None
        for lf in ("line", "line_value", "threshold"):
            board_line = _safe_float(enrichment.get(lf))
            if board_line and board_line > 0:
                break

        parsed = self._find_player_outcomes(event_data, player, market_key, board_line)

        # Minutes restriction probability from hold (wide hold → more uncertainty)
        hold_pct = parsed.get("hold_pct") or 0.05
        minutes_restriction_prob = min(0.40, hold_pct * 4.0)

        request_status = "success" if parsed["found"] else "empty"

        return VendorPacket(
            source                   = self.source_name,
            retrieved_at             = _now_utc(),
            source_grade             = self.source_grade,
            request_status           = request_status,
            minutes_restriction_prob = minutes_restriction_prob,
            data_provenance          = "vendor_retrieved",
            raw                      = {
                "sport_key":    sport_key,
                "event_id":     event_id,
                "market_key":   market_key,
                "api_status":   api_status,
                "player_found": parsed["found"],
                "hold_pct":     parsed.get("hold_pct"),
                "is_exact":     parsed.get("is_exact"),
                "n_outcomes":   len(parsed.get("lines") or []),
                "lines":        (parsed.get("lines") or [])[:5],
            },
        )


# ---------------------------------------------------------------------------
# InternalStatsApiAdapter — per-minute rates from enrichment game log (no HTTP)
# ---------------------------------------------------------------------------

class InternalStatsApiAdapter(OpportunitySourceAdapter):
    """
    Adapter for NBA Stats API / MLB Stats API (internal; no external credentials).

    Provides: component opportunity rates derived from season/game logs
    already in the enrichment dict.  Computes per-minute rates from
    historical usage data.  Clearly labelled as enrichment_provided.
    """
    source_name  = "internal_stats_api"
    source_grade = "B"

    def is_available(self) -> bool:
        return True   # always available; uses enrichment data

    def fetch(
        self,
        player: str,
        event_id: str | None,
        event_date: str | None,
        sport: str,
        prop_type: str | None,
        enrichment: dict[str, Any],
    ) -> VendorPacket:
        game_log   = enrichment.get("game_log") or []
        season_log = enrichment.get("season_log") or {}

        # Compute per-minute rates from game log
        rates = _compute_rates_from_log(game_log, season_log)
        component_opp: ComponentOpportunityRates | None = None
        if rates:
            component_opp = ComponentOpportunityRates(
                scoring_per_min    = rates.get("scoring_per_min"),
                rebounding_per_min = rates.get("rebounding_per_min"),
                assisting_per_min  = rates.get("assisting_per_min"),
                source             = "internal_stats_api/game_log",
            )

        # Minutes distribution from L5 average if available
        minutes_dist: MinutesDistribution | None = None
        l5_games = enrichment.get("l5_games") or []
        if l5_games:
            mins = [_safe_float(g.get("minutes") or g.get("min")) for g in l5_games
                    if g.get("minutes") or g.get("min")]
            mins = [m for m in mins if m is not None and m > 0]
            if mins:
                avg_min = sum(mins) / len(mins)
                std_min = _std(mins)
                minutes_dist = MinutesDistribution(
                    low        = round(max(0.0, avg_min - std_min), 1),
                    mode       = round(avg_min, 1),
                    high       = round(avg_min + std_min, 1),
                    confidence = min(0.80, 0.50 + 0.05 * len(mins)),
                    source     = f"internal_stats_api/l5_avg ({len(mins)} games)",
                )

        request_status = "success" if (component_opp or minutes_dist) else "empty"

        return VendorPacket(
            source               = self.source_name,
            retrieved_at         = _now_utc(),
            source_grade         = self.source_grade,
            request_status       = request_status,
            minutes_distribution = minutes_dist,
            component_opportunity= component_opp,
            data_provenance      = "enrichment_provided",
            raw                  = {"rates": rates or {}, "l5_game_count": len(l5_games)},
        )


# ---------------------------------------------------------------------------
# SportsDataIOAdapter — graceful no-op; endpoint not yet authorized
# ---------------------------------------------------------------------------

class SportsDataIOAdapter(OpportunitySourceAdapter):
    """
    Adapter for SportsDataIO NBA projections.

    Status: endpoint not yet authorized.  Returns "not-implemented" when key
    is configured rather than "auth-required", so the orchestrator knows the
    key is valid but the integration is pending, not broken.

    When this adapter is promoted: implement
    GET https://api.sportsdata.io/v3/nba/projections/json/PlayerGameProjectionStatsByDate/{date}
    and parse projected_minutes, projected_points, projected_rebounds, projected_assists.
    """
    source_name  = "sportsdataio"
    source_grade = "A"

    def is_available(self) -> bool:
        return bool(os.environ.get("SPORTSDATAIO_KEY"))

    def fetch(
        self,
        player: str,
        event_id: str | None,
        event_date: str | None,
        sport: str,
        prop_type: str | None,
        enrichment: dict[str, Any],
    ) -> VendorPacket:
        if not self.is_available():
            return _unobtainable_packet(
                self.source_name,
                "SPORTSDATAIO_KEY not configured",
            )
        return VendorPacket(
            source          = self.source_name,
            retrieved_at    = _now_utc(),
            source_grade    = self.source_grade,
            request_status  = "not-implemented",
            failure_reason  = (
                "SportsDataIO NBA Projections endpoint not yet authorized. "
                "Key is present but live integration is pending. "
                "Endpoint: GET /v3/nba/projections/json/PlayerGameProjectionStatsByDate/{date}"
            ),
            data_provenance = "not-attempted",
        )


# ---------------------------------------------------------------------------
# RotoWireAdapter — graceful no-op; endpoint not yet authorized
# ---------------------------------------------------------------------------

class RotoWireAdapter(OpportunitySourceAdapter):
    """
    Adapter for RotoWire projected minutes / rotation notes.

    Status: endpoint not yet authorized.  Returns "not-implemented" when key
    is configured rather than "auth-required", so the orchestrator knows the
    key is valid but the integration is pending, not broken.

    When this adapter is promoted: implement RotoWire's player news/projected
    minutes feed and parse projected_minutes for the target player.
    """
    source_name  = "rotowire"
    source_grade = "A"

    def is_available(self) -> bool:
        return bool(os.environ.get("ROTOWIRE_KEY"))

    def fetch(
        self,
        player: str,
        event_id: str | None,
        event_date: str | None,
        sport: str,
        prop_type: str | None,
        enrichment: dict[str, Any],
    ) -> VendorPacket:
        if not self.is_available():
            return _unobtainable_packet(
                self.source_name,
                "ROTOWIRE_KEY not configured",
            )
        return VendorPacket(
            source          = self.source_name,
            retrieved_at    = _now_utc(),
            source_grade    = self.source_grade,
            request_status  = "not-implemented",
            failure_reason  = (
                "RotoWire projected minutes feed not yet authorized. "
                "Key is present but live integration is pending."
            ),
            data_provenance = "not-attempted",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_rates_from_log(
    game_log: list[dict],
    season_log: dict,
) -> dict[str, float | None]:
    """Compute per-minute opportunity rates from game/season log data."""
    if not game_log and not season_log:
        return {}

    valid_games = [
        g for g in game_log
        if _safe_float(g.get("minutes") or g.get("min") or g.get("MP")) is not None
        and (_safe_float(g.get("minutes") or g.get("min") or g.get("MP")) or 0) > 5
    ]

    if not valid_games:
        return {}

    def _avg_rate(stat_keys: list[str]) -> float | None:
        rates = []
        for g in valid_games:
            mins = _safe_float(g.get("minutes") or g.get("min") or g.get("MP")) or 0
            if mins <= 0:
                continue
            for k in stat_keys:
                val = _safe_float(g.get(k))
                if val is not None:
                    rates.append(val / mins)
                    break
        return (sum(rates) / len(rates)) if rates else None

    return {
        "scoring_per_min":    _avg_rate(["points", "pts", "PTS"]),
        "rebounding_per_min": _avg_rate(["rebounds", "reb", "REB", "TRB"]),
        "assisting_per_min":  _avg_rate(["assists", "ast", "AST"]),
    }
