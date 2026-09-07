"""
weather_gate.py — downstream Weather market/portfolio gate.

V17 rule: weather-model probability and Kalshi market evidence are separate
contracts. A model PMF may prove probability normalization; exchange YES prices
may only prove market/bracket coherence and can never create model probability.

Legacy candidates without a V17 probability package remain visible through the
existing research path so this patch does not make discovery stricter. They are
explicitly marked LEGACY_RESEARCH_ONLY and are not governed-probability eligible.
"""
from __future__ import annotations

from typing import Any

_MAX_HORIZON_HOURS: float = 24.0
_MAX_SIGMA_F: float = 4.5
_MAX_PRICE_AGE_MINUTES = 10.0


def _v17_governance(candidate: dict[str, Any]) -> dict[str, Any]:
    package = candidate.get("weather_v17_probability_package") or {}
    completed = package.get("probability_status") == "COMPLETED"
    calibrated = package.get("calibration_status") == "CALIBRATED"
    lower = package.get("calibrated_lower_bound")
    pmf = package.get("final_high_pmf") or {}
    pmf_sum = sum(float(v) for v in pmf.values()) if pmf else None
    normalized = completed and pmf_sum is not None and abs(pmf_sum - 1.0) <= 1e-6
    eligible = completed and calibrated and lower is not None and normalized
    return {
        "package_present": bool(package),
        "completed": completed,
        "calibrated": calibrated,
        "lower_bound_present": lower is not None,
        "pmf_sum": pmf_sum,
        "pmf_normalized": normalized,
        "governed_probability_eligible": eligible,
        "probability_governance_status": "V17_GOVERNED" if eligible else ("V17_RESEARCH_ONLY" if package else "LEGACY_RESEARCH_ONLY"),
    }


def check(candidate: dict[str, Any]) -> dict[str, Any]:
    """Run weather market/portfolio gates without manufacturing probability."""
    verdicts: list[dict[str, Any]] = []
    confidence_tier = candidate.get("confidence_tier", "WEATHER_SCOUT")
    price_age_minutes = candidate.get("price_age_minutes")
    edge_lb = candidate.get("edge_lower_bound")
    gov = _v17_governance(candidate)

    def envelope(**extra: Any) -> dict[str, Any]:
        return {
            "confidence_tier": confidence_tier,
            "net_edge_lower_bound": edge_lb,
            "probability_governance_status": gov["probability_governance_status"],
            "governed_probability_eligible": gov["governed_probability_eligible"],
            "weather_v17_probability_completed": gov["completed"],
            **extra,
        }

    def _fail(gate: int, code: str, detail: str) -> dict[str, Any]:
        verdicts.append({"gate": gate, "passed": False, "code": code, "detail": detail})
        return envelope(passed=False, failure_category=code, failure_gate=gate, gate_verdicts=verdicts)

    def _pass_gate(gate: int, detail: str) -> None:
        verdicts.append({"gate": gate, "passed": True, "detail": detail})

    if confidence_tier != "WEATHER_MODEL_READY":
        return _fail(1, "WEATHER_WATCH_NOT_ELIGIBLE",
                     f"confidence_tier='{confidence_tier}' — WATCH/SCOUT remain visible but outside final ranked pool.")
    _pass_gate(1, f"confidence_tier={confidence_tier}")

    horizon = candidate.get("forecast_horizon_hours")
    if horizon is None or horizon > _MAX_HORIZON_HOURS:
        return _fail(2, "HORIZON_TOO_FAR", f"forecast_horizon_hours={horizon} exceeds {_MAX_HORIZON_HOURS}h cap.")
    _pass_gate(2, f"horizon={horizon}h <= {_MAX_HORIZON_HOURS}h")

    sigma_f = candidate.get("sigma_f")
    if sigma_f is None or sigma_f >= _MAX_SIGMA_F:
        return _fail(3, "SIGMA_F_TOO_HIGH", f"sigma_f={sigma_f} >= {_MAX_SIGMA_F}.")
    _pass_gate(3, f"sigma_f={sigma_f} < {_MAX_SIGMA_F}")

    if not candidate.get("settlement_station_verified", False):
        return _fail(4, "SETTLEMENT_STATION_UNVERIFIED", "settlement station not verified")
    _pass_gate(4, "settlement_station_verified=True")

    if not candidate.get("nws_gridpoint_available", False):
        return _fail(5, "NWS_GRIDPOINT_UNAVAILABLE", "NWS gridpoint unavailable")
    _pass_gate(5, "nws_gridpoint_available=True")

    if not candidate.get("bracket_coverage_complete", False):
        return _fail(6, "BRACKET_COVERAGE_INCOMPLETE", "temperature bracket coverage incomplete")
    _pass_gate(6, "bracket_coverage_complete=True")

    # Gate 7 — V17 model normalization. Never derive this from exchange prices.
    if gov["package_present"]:
        if not gov["pmf_normalized"]:
            return _fail(7, "MODEL_PMF_NORMALIZATION_FAIL", f"weather model PMF sum={gov['pmf_sum']}")
        _pass_gate(7, f"V17 weather PMF normalized: sum={gov['pmf_sum']:.6f}")
    else:
        # Legacy discovery compatibility: the old `probability_normalization_pass`
        # flag was actually a Kalshi-price coherence check. Preserve the research
        # path without treating it as governed model evidence.
        market_coherence = candidate.get("market_bracket_coherence_pass")
        if market_coherence is None:
            legacy_flag = candidate.get("probability_normalization_pass")
            if legacy_flag is not None:
                market_coherence = bool(legacy_flag)
            else:
                brackets = candidate.get("brackets") or []
                yes_sum = sum(float(b.get("yes_price", 0) or 0) for b in brackets)
                market_coherence = abs(yes_sum - 1.0) <= 0.05
        if not market_coherence:
            return _fail(7, "MARKET_BRACKET_COHERENCE_FAIL", "Kalshi bracket prices are not coherent; this is market evidence, not model probability.")
        _pass_gate(7, "legacy market bracket coherence passed; probability_governance_status=LEGACY_RESEARCH_ONLY")

    if not candidate.get("market_open", False):
        return _fail(8, "MARKET_NOT_OPEN", "market is not open")
    _pass_gate(8, "market_open=True")

    if not candidate.get("orderbook_nonempty", False):
        return _fail(9, "ORDERBOOK_EMPTY", "no YES or NO bid in orderbook")
    _pass_gate(9, "orderbook_nonempty=True")

    if price_age_minutes is None or price_age_minutes > _MAX_PRICE_AGE_MINUTES:
        return _fail(10, "KALSHI_DATA_UNOBTAINABLE", f"price_age_minutes={price_age_minutes}; freshness cap={_MAX_PRICE_AGE_MINUTES}m")
    _pass_gate(10, f"price_age_minutes={price_age_minutes:.1f} <= {_MAX_PRICE_AGE_MINUTES}m")

    if edge_lb is None or edge_lb <= 0:
        return _fail(11, "EDGE_BELOW_FLOOR", f"edge_lower_bound={edge_lb}")
    _pass_gate(11, f"edge_lower_bound={edge_lb:.4f} > 0")

    if not candidate.get("portfolio_check_passed", False):
        reason = candidate.get("portfolio_rejection_reason", "PORTFOLIO_GOVERNOR_REJECT")
        return _fail(12, reason, f"Portfolio governor rejected candidate: {reason}")
    _pass_gate(12, "portfolio_check_passed=True")

    return envelope(passed=True, failure_category=None, failure_gate=None, gate_verdicts=verdicts)
