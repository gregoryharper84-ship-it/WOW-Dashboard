"""Station-specific, multi-model, intraday-updating Kalshi weather engine.

The module owns WEATHER probability only. Kalshi prices, fees, edge and
portfolio state are forbidden model inputs and remain downstream contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

EPS = 1e-12
MIN_F, MAX_F = -40, 140
FORBIDDEN_CITY_STATIONS = {
    "miami": {"KPBI", "PBI", "WEST PALM BEACH"},
    "los angeles": {"KBUR", "BUR", "BURBANK"},
    "chicago": {"KORD", "ORD", "O'HARE", "OHARE"},
}
MARKET_KEYS = {"market_price", "yes_price", "no_price", "edge", "payout", "fee_adjusted_break_even"}


def _clip(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _normalize(pmf: dict[int, float]) -> dict[int, float]:
    total = sum(max(0.0, float(v)) for v in pmf.values())
    if total <= EPS:
        raise ValueError("PMF_NORMALIZATION_FAILED")
    return {int(k): max(0.0, float(v)) / total for k, v in sorted(pmf.items())}


def gaussian_integer_pmf(mu: float, sigma: float, lo: int = MIN_F, hi: int = MAX_F) -> dict[int, float]:
    if sigma <= 0:
        k = max(lo, min(hi, int(round(mu))))
        return {t: 1.0 if t == k else 0.0 for t in range(lo, hi + 1)}
    return _normalize({
        t: max(0.0, _cdf(t + 0.5, mu, sigma) - _cdf(t - 0.5, mu, sigma))
        for t in range(lo, hi + 1)
    })


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _forecast_high(snapshot: dict[str, Any]) -> float | None:
    for key in ("forecast_high_f", "forecast_high"):
        if snapshot.get(key) is not None:
            try:
                return float(snapshot[key])
            except (TypeError, ValueError):
                return None
    vals: list[float] = []
    for row in snapshot.get("hourly") or snapshot.get("hourly_temperature") or []:
        try:
            value = row.get("temperature_f", row.get("temperature")) if isinstance(row, dict) else row
            if value is not None:
                vals.append(float(value))
        except (TypeError, ValueError):
            continue
    return max(vals) if vals else None


def _profile(snapshot: dict[str, Any], station: str, profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    model = str(snapshot.get("model_name") or snapshot.get("source_family") or "").upper()
    horizon = snapshot.get("forecast_horizon_hours")
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for p in profiles:
        pst = str(p.get("station_id") or "").upper()
        pmodel = str(p.get("model_name") or p.get("source_family") or "").upper()
        if pst and pst != station:
            continue
        if pmodel and pmodel != model:
            continue
        ph = p.get("forecast_horizon_hours")
        distance = abs(float(ph) - float(horizon)) if ph is not None and horizon is not None else 999.0
        ranked.append(((1 if pst else 0) + (1 if pmodel else 0), -distance, p))
    return max(ranked, default=(0, 0.0, None), key=lambda x: (x[0], x[1]))[2]


def _sigma(profile: dict[str, Any] | None, fallback: float) -> float:
    if profile:
        for key in ("rmse", "sigma_f", "error_sigma_f"):
            if profile.get(key) is not None:
                try:
                    return max(0.35, float(profile[key]))
                except (TypeError, ValueError):
                    pass
        if profile.get("p10_error") is not None and profile.get("p90_error") is not None:
            return max(0.35, (float(profile["p90_error"]) - float(profile["p10_error"])) / 2.563)
    return max(0.35, float(fallback))


def _base_weight(snapshot: dict[str, Any], sigma: float, now: datetime) -> float:
    quality = _clip(float(snapshot.get("source_quality", 1.0) or 0.0))
    ts = _parse_ts(snapshot.get("retrieved_at") or snapshot.get("model_run_time"))
    age_h = max(0.0, (now - ts).total_seconds() / 3600.0) if ts else 0.0
    freshness = math.exp(-age_h / 18.0)
    return max(EPS, quality * freshness / max(0.5, sigma * sigma))


def _observation_likelihood(snapshot: dict[str, Any], observations: list[dict[str, Any]], sigma: float) -> float:
    hourly: list[tuple[datetime, float]] = []
    for row in snapshot.get("hourly") or []:
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(row.get("valid_at") or row.get("timestamp") or row.get("time"))
        temp = row.get("temperature_f", row.get("temperature"))
        try:
            if ts and temp is not None:
                hourly.append((ts, float(temp)))
        except (TypeError, ValueError):
            pass
    if not hourly or not observations:
        return 1.0
    scores: list[float] = []
    for obs in observations:
        ots = _parse_ts(obs.get("observed_at") or obs.get("timestamp"))
        oval = obs.get("temperature_f", obs.get("current_temperature"))
        if ots is None or oval is None:
            continue
        nearest = min(hourly, key=lambda x: abs((x[0] - ots).total_seconds()))
        if abs((nearest[0] - ots).total_seconds()) > 5400:
            continue
        residual = float(oval) - nearest[1]
        scores.append(math.exp(-0.5 * (residual / max(0.5, sigma)) ** 2))
    if not scores:
        return 1.0
    return max(1e-6, math.exp(sum(math.log(max(1e-9, x)) for x in scores) / len(scores)))


def _family_weights(components: list[dict[str, Any]]) -> None:
    """Cap false confidence from multiple products in the same forecast family."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in components:
        groups.setdefault(str(c["source_family"]).upper(), []).append(c)
    strengths: dict[str, float] = {}
    for fam, rows in groups.items():
        ws = sorted((r["base_weight"] for r in rows), reverse=True)
        strengths[fam] = ws[0] + 0.20 * sum(ws[1:])
    total = sum(strengths.values()) or 1.0
    for fam, rows in groups.items():
        share = strengths[fam] / total
        local = sum(r["base_weight"] for r in rows) or 1.0
        for row in rows:
            row["weight"] = share * row["base_weight"] / local
    z = sum(c["weight"] for c in components) or 1.0
    for c in components:
        c["weight"] /= z


def _validate_identity(identity: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    station = str(identity.get("station_id") or identity.get("settlement_station") or "").strip().upper()
    city = str(identity.get("city") or identity.get("city_label") or "").strip().lower()
    if not identity.get("settlement_station_verified", identity.get("verified", False)):
        blockers.append("SETTLEMENT_STATION_UNVERIFIED")
    if not station:
        blockers.append("SETTLEMENT_STATION_MISSING")
    if not identity.get("settlement_source"):
        blockers.append("SETTLEMENT_SOURCE_MISSING")
    banned = FORBIDDEN_CITY_STATIONS.get(city, set())
    if station in banned or any(len(x) > 4 and x in station for x in banned):
        blockers.append("SETTLEMENT_STATION_REGRESSION_BANNED")
    return blockers


def project_contract(pmf: dict[int, float], contract: dict[str, Any]) -> float:
    kind = str(contract.get("kind") or contract.get("operator") or "BRACKET").upper()
    side = str(contract.get("side") or "YES").upper()
    lower, upper = contract.get("lower_f", contract.get("lower")), contract.get("upper_f", contract.get("upper"))
    threshold = contract.get("threshold_f", contract.get("threshold"))
    if kind in {"BRACKET", "BETWEEN", "INCLUSIVE"}:
        if lower is None or upper is None:
            raise ValueError("CONTRACT_BOUNDS_MISSING")
        yes = sum(v for k, v in pmf.items() if float(lower) <= k <= float(upper))
    elif kind in {"AT_LEAST", "GE", ">="}:
        if threshold is None: raise ValueError("CONTRACT_THRESHOLD_MISSING")
        yes = sum(v for k, v in pmf.items() if k >= float(threshold))
    elif kind in {"ABOVE", "GT", ">"}:
        if threshold is None: raise ValueError("CONTRACT_THRESHOLD_MISSING")
        yes = sum(v for k, v in pmf.items() if k > float(threshold))
    elif kind in {"AT_MOST", "LE", "<="}:
        if threshold is None: raise ValueError("CONTRACT_THRESHOLD_MISSING")
        yes = sum(v for k, v in pmf.items() if k <= float(threshold))
    elif kind in {"BELOW", "LT", "<"}:
        if threshold is None: raise ValueError("CONTRACT_THRESHOLD_MISSING")
        yes = sum(v for k, v in pmf.items() if k < float(threshold))
    else:
        raise ValueError("UNSUPPORTED_CONTRACT_OPERATOR")
    return _clip(yes if side == "YES" else 1.0 - yes)


def _calibrate(raw: float, evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Calibrate a point estimate; publish bounds only when calibration evidence supplies them."""
    if not evidence:
        return {"calibration_status": "UNCALIBRATED_RESEARCH_ONLY", "calibration_method": None,
                "calibration_sample_size": 0, "calibrated_probability": raw,
                "calibrated_lower_bound": None, "calibrated_upper_bound": None}
    method = str(evidence.get("method") or "AFFINE").upper()
    n = int(evidence.get("sample_size") or 0)
    calibrated = raw
    if method == "PLATT":
        a, b = float(evidence.get("a", 1.0)), float(evidence.get("b", 0.0))
        logit = math.log(max(EPS, raw) / max(EPS, 1.0 - raw))
        calibrated = 1.0 / (1.0 + math.exp(-(a * logit + b)))
    elif method == "ISOTONIC_POINTS":
        points = sorted((float(x), float(y)) for x, y in evidence.get("points") or [])
        if points:
            calibrated = points[0][1] if raw <= points[0][0] else points[-1][1]
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                if x0 <= raw <= x1:
                    calibrated = y0 + (raw - x0) * (y1 - y0) / max(EPS, x1 - x0)
                    break
    else:
        calibrated = float(evidence.get("slope", 1.0)) * raw + float(evidence.get("intercept", 0.0))
    calibrated = _clip(calibrated)
    lb, ub = evidence.get("lower_bound"), evidence.get("upper_bound")
    if lb is None or ub is None:
        status, lb, ub = "CALIBRATION_POINT_ONLY_RESEARCH", None, None
    else:
        lb, ub = _clip(float(lb)), _clip(float(ub))
        if lb > calibrated or ub < calibrated or lb > ub:
            return {"calibration_status": "CALIBRATION_BOUNDS_INVALID", "calibration_method": method,
                    "calibration_sample_size": n, "calibrated_probability": calibrated,
                    "calibrated_lower_bound": None, "calibrated_upper_bound": None}
        status = "CALIBRATED"
    return {"calibration_status": status, "calibration_method": method, "calibration_sample_size": n,
            "calibrated_probability": calibrated, "calibrated_lower_bound": lb, "calibrated_upper_bound": ub}


class WeatherV17Engine:
    def __init__(self, fallback_sigma_f: float = 3.0, min_temp_f: int = MIN_F, max_temp_f: int = MAX_F):
        self.fallback_sigma_f, self.min_temp_f, self.max_temp_f = fallback_sigma_f, min_temp_f, max_temp_f

    def score(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = payload.get("settlement_identity") or {}
        blockers = _validate_identity(identity)
        leaked = sorted(k for k in MARKET_KEYS if k in payload)
        if leaked:
            blockers.append("MARKET_DATA_LEAKAGE_IN_WEATHER_MODEL_INPUT")
        forecasts = list(payload.get("forecast_snapshots") or [])
        contract = payload.get("contract") or {}
        if not forecasts: blockers.append("FORECAST_SNAPSHOTS_MISSING")
        if not contract: blockers.append("CONTRACT_DEFINITION_MISSING")
        if blockers: return self._blocked(blockers, identity)

        now = _parse_ts(payload.get("scored_at")) or datetime.now(timezone.utc)
        station = str(identity.get("station_id") or identity.get("settlement_station")).upper()
        profiles = list(payload.get("station_error_profiles") or [])
        observations = list(payload.get("observations") or [])
        observed_max = payload.get("maximum_observed_so_far_f")
        if observed_max is None:
            vals = []
            for row in observations:
                value = row.get("maximum_observed_so_far_f", row.get("maximum_observed_so_far"))
                try:
                    if value is not None: vals.append(float(value))
                except (TypeError, ValueError): pass
            observed_max = max(vals) if vals else None

        components: list[dict[str, Any]] = []
        for snap in forecasts:
            if str(snap.get("station_id") or station).upper() != station: continue
            high = _forecast_high(snap)
            if high is None: continue
            prof = _profile(snap, station, profiles)
            bias = float((prof or {}).get("mean_error") or 0.0)  # observed - forecast
            sigma = _sigma(prof, self.fallback_sigma_f)
            family = str(snap.get("source_family") or snap.get("model_name") or "UNKNOWN")
            likelihood = _observation_likelihood(snap, observations, sigma)
            components.append({"model_name": str(snap.get("model_name") or family), "source_family": family,
                "forecast_high_f": high, "bias_adjustment_f": bias, "mu_f": high + bias, "sigma_f": sigma,
                "profile_sample_size": int((prof or {}).get("sample_size") or 0),
                "base_weight": _base_weight(snap, sigma, now) * likelihood, "observation_likelihood": likelihood,
                "distribution_method": "STATION_ERROR_KERNEL" if prof else "GAUSSIAN_FALLBACK"})
        if not components: return self._blocked(["NO_USABLE_FORECAST_COMPONENTS"], identity)
        _family_weights(components)

        pmf = {t: 0.0 for t in range(self.min_temp_f, self.max_temp_f + 1)}
        for c in components:
            cp = gaussian_integer_pmf(c["mu_f"], c["sigma_f"], self.min_temp_f, self.max_temp_f)
            for t, p in cp.items(): pmf[t] += c["weight"] * p
        pmf = _normalize(pmf)

        regimes = list(payload.get("regimes") or [])
        if regimes:
            total = sum(max(0.0, float(r.get("probability", 0.0))) for r in regimes)
            if total <= EPS: return self._blocked(["REGIME_PROBABILITY_NORMALIZATION_FAILED"], identity)
            regime_probs = {str(r.get("name") or "OTHER"): max(0.0, float(r.get("probability", 0.0))) / total for r in regimes}
            mixed = {t: 0.0 for t in pmf}
            for r in regimes:
                rp = regime_probs[str(r.get("name") or "OTHER")]
                delta = float(r.get("delta_f") or 0.0)
                extra_sigma = max(0.0, float(r.get("extra_sigma_f") or 0.0))
                for base_t, base_p in pmf.items():
                    target = base_t + delta
                    kernel = gaussian_integer_pmf(target, max(0.20, extra_sigma), self.min_temp_f, self.max_temp_f)
                    for t, kp in kernel.items(): mixed[t] += rp * base_p * kp
            pmf = _normalize(mixed)
        else:
            regime_probs = {"UNSPECIFIED_BASELINE": 1.0}

        if observed_max is not None:
            floor = int(math.ceil(float(observed_max) - EPS))
            pmf = _normalize({t: p if t >= floor else 0.0 for t, p in pmf.items()})
        try:
            raw = project_contract(pmf, contract)
        except ValueError as exc:
            return self._blocked([str(exc)], identity)
        calibration = _calibrate(raw, payload.get("calibration"))
        model_status = "MODEL_QUALIFIED" if calibration["calibration_status"] == "CALIBRATED" else "RESEARCH_ONLY_CALIBRATION_REQUIRED"

        def quantile(q: float) -> int:
            total = 0.0
            for t, p in sorted(pmf.items()):
                total += p
                if total + EPS >= q: return t
            return max(pmf)

        public_components = [{k: c[k] for k in ("model_name", "source_family", "forecast_high_f", "bias_adjustment_f",
            "mu_f", "sigma_f", "profile_sample_size", "weight", "observation_likelihood", "distribution_method")} for c in components]
        return {"runtime_generation": "V17_ACTIVE", "lane": "KALSHI_WEATHER_PROBABILITY",
            "controlling_specialist": "WOW_KALSHI_WEATHER_V17", "can_execute": False, "dry_run_only": True,
            "model_status": model_status, "probability_status": "COMPLETED", "blockers": [], "station_id": station,
            "settlement_source": identity.get("settlement_source"), "observed_maximum_so_far_f": observed_max,
            "distribution_method": "STATION_MULTI_MODEL_MIXTURE" if any(c["distribution_method"] != "GAUSSIAN_FALLBACK" for c in components) else "GAUSSIAN_FALLBACK_ENSEMBLE",
            "component_models": public_components, "regime_probabilities": regime_probs,
            "final_high_pmf": {str(k): round(v, 12) for k, v in pmf.items() if v > 1e-10},
            "pmf_normalization": sum(pmf.values()), "distribution_summary": {"p10_f": quantile(.10), "median_f": quantile(.50),
                "p90_f": quantile(.90), "mean_f": sum(k * v for k, v in pmf.items())}, "contract": contract,
            "raw_probability": raw, **calibration, "market_probability": None, "edge": None,
            "portfolio_status": "NOT_EVALUATED_IN_PROBABILITY_LANE", "terminal_authority": "V17_TERMINAL_REDUCER",
            "scored_at": now.isoformat().replace("+00:00", "Z")}

    @staticmethod
    def _blocked(blockers: list[str], identity: dict[str, Any]) -> dict[str, Any]:
        return {"runtime_generation": "V17_ACTIVE", "lane": "KALSHI_WEATHER_PROBABILITY",
            "controlling_specialist": "WOW_KALSHI_WEATHER_V17", "can_execute": False, "dry_run_only": True,
            "model_status": "MODEL_INPUTS_INSUFFICIENT", "probability_status": "BLOCKED",
            "blockers": sorted(set(blockers)), "station_id": identity.get("station_id") or identity.get("settlement_station"),
            "raw_probability": None, "calibrated_probability": None, "calibrated_lower_bound": None,
            "calibrated_upper_bound": None, "market_probability": None, "edge": None,
            "terminal_authority": "V17_TERMINAL_REDUCER"}


def score_weather_contract(payload: dict[str, Any]) -> dict[str, Any]:
    return WeatherV17Engine().score(payload)
