"""
gate_engine/llp_odds_resolver.py
================================
LLP Odds Fallback Patch v16.1B — per-candidate source resolution module.

Implements a fault-tolerant fallback ladder so a single odds-provider
failure cannot cause a global board NO PLAY.

Source priority per candidate:
  1. Odds API live h2h market
  2. ESPN event identity validation (event exists, date, status — NOT odds)
  3. PrizePicks two-way decimal payout reconstruction
  4. DATA_UNOBTAINABLE

Pure Python — no Flask imports, no app.py imports, no global state.
"""
from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional


# ── Source quality constants ──────────────────────────────────────────────────
SOURCE_QUALITY_LIVE          = "live"
SOURCE_QUALITY_OFFICIAL      = "official"
SOURCE_QUALITY_RECONSTRUCTED = "reconstructed"
SOURCE_QUALITY_PROXY         = "proxy"
SOURCE_QUALITY_UNAVAILABLE   = "unavailable"

# ── Diagnostic tags (injected into failure_paths on the candidate record) ─────
TAG_ODDS_API_FAILED           = "ODDS_API_FAILED"
TAG_ESPN_EVENT_VALIDATED      = "ESPN_EVENT_VALIDATED"
TAG_NO_SPORTSBOOK_COMP        = "NO_SPORTSBOOK_COMP"
TAG_PROXY_NO_VIG              = "PROXY_NO_VIG"
TAG_PRIZEPICKS_RECONSTRUCTION = "PRIZEPICKS_RECONSTRUCTION"
TAG_DATA_UNOBTAINABLE         = "DATA_UNOBTAINABLE"

# ── Label / stake ceilings enforced on proxy paths by _llp_analyze_one ────────
PROXY_LABEL_CEILING       = "LLP_SCOUT"       # maximum LLP taxonomy label
PROXY_CONFIDENCE_CEILING  = "LOW"             # confidence_tier ceiling
PROXY_STAKE_TIER          = "PASS"            # stake_tier value
PROXY_BIG_STAKE_STATUS    = "BLOCKED"         # big_stake_status value
PROXY_FINAL_DECISION_MAX  = "WATCH"           # final_decision capped from BET


# ── ESPN sport/league map for scoreboard endpoint ─────────────────────────────
_ESPN_SPORT_MAP: Dict[str, tuple] = {
    "nba":   ("basketball", "nba"),
    "wnba":  ("basketball", "wnba"),
    "nfl":   ("football",   "nfl"),
    "mlb":   ("baseball",   "mlb"),
    "nhl":   ("hockey",     "nhl"),
    "ncaaf": ("football",   "college-football"),
    "ncaab": ("basketball", "mens-college-basketball"),
}


# ─────────────────────────────────────────────────────────────────────────────
# PrizePicks reconstruction
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_no_vig_from_decimal(
    decimal_a: float,
    decimal_b: float,
) -> Optional[Dict[str, Any]]:
    """
    Two-way decimal payout normalization (PrizePicks Teams board).

        raw_prob_a  = 1 / decimal_a
        raw_prob_b  = 1 / decimal_b
        overround   = raw_prob_a + raw_prob_b
        no_vig_a    = raw_prob_a / overround
        no_vig_b    = raw_prob_b / overround

    Returns a metadata dict on success, None if inputs are invalid.

    Semantics:
      - decimal_a / decimal_b must both be > 1.0 (valid payout format).
      - Result is NOT consensus sportsbook pricing — caller must tag it proxy.
      - Equivalent to _llp_no_vig_two_way(1/da, 1/db) in app.py.
    """
    try:
        da = float(decimal_a)
        db = float(decimal_b)
    except (TypeError, ValueError):
        return None
    if da <= 1.0 or db <= 1.0:
        return None
    raw_a     = 1.0 / da
    raw_b     = 1.0 / db
    overround = raw_a + raw_b
    if overround <= 0:
        return None
    return {
        "raw_prob_a":             round(raw_a, 6),
        "raw_prob_b":             round(raw_b, 6),
        "overround":              round(overround, 6),
        "reconstructed_no_vig_a": round(raw_a / overround, 6),
        "reconstructed_no_vig_b": round(raw_b / overround, 6),
        "reconstruction_method":  "two_way_decimal_normalization",
        "reconstruction_timestamp": (
            datetime.datetime.utcnow().isoformat() + "Z"),
        "source_type":   "prizepicks_reconstructed",
        "source_quality": SOURCE_QUALITY_PROXY,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ESPN event identity validation (not an odds source)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    return "".join(c for c in (name or "").lower() if c.isalnum())


def validate_espn_event(
    sport: str,
    away: str,
    home: str,
    board_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Lightweight ESPN scoreboard lookup for event identity only.

    Returns a metadata dict when the event is found; None on miss or error.

    IMPORTANT — ESPN is not a sportsbook:
      - We only extract: event exists, start time, teams, status.
      - We never fabricate odds, consensus prices, or CLV from ESPN.
      - Caller is responsible for the hard-kill when status is in-progress
        or completed.
    """
    import requests  # noqa: PLC0415 — local import keeps module importable without requests

    league_info = _ESPN_SPORT_MAP.get((sport or "").lower())
    if not league_info:
        return None
    esport, eleague = league_info
    url = (f"https://site.api.espn.com/apis/site/v2/sports"
           f"/{esport}/{eleague}/scoreboard")
    params: Dict[str, str] = {}
    if board_date:
        params["dates"] = board_date.replace("-", "")

    try:
        r = requests.get(url, params=params, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    away_n = _normalize_name(away)
    home_n = _normalize_name(home)
    for ev in (data.get("events") or []):
        comps = ev.get("competitions") or [{}]
        comp  = comps[0] if comps else {}
        teams = {
            c.get("homeAway", ""): (c.get("team") or {}).get("displayName", "")
            for c in (comp.get("competitors") or [])
        }
        h_n = _normalize_name(teams.get("home", ""))
        a_n = _normalize_name(teams.get("away", ""))
        if ((away_n and a_n and (away_n in a_n or a_n in away_n)) and
                (home_n and h_n and (home_n in h_n or h_n in home_n))):
            status_obj = (comp.get("status") or {}).get("type") or {}
            return {
                "event_id":     ev.get("id"),
                "name":         ev.get("name"),
                "start_time":   ev.get("date"),
                "home_team":    teams.get("home"),
                "away_team":    teams.get("away"),
                "status_state": status_obj.get("state", "unknown"),
                "status_desc":  status_obj.get("description", ""),
                "completed":    bool(status_obj.get("completed", False)),
                "source":       "site.api.espn.com",
            }
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Resolution result
# ─────────────────────────────────────────────────────────────────────────────

class OddsResolution:
    """
    Structured result of a per-candidate source resolution pass.

    ``event`` and ``sel`` mirror the shapes returned by ``_llp_match_event``
    and ``_llp_extract_market`` so that downstream code in ``_llp_analyze_one``
    can use them without branching.  For proxy paths, ``event`` is None and
    ``sel`` is a synthetic dict with ``novig_prob`` populated from reconstruction
    but ``american`` / ``point`` / ``book`` set to None.
    """

    __slots__ = (
        "event", "sel",
        "odds_source_primary", "odds_source_fallback_used", "odds_source_quality",
        "sportsbook_no_vig_available", "reconstructed_no_vig_available",
        "reconstructed_no_vig_probability", "reconstructed_no_vig_opponent",
        "reconstruction_meta",
        "source_resolution_path", "source_failure_reasons",
        "label_ceiling_reason", "data_contract_status",
        "diagnostic_tags", "espn_validation",
    )

    def __init__(
        self,
        *,
        event=None,
        sel=None,
        odds_source_primary: str = "unavailable",
        odds_source_fallback_used: bool = False,
        odds_source_quality: str = SOURCE_QUALITY_UNAVAILABLE,
        sportsbook_no_vig_available: bool = False,
        reconstructed_no_vig_available: bool = False,
        reconstructed_no_vig_probability: Optional[float] = None,
        reconstructed_no_vig_opponent: Optional[float] = None,
        reconstruction_meta: Optional[Dict] = None,
        source_resolution_path: Optional[List[str]] = None,
        source_failure_reasons: Optional[List[str]] = None,
        label_ceiling_reason: Optional[str] = None,
        data_contract_status: str = "DATA_CONTRACT_INCOMPLETE",
        diagnostic_tags: Optional[List[str]] = None,
        espn_validation: Optional[Dict] = None,
    ) -> None:
        self.event                         = event
        self.sel                           = sel
        self.odds_source_primary           = odds_source_primary
        self.odds_source_fallback_used     = odds_source_fallback_used
        self.odds_source_quality           = odds_source_quality
        self.sportsbook_no_vig_available   = sportsbook_no_vig_available
        self.reconstructed_no_vig_available = reconstructed_no_vig_available
        self.reconstructed_no_vig_probability = reconstructed_no_vig_probability
        self.reconstructed_no_vig_opponent = reconstructed_no_vig_opponent
        self.reconstruction_meta           = reconstruction_meta or {}
        self.source_resolution_path        = source_resolution_path or []
        self.source_failure_reasons        = source_failure_reasons or []
        self.label_ceiling_reason          = label_ceiling_reason
        self.data_contract_status          = data_contract_status
        self.diagnostic_tags               = diagnostic_tags or []
        self.espn_validation               = espn_validation

    @property
    def usable(self) -> bool:
        """True when at least one source produced a usable no-vig probability."""
        return self.odds_source_quality != SOURCE_QUALITY_UNAVAILABLE

    @property
    def is_proxy(self) -> bool:
        """True when the source is reconstructed/proxy (not live sportsbook)."""
        return self.odds_source_quality in (
            SOURCE_QUALITY_RECONSTRUCTED, SOURCE_QUALITY_PROXY)

    def to_record_fields(self) -> Dict[str, Any]:
        """
        Return the ten new per-candidate output fields for injection into
        an ``_llp_analyze_one`` record via ``record.update(...)``.
        """
        return {
            "odds_source_primary":            self.odds_source_primary,
            "odds_source_fallback_used":      self.odds_source_fallback_used,
            "odds_source_quality":            self.odds_source_quality,
            "sportsbook_no_vig_available":    self.sportsbook_no_vig_available,
            "reconstructed_no_vig_available": self.reconstructed_no_vig_available,
            "reconstructed_no_vig_probability": self.reconstructed_no_vig_probability,
            "source_resolution_path":         list(self.source_resolution_path),
            "source_failure_reasons":         list(self.source_failure_reasons),
            "label_ceiling_reason":           self.label_ceiling_reason,
            "data_contract_status":           self.data_contract_status,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Main resolution entry point
# ─────────────────────────────────────────────────────────────────────────────

def resolve_odds_source(
    game: Dict[str, Any],
    sport_key: str,
    sport: str,
    fetch_odds_fn: Callable,
    match_event_fn: Callable,
    extract_market_fn: Callable,
    board_date: Optional[str] = None,
    use_espn_validation: bool = True,
    espn_validate_fn: Optional[Callable] = None,
) -> OddsResolution:
    """
    Per-candidate fallback ladder.  Never raises.

    Ladder:
      1. Odds API live h2h
      2. ESPN event identity (validates existence/status, no odds)
      3. PrizePicks two-way decimal reconstruction (pp_home_decimal + pp_away_decimal)
      4. DATA_UNOBTAINABLE

    Parameters
    ----------
    game              : raw game dict from the caller (same shape as _llp_analyze_one receives)
    sport_key         : Odds API sport key (e.g. "baseball_mlb")
    sport             : normalised sport string (e.g. "mlb")
    fetch_odds_fn     : callable(sport_key) → events list or None
    match_event_fn    : callable(events, away, home) → event dict or None
    extract_market_fn : callable(event, market, side) → sel dict or None
    board_date        : YYYY-MM-DD string or None
    use_espn_validation : set False to skip the ESPN step (useful in tests)
    espn_validate_fn  : override for ESPN lookup (defaults to validate_espn_event)
    """
    away   = (game.get("away") or game.get("away_team") or "").strip()
    home   = (game.get("home") or game.get("home_team") or "").strip()
    market = (game.get("market") or "h2h").lower().strip()
    side   = (game.get("side") or "").strip()

    path: List[str]     = []
    failures: List[str] = []
    tags: List[str]     = []
    espn_val: Optional[Dict[str, Any]] = None

    _espn_fn = espn_validate_fn or validate_espn_event

    # ── Step 1: Odds API ──────────────────────────────────────────────────────
    path.append("odds_api_attempt")
    events = None
    try:
        events = fetch_odds_fn(sport_key)
    except Exception as exc:
        failures.append(f"odds_api_exception:{str(exc)[:80]}")

    if events:
        event = None
        try:
            event = match_event_fn(events, away, home)
        except Exception as exc:
            failures.append(f"odds_api_match_exception:{str(exc)[:80]}")

        if event:
            sel = None
            try:
                sel = extract_market_fn(event, market, side)
            except Exception as exc:
                failures.append(f"odds_api_extract_exception:{str(exc)[:80]}")

            if sel and sel.get("novig_prob") is not None:
                path.append("odds_api_success")
                return OddsResolution(
                    event=event,
                    sel=sel,
                    odds_source_primary="odds_api",
                    odds_source_fallback_used=False,
                    odds_source_quality=SOURCE_QUALITY_LIVE,
                    sportsbook_no_vig_available=True,
                    reconstructed_no_vig_available=False,
                    source_resolution_path=path,
                    source_failure_reasons=failures,
                    data_contract_status="DATA_CONTRACT_COMPLETE",
                    diagnostic_tags=tags,
                    espn_validation=None,
                )
            elif not sel:
                failures.append(f"odds_api_market_not_found:{market}/{side}")
            else:
                failures.append("odds_api_novig_missing")
        else:
            failures.append("odds_api_event_not_matched")
    else:
        if not any(f.startswith("odds_api_exception") for f in failures):
            failures.append("odds_api_no_events")
        tags.append(TAG_ODDS_API_FAILED)

    path.append("odds_api_failed")

    # ── Step 2: ESPN event identity validation ────────────────────────────────
    if use_espn_validation:
        path.append("espn_validation_attempt")
        try:
            espn_val = _espn_fn(sport, away, home, board_date)
        except Exception as exc:
            espn_val = None
            failures.append(f"espn_exception:{str(exc)[:80]}")

        if espn_val:
            path.append("espn_event_validated")
            tags.append(TAG_ESPN_EVENT_VALIDATED)
            # Hard kill: event already in-progress or completed.
            # Preserve existing hard-kill behavior — do not attempt reconstruction.
            state = (espn_val.get("status_state") or "").lower()
            if state in ("in", "post") or espn_val.get("completed"):
                path.append("espn_hard_kill_event_live_or_settled")
                failures.append(
                    f"event_status:{espn_val.get('status_desc', 'started_or_settled')}")
                return OddsResolution(
                    event=None,
                    sel=None,
                    odds_source_primary="espn_validation",
                    odds_source_fallback_used=True,
                    odds_source_quality=SOURCE_QUALITY_UNAVAILABLE,
                    sportsbook_no_vig_available=False,
                    reconstructed_no_vig_available=False,
                    source_resolution_path=path,
                    source_failure_reasons=failures,
                    label_ceiling_reason="event_started_or_settled",
                    data_contract_status="DATA_CONTRACT_INCOMPLETE_EVENT_LIVE",
                    diagnostic_tags=tags,
                    espn_validation=espn_val,
                )
        else:
            path.append("espn_validation_miss")
            failures.append("espn_event_not_found")

    # ── Step 3: PrizePicks two-way reconstruction ─────────────────────────────
    # Requires pp_home_decimal AND pp_away_decimal on the game object.
    # Both sides must be present — one side only is not enough for a no-vig.
    pp_home = game.get("pp_home_decimal")
    pp_away = game.get("pp_away_decimal")

    if pp_home is not None and pp_away is not None:
        path.append("prizepicks_reconstruction_attempt")
        reco = reconstruct_no_vig_from_decimal(pp_home, pp_away)
        if reco:
            # Map reconstructed no-vig to the chosen side.
            # pp_home_decimal → reconstructed_no_vig_a (home)
            # pp_away_decimal → reconstructed_no_vig_b (away)
            side_n = _normalize_name(side)
            home_n = _normalize_name(home)
            if side_n and home_n and (side_n == home_n
                                       or side_n in home_n
                                       or home_n in side_n):
                chosen_novig   = reco["reconstructed_no_vig_a"]
                chosen_raw     = reco["raw_prob_a"]
                opponent_novig = reco["reconstructed_no_vig_b"]
            else:
                chosen_novig   = reco["reconstructed_no_vig_b"]
                chosen_raw     = reco["raw_prob_b"]
                opponent_novig = reco["reconstructed_no_vig_a"]

            # Synthetic sel — same shape as _llp_extract_market() output.
            # book/american/point are None (no sportsbook source).
            synthetic_sel = {
                "book":             None,
                "american":         None,
                "point":            None,
                "name":             side,
                "implied_prob":     round(chosen_raw, 6),
                "novig_prob":       round(chosen_novig, 6),
                "_pp_reconstructed": True,
            }

            reco["board_price_timestamp"] = board_date or ""
            tags.extend([TAG_NO_SPORTSBOOK_COMP, TAG_PROXY_NO_VIG,
                          TAG_PRIZEPICKS_RECONSTRUCTION])
            path.append("prizepicks_reconstruction_success")

            return OddsResolution(
                event=None,
                sel=synthetic_sel,
                odds_source_primary="prizepicks_reconstructed",
                odds_source_fallback_used=True,
                odds_source_quality=SOURCE_QUALITY_PROXY,
                sportsbook_no_vig_available=False,
                reconstructed_no_vig_available=True,
                reconstructed_no_vig_probability=round(chosen_novig, 6),
                reconstructed_no_vig_opponent=round(opponent_novig, 6),
                reconstruction_meta=reco,
                source_resolution_path=path,
                source_failure_reasons=failures,
                label_ceiling_reason=(
                    f"proxy_no_vig:{TAG_PRIZEPICKS_RECONSTRUCTION}"),
                data_contract_status="DATA_CONTRACT_PROXY_NO_VIG",
                diagnostic_tags=tags,
                espn_validation=espn_val,
            )
        else:
            failures.append("prizepicks_reconstruction_invalid_decimals")
            path.append("prizepicks_reconstruction_failed")
    else:
        missing = []
        if pp_home is None:
            missing.append("pp_home_decimal")
        if pp_away is None:
            missing.append("pp_away_decimal")
        failures.append(f"prizepicks_missing:{','.join(missing)}")
        path.append("prizepicks_unavailable")

    # ── Step 4: DATA_UNOBTAINABLE ─────────────────────────────────────────────
    tags.append(TAG_DATA_UNOBTAINABLE)
    path.append("data_unobtainable")
    return OddsResolution(
        event=None,
        sel=None,
        odds_source_primary="unavailable",
        odds_source_fallback_used=True,
        odds_source_quality=SOURCE_QUALITY_UNAVAILABLE,
        sportsbook_no_vig_available=False,
        reconstructed_no_vig_available=False,
        source_resolution_path=path,
        source_failure_reasons=failures,
        label_ceiling_reason="data_unobtainable",
        data_contract_status="DATA_CONTRACT_INCOMPLETE",
        diagnostic_tags=tags,
        espn_validation=espn_val,
    )
