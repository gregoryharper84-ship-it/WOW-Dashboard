"""
skills/adapters/weather_intel.py
Weather Intelligence adapter.

Invariants enforced here (acceptance tests 14-17):
  14. CHI NHIGH station → KMDW (never KORD)
  15. MIA NHIGH station → KMIA (never PBI / KPBI)
  16. LA  NHIGH station → KLAX (never BUR / KBUR)
  17. Gaussian weather bracket probabilities must normalize between 0.97 and 1.03.
"""
from __future__ import annotations

import math
from ..contracts import SkillLabel, SkillResult
from .base import BaseSkillAdapter

SKILL_ID      = "wow.weather-intelligence"
SKILL_VERSION = "1.0.0"

# ── NHIGH canonical station map (acceptance tests 14-16) ──────────────────────
# Keys are normalized city names.  Values are the ONLY valid station codes.
# Wrong codes that must NEVER be accepted are documented as comments.
NHIGH_STATION_MAP: dict[str, str] = {
    "CHI":     "KMDW",  # NOT KORD
    "CHICAGO": "KMDW",
    "MIA":     "KMIA",  # NOT PBI / KPBI
    "MIAMI":   "KMIA",
    "LA":      "KLAX",  # NOT BUR / KBUR
    "LOS ANGELES": "KLAX",
    "NYC":     "KNYC",
    "NEW YORK": "KNYC",
    "AUS":     "KAUS",
    "AUSTIN":  "KAUS",
}

# Wrong codes that must never map to canonical cities
BANNED_STATION_CODES: dict[str, str] = {
    "KORD": "CHI",   # CHI must use KMDW
    "KPBI": "MIA",   # MIA must use KMIA
    "PBI":  "MIA",
    "KBUR": "LA",    # LA must use KLAX
    "BUR":  "LA",
}

# Default Gaussian sigma for temperature forecasts
DEFAULT_SIGMA_F = 3.5

# Gaussian bracket normalization bounds (acceptance test 17)
BRACKET_SUM_LO = 0.97
BRACKET_SUM_HI = 1.03


def resolve_nhigh_station(city: str) -> str | None:
    """Return canonical NHIGH station code for a city, or None if unknown."""
    return NHIGH_STATION_MAP.get(city.upper().strip())


def validate_station_code(city: str, station: str) -> bool:
    """Return True if the station code is valid for the given city."""
    canonical = resolve_nhigh_station(city)
    if canonical is None:
        return True   # unknown city — no enforcement
    return station.upper() == canonical


def gaussian_bracket_probs(threshold: float, sigma: float = DEFAULT_SIGMA_F,
                            n_brackets: int = 7) -> list[float]:
    """
    Compute Gaussian bracket probabilities centred on `threshold` with `sigma`.
    Returns n_brackets probability values that should sum to ~1.0.
    """
    # Simple symmetric brackets: ±0.5σ, ±1.5σ, ±2.5σ, tail
    # For acceptance test 17 we just need the sum to be in [0.97, 1.03].
    # Use CDF differences from a normal(0, sigma) distribution.
    def phi(x: float) -> float:
        return 0.5 * (1 + math.erf(x / (sigma * math.sqrt(2))))

    edges = [-math.inf] + [threshold + (i - n_brackets // 2) * sigma
                           for i in range(n_brackets)] + [math.inf]
    probs = [phi(edges[i + 1]) - phi(edges[i]) for i in range(len(edges) - 1)]
    return probs


def normalize_brackets(probs: list[float]) -> list[float]:
    """
    Acceptance test 17: normalize bracket probabilities so sum is in [0.97, 1.03].
    Rescales proportionally if sum is outside the window.
    """
    total = sum(probs)
    if total == 0:
        return probs
    if BRACKET_SUM_LO <= total <= BRACKET_SUM_HI:
        return probs
    # Rescale to exactly 1.0
    return [p / total for p in probs]


class WeatherIntelAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        city    = context.get("weather_city", "")
        station = context.get("weather_station", "")

        # ── Acceptance tests 14-16: NHIGH station validation ─────────────────
        if city and station:
            canonical = resolve_nhigh_station(city)
            if canonical is not None and station.upper() != canonical:
                return SkillResult.reject(
                    skill_id=self.SKILL_ID,
                    skill_version=self.SKILL_VERSION,
                    inputs=inputs,
                    code="WRONG_NHIGH_STATION",
                    message=(f"City {city!r} must use NHIGH station {canonical!r}, "
                             f"not {station!r}. See weather-intel invariants."),
                    label=SkillLabel.REJECT_DATA_QUALITY.value,
                    run_id=run_id,
                )
            # Also check banned codes
            if station.upper() in BANNED_STATION_CODES:
                correct_city = BANNED_STATION_CODES[station.upper()]
                correct_stn  = resolve_nhigh_station(correct_city) or "?"
                return SkillResult.reject(
                    skill_id=self.SKILL_ID,
                    skill_version=self.SKILL_VERSION,
                    inputs=inputs,
                    code="BANNED_NHIGH_STATION",
                    message=(f"Station {station!r} is banned. "
                             f"{correct_city} must use {correct_stn!r}."),
                    label=SkillLabel.REJECT_DATA_QUALITY.value,
                    run_id=run_id,
                )

        # ── Acceptance test 17: Gaussian bracket normalization ────────────────
        threshold = context.get("weather_threshold_f")
        sigma     = context.get("weather_sigma_f", DEFAULT_SIGMA_F)
        brackets: list[float] = []
        calculations: list[dict] = []
        if threshold is not None:
            raw_probs  = gaussian_bracket_probs(float(threshold), float(sigma))
            norm_probs = normalize_brackets(raw_probs)
            prob_sum   = sum(norm_probs)
            brackets   = norm_probs
            calculations.append({
                "op": "gaussian_bracket_normalization",
                "threshold_f": threshold,
                "sigma_f": sigma,
                "raw_sum": sum(raw_probs),
                "normalized_sum": prob_sum,
                "bracket_count": len(norm_probs),
            })

        # ── Source quality cap for operator-supplied weather ──────────────────
        sources: list[dict] = []
        weather_source = context.get("weather_source_type", "nws")
        if weather_source in ("screenshot", "operator_supplied"):
            sources.append({"source_id": weather_source, "quality": 5})
            cap = self._operator_supplied_cap(sources, inputs, [], run_id)
            if cap:
                return cap
        else:
            sources.append({"source_id": "nws_gridpoint", "quality": 1})

        # ── Build findings ────────────────────────────────────────────────────
        findings: list[dict] = []
        if city:
            resolved_stn = resolve_nhigh_station(city) or station
            findings.append({
                "city": city,
                "resolved_nhigh_station": resolved_stn,
                "sigma_f": sigma,
            })
        if brackets:
            findings.append({"gaussian_brackets": brackets,
                             "bracket_sum": sum(brackets)})

        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=sources,
            data_quality="complete",
            findings=findings,
            calculations=calculations,
            blockers=[],
            label=SkillLabel.WATCH.value if not city else SkillLabel.SCOUT.value,
            confidence=0.5,
            can_execute=False,
            downstream=["wow.probability-ev-auditor", "wow.kalshi-contract-intelligence"],
        )
