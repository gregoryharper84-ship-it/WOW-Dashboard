"""
weather_gate.py  —  12-gate weather filter for GET /wow/kalshi/category-scan
WOW v16.5 Category-Router / Singles-Governor Layer

Only WEATHER_MODEL_READY candidates with all 12 gates passing may enter the
final ranked pool. WEATHER_WATCH and WEATHER_SCOUT stay visible in the
scan output but are hard-blocked from the final pool.

Gate order (strict — first failure short-circuits):
  1.  confidence_tier == WEATHER_MODEL_READY
  2.  forecast_horizon_hours <= 24
  3.  sigma_f < 4.5
  4.  settlement_station_verified
  5.  nws_gridpoint_available
  6.  bracket_coverage_complete
  7.  probability_normalization_pass   (bracket YES prices sum within ±5% of 1.0)
  8.  market_open
  9.  orderbook_nonempty               (at least one YES or NO bid present)
  10. price_age_minutes <= 10
  11. edge_lower_bound > 0             (best bracket adjusted_edge after fee model)
  12. portfolio_check (delegated — caller passes result in candidate)

All gate verdicts are recorded for observability even when short-circuiting.
"""
from __future__ import annotations

from typing import Any

_MAX_HORIZON_HOURS: float = 24.0
_MAX_SIGMA_F: float       = 4.5
_MAX_PRICE_AGE_MINUTES    = 10.0


def check(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Run all 12 weather gates against a single weather candidate.

    Parameters
    ----------
    candidate — dict assembled by the category-scan orchestrator, expected keys:
      confidence_tier          str   WEATHER_MODEL_READY | WEATHER_WATCH | WEATHER_SCOUT
      forecast_horizon_hours   float
      sigma_f                  float
      settlement_station_verified  bool
      nws_gridpoint_available      bool
      bracket_coverage_complete    bool
      probability_normalization_pass bool  (OR brackets list for auto-compute)
      brackets                 list[dict]  [{yes_price, ...}]
      market_open              bool
      orderbook_nonempty       bool
      price_age_minutes        float | None
      edge_lower_bound         float | None  (best bracket adjusted edge)
      portfolio_check_passed   bool   (pre-computed by portfolio_governor.check_single)
      portfolio_rejection_reason str | None

    Returns
    -------
    dict with:
      passed           bool
      failure_category str | None
      failure_gate     int | None
      gate_verdicts    list[dict]
      confidence_tier  str
      net_edge_lower_bound float | None
    """
    verdicts: list[dict[str, Any]] = []
    confidence_tier   = candidate.get("confidence_tier", "WEATHER_SCOUT")
    price_age_minutes = candidate.get("price_age_minutes")
    edge_lb           = candidate.get("edge_lower_bound")

    def _fail(gate: int, code: str, detail: str) -> dict[str, Any]:
        verdicts.append({"gate": gate, "passed": False, "code": code, "detail": detail})
        return {
            "passed":           False,
            "failure_category": code,
            "failure_gate":     gate,
            "gate_verdicts":    verdicts,
            "confidence_tier":  confidence_tier,
            "net_edge_lower_bound": edge_lb,
        }

    def _pass_gate(gate: int, detail: str) -> None:
        verdicts.append({"gate": gate, "passed": True, "detail": detail})

    # ── Gate 1: confidence_tier == WEATHER_MODEL_READY ────────────────────────
    if confidence_tier != "WEATHER_MODEL_READY":
        return _fail(1, "WEATHER_WATCH_NOT_ELIGIBLE",
                     f"confidence_tier='{confidence_tier}' — only WEATHER_MODEL_READY "
                     f"may enter the final pool; WATCH/SCOUT are visible but blocked.")
    _pass_gate(1, f"confidence_tier={confidence_tier}")

    # ── Gate 2: forecast_horizon_hours <= 24 ──────────────────────────────────
    horizon = candidate.get("forecast_horizon_hours")
    if horizon is None or horizon > _MAX_HORIZON_HOURS:
        return _fail(2, "HORIZON_TOO_FAR",
                     f"forecast_horizon_hours={horizon} exceeds {_MAX_HORIZON_HOURS}h cap.")
    _pass_gate(2, f"horizon={horizon}h <= {_MAX_HORIZON_HOURS}h")

    # ── Gate 3: sigma_f < 4.5 ─────────────────────────────────────────────────
    sigma_f = candidate.get("sigma_f")
    if sigma_f is None or sigma_f >= _MAX_SIGMA_F:
        return _fail(3, "SIGMA_F_TOO_HIGH",
                     f"sigma_f={sigma_f} >= {_MAX_SIGMA_F} — forecast spread too wide for final pool.")
    _pass_gate(3, f"sigma_f={sigma_f} < {_MAX_SIGMA_F}")

    # ── Gate 4: settlement_station_verified ───────────────────────────────────
    if not candidate.get("settlement_station_verified", False):
        return _fail(4, "SETTLEMENT_STATION_UNVERIFIED",
                     "settlement_station_verified=False — station METAR code not confirmed.")
    _pass_gate(4, "settlement_station_verified=True")

    # ── Gate 5: nws_gridpoint_available ───────────────────────────────────────
    if not candidate.get("nws_gridpoint_available", False):
        return _fail(5, "NWS_GRIDPOINT_UNAVAILABLE",
                     "nws_gridpoint_available=False — NWS gridpoint check failed.")
    _pass_gate(5, "nws_gridpoint_available=True")

    # ── Gate 6: bracket_coverage_complete ─────────────────────────────────────
    if not candidate.get("bracket_coverage_complete", False):
        return _fail(6, "BRACKET_COVERAGE_INCOMPLETE",
                     "bracket_coverage_complete=False — not all temperature brackets covered.")
    _pass_gate(6, "bracket_coverage_complete=True")

    # ── Gate 7: probability_normalization_pass ────────────────────────────────
    norm_pass = candidate.get("probability_normalization_pass")
    if norm_pass is None:
        # Auto-compute from brackets if not pre-computed
        brackets = candidate.get("brackets") or []
        yes_sum  = sum(float(b.get("yes_price", 0) or 0) for b in brackets)
        norm_pass = abs(yes_sum - 1.0) <= 0.05
    if not norm_pass:
        brackets = candidate.get("brackets") or []
        yes_sum  = sum(float(b.get("yes_price", 0) or 0) for b in brackets)
        return _fail(7, "PROBABILITY_NORMALIZATION_FAIL",
                     f"Bracket YES prices sum={yes_sum:.3f} — deviates >5% from 1.0.")
    _pass_gate(7, "probability_normalization_pass=True")

    # ── Gate 8: market_open ───────────────────────────────────────────────────
    if not candidate.get("market_open", False):
        return _fail(8, "MARKET_NOT_OPEN",
                     "market_open=False — market is not currently open for trading.")
    _pass_gate(8, "market_open=True")

    # ── Gate 9: orderbook_nonempty ────────────────────────────────────────────
    if not candidate.get("orderbook_nonempty", False):
        return _fail(9, "ORDERBOOK_EMPTY",
                     "orderbook_nonempty=False — no YES or NO bid present in orderbook.")
    _pass_gate(9, "orderbook_nonempty=True")

    # ── Gate 10: price_age_minutes <= 10 ──────────────────────────────────────
    if price_age_minutes is None or price_age_minutes > _MAX_PRICE_AGE_MINUTES:
        return _fail(10, "KALSHI_DATA_UNOBTAINABLE",
                     f"price_age_minutes={price_age_minutes} > {_MAX_PRICE_AGE_MINUTES}min — "
                     f"stale or missing price; orderbook freshness gate failed.")
    _pass_gate(10, f"price_age_minutes={price_age_minutes:.1f} <= {_MAX_PRICE_AGE_MINUTES}min")

    # ── Gate 11: edge_lower_bound > 0 ─────────────────────────────────────────
    if edge_lb is None or edge_lb <= 0:
        return _fail(11, "EDGE_BELOW_FLOOR",
                     f"edge_lower_bound={edge_lb} — best bracket adjusted_edge not positive; "
                     f"no positive expected value after fees.")
    _pass_gate(11, f"edge_lower_bound={edge_lb:.4f} > 0")

    # ── Gate 12: portfolio check ──────────────────────────────────────────────
    portfolio_passed = candidate.get("portfolio_check_passed", False)
    if not portfolio_passed:
        reason = candidate.get("portfolio_rejection_reason", "PORTFOLIO_GOVERNOR_REJECT")
        return _fail(12, reason,
                     f"Portfolio governor rejected candidate: {reason}")
    _pass_gate(12, "portfolio_check_passed=True")

    # ── All gates passed ──────────────────────────────────────────────────────
    return {
        "passed":             True,
        "failure_category":   None,
        "failure_gate":       None,
        "gate_verdicts":      verdicts,
        "confidence_tier":    confidence_tier,
        "net_edge_lower_bound": edge_lb,
    }
