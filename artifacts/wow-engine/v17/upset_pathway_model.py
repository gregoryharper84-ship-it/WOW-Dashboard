"""Governed sport-specific upset pathway model.

The controlling sport model owns every probability in this package. This
module validates and aggregates fitted regime output; it never converts market
price, narrative text, public sentiment, streaks, or hand-entered weights into
sporting probability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
SCHEMA_VERSION = "WOW_V17_UPSET_PATHWAY_V1"
FORBIDDEN_PROBABILITY_SOURCES = frozenset({
    "MARKET", "SPORTSBOOK", "ODDS", "PUBLIC", "SHARP_ACTION", "REVENGE",
    "MOMENTUM", "RECENT_STREAK", "NARRATIVE", "HAND_WEIGHTED",
})

SPORT_MECHANISM_FAMILIES: dict[str, frozenset[str]] = {
    "MLB": frozenset({"STARTER", "BULLPEN", "PLATOON", "DEFENSE", "POWER", "RUN_ENVIRONMENT"}),
    "NFL": frozenset({"QB", "PASS_RUSH", "OFFENSIVE_LINE", "EXPLOSIVE_PLAY", "TURNOVER", "SPECIAL_TEAMS", "WEATHER", "FOURTH_DOWN"}),
    "NBA": frozenset({"SHOOTING", "TURNOVER", "OFFENSIVE_REBOUND", "RIM", "FOUL", "TRANSITION", "ROTATION"}),
    "WNBA": frozenset({"SHOOTING", "TURNOVER", "OFFENSIVE_REBOUND", "RIM", "FOUL", "TRANSITION", "ROTATION"}),
    "NHL": frozenset({"GOALIE", "SHOT_QUALITY", "FINISHING", "SPECIAL_TEAMS", "REST", "OVERTIME"}),
    "SOCCER": frozenset({"XG", "TRANSITION", "SET_PIECE", "FINISHING", "GOALKEEPER", "PRESS", "RED_CARD", "DRAW_STATE"}),
    "TENNIS": frozenset({"SURFACE", "SERVE", "RETURN", "TIEBREAK", "WORKLOAD", "INJURY", "RETIREMENT"}),
}


class UpsetPathwayInvalid(ValueError):
    pass


def _prob(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise UpsetPathwayInvalid(f"{field}:BOOLEAN_FORBIDDEN")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise UpsetPathwayInvalid(f"{field}:PROBABILITY_REQUIRED") from exc
    if not isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise UpsetPathwayInvalid(f"{field}:OUT_OF_RANGE")
    return parsed


@dataclass(frozen=True)
class Regime:
    regime_id: str
    probability: float
    upset_probability_given_regime: float
    mechanism_families: tuple[str, ...]
    favorite_failure: bool
    underdog_success: bool

    @property
    def contribution(self) -> float:
        return self.probability * self.upset_probability_given_regime


@dataclass(frozen=True)
class PathwayDiagnostics:
    credible_regime_count: int
    pathway_breadth: float
    largest_contribution_share: float
    miracle_dependency: str
    dominant_regime: str
    dominant_mechanisms: tuple[str, ...]


def validate_and_aggregate_upset_pathways(
    package: Mapping[str, Any], *, governed_raw_underdog_probability: float | None = None,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Validate fitted regimes and return their unconditional distribution.

    Pathway breadth is the effective number of contributing regimes
    (inverse-Herfindahl). It is descriptive and never adjusts probability.
    """
    if package.get("schema_version") != SCHEMA_VERSION:
        raise UpsetPathwayInvalid("UPSET_PATHWAY_SCHEMA_INVALID")
    sport = str(package.get("sport") or "").upper()
    allowed = SPORT_MECHANISM_FAMILIES.get(sport)
    if not allowed:
        raise UpsetPathwayInvalid("SPORT_SPECIFIC_PATHWAY_CONTRACT_UNAVAILABLE")
    if not package.get("fitted_artifact_id") or not package.get("feature_schema_hash"):
        raise UpsetPathwayInvalid("FITTED_PATHWAY_ARTIFACT_PROVENANCE_REQUIRED")
    source_types = {str(v).upper() for v in package.get("probability_source_types") or ()}
    if not source_types or source_types & FORBIDDEN_PROBABILITY_SOURCES:
        raise UpsetPathwayInvalid("UPSET_PATHWAY_PROBABILITY_SOURCE_FORBIDDEN")
    if not source_types <= {"MODEL_INPUT", "REGIME_INPUT", "CALIBRATION_INPUT"}:
        raise UpsetPathwayInvalid("UPSET_PATHWAY_PROBABILITY_SOURCE_UNRECOGNIZED")

    regimes: list[Regime] = []
    seen: set[str] = set()
    for index, raw in enumerate(package.get("regimes") or ()):
        if not isinstance(raw, Mapping):
            raise UpsetPathwayInvalid(f"REGIME_{index}:OBJECT_REQUIRED")
        regime_id = str(raw.get("regime_id") or "").strip()
        if not regime_id or regime_id in seen:
            raise UpsetPathwayInvalid("REGIME_ID_MISSING_OR_DUPLICATE")
        seen.add(regime_id)
        mechanisms = tuple(sorted({str(v).upper() for v in raw.get("mechanism_families") or ()}))
        if not mechanisms or not set(mechanisms) <= allowed:
            raise UpsetPathwayInvalid(f"{regime_id}:SPORT_MECHANISM_INVALID")
        regimes.append(Regime(
            regime_id=regime_id,
            probability=_prob(raw.get("probability"), f"{regime_id}.probability"),
            upset_probability_given_regime=_prob(raw.get("upset_probability_given_regime"), f"{regime_id}.conditional"),
            mechanism_families=mechanisms,
            favorite_failure=raw.get("favorite_failure") is True,
            underdog_success=raw.get("underdog_success") is True,
        ))
    if len(regimes) < 2:
        raise UpsetPathwayInvalid("MULTI_REGIME_DISTRIBUTION_REQUIRED")
    if abs(sum(r.probability for r in regimes) - 1.0) > tolerance:
        raise UpsetPathwayInvalid("REGIME_PROBABILITIES_MUST_SUM_TO_ONE")

    raw_upset = sum(r.contribution for r in regimes)
    if governed_raw_underdog_probability is not None:
        governed_raw = _prob(governed_raw_underdog_probability, "governed_raw_underdog_probability")
        if abs(raw_upset - governed_raw) > tolerance:
            raise UpsetPathwayInvalid("PATHWAY_UNCONDITIONAL_PROBABILITY_MISMATCH")

    failure_regime_mass = sum(r.probability for r in regimes if r.favorite_failure)
    independent_fragility = package.get("independent_favorite_fragility_probability")
    failure_probability = (
        _prob(independent_fragility, "independent_favorite_fragility_probability")
        if independent_fragility is not None else failure_regime_mass
    )
    total = raw_upset or 1.0
    shares = [r.contribution / total for r in regimes if r.contribution > 0.0]
    breadth = 1.0 / sum(share * share for share in shares) if shares else 0.0
    dominant = max(regimes, key=lambda r: (r.contribution, r.regime_id))
    largest_share = max(shares, default=0.0)
    credible = [r for r in regimes if r.contribution / total >= 0.10]
    miracle = "HIGH" if largest_share >= 0.75 else "MODERATE" if largest_share >= 0.50 else "LOW"
    diagnostics = PathwayDiagnostics(
        credible_regime_count=len(credible), pathway_breadth=breadth,
        largest_contribution_share=largest_share, miracle_dependency=miracle,
        dominant_regime=dominant.regime_id,
        dominant_mechanisms=dominant.mechanism_families,
    )
    return {
        "schema_version": SCHEMA_VERSION, "status": "PASS", "sport": sport,
        "fitted_artifact_id": package["fitted_artifact_id"],
        "feature_schema_hash": package["feature_schema_hash"],
        "unconditional_raw_upset_probability": raw_upset,
        "favorite_fragility_probability": failure_probability,
        "favorite_failure_regime_mass": failure_regime_mass,
        "regime_contributions": [{**asdict(r), "contribution": r.contribution} for r in regimes],
        "diagnostics": asdict(diagnostics), "probability_mutated_downstream": False,
        "market_inputs_used": False, "can_execute": False,
    }


def unavailable_pathway(reason: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "status": "UNAVAILABLE",
            "reason_codes": [reason], "probability_mutated_downstream": False,
            "market_inputs_used": False, "can_execute": False}


__all__ = ["CAN_EXECUTE", "SCHEMA_VERSION", "UpsetPathwayInvalid",
           "validate_and_aggregate_upset_pathways", "unavailable_pathway"]
