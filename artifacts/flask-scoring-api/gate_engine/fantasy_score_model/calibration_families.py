"""
gate_engine/fantasy_score_model/calibration_families.py
WOW v16 — Fantasy Score Calibration Family Registry

Seven separate calibration families so uncertainty bands are independently
derived and never cross-contaminated.

Each family specifies:
  uncertainty_base        — baseline probability haircut for PROVISIONAL model
  thin_sample_threshold   — n_samples < this → apply thin_sample_penalty
  thin_sample_penalty     — additional uncertainty reduction when sample is thin
  minimum_qual_lb         — calibrated lower bound floor for YES_MODEL_QUALIFIED (fixed at 0.65)
  family_id               — canonical string for ledger records

can_execute = False  (unconditional)
"""
from __future__ import annotations
from dataclasses import dataclass

can_execute: bool = False

_YES_QUALIFIED_FLOOR: float = 0.65   # never lower this


@dataclass(frozen=True)
class CalibrationFamily:
    family_id:            str
    uncertainty_base:     float   # haircut applied to raw calibrated prob
    thin_sample_threshold: int    # samples below this → thin-sample condition
    thin_sample_penalty:  float   # additional haircut when thin
    minimum_qual_lb:      float = _YES_QUALIFIED_FLOOR
    note:                 str = ""


_FAMILIES: dict[str, CalibrationFamily] = {
    "NBA": CalibrationFamily(
        family_id="NBA",
        uncertainty_base=0.07,
        thin_sample_threshold=8,
        thin_sample_penalty=0.04,
        note="NBA Poisson generative; PROVISIONAL until back-tested",
    ),
    "WNBA": CalibrationFamily(
        family_id="WNBA",
        uncertainty_base=0.08,
        thin_sample_threshold=8,
        thin_sample_penalty=0.05,
        note="WNBA generative; weights assumed same as NBA — unconfirmed",
    ),
    "NFL_QB": CalibrationFamily(
        family_id="NFL_QB",
        uncertainty_base=0.09,
        thin_sample_threshold=6,
        thin_sample_penalty=0.05,
        note="NFL QB game-script generative; high week-to-week variance",
    ),
    "NFL_RB": CalibrationFamily(
        family_id="NFL_RB",
        uncertainty_base=0.10,
        thin_sample_threshold=6,
        thin_sample_penalty=0.06,
        note="NFL RB; snap share and game script are primary drivers",
    ),
    "NFL_WR_TE": CalibrationFamily(
        family_id="NFL_WR_TE",
        uncertainty_base=0.10,
        thin_sample_threshold=6,
        thin_sample_penalty=0.06,
        note="NFL WR/TE; reception weight UNCONFIRMED (0.5 half-PPR); high variance",
    ),
    "MLB_HITTER": CalibrationFamily(
        family_id="MLB_HITTER",
        uncertainty_base=0.08,
        thin_sample_threshold=8,
        thin_sample_penalty=0.04,
        note="MLB hitter PA event-tree; PROVISIONAL until back-tested",
    ),
    "MLB_PITCHER": CalibrationFamily(
        family_id="MLB_PITCHER",
        uncertainty_base=0.10,
        thin_sample_threshold=8,
        thin_sample_penalty=0.06,
        note="MLB pitcher 7-regime unconditional mixture; highest variance",
    ),
}


def get_family(family_id: str) -> CalibrationFamily:
    """Return the CalibrationFamily for family_id.  Raises KeyError if unknown."""
    key = family_id.upper()
    if key not in _FAMILIES:
        raise KeyError(f"Unknown calibration family: {family_id!r}. "
                       f"Supported: {sorted(_FAMILIES)}")
    return _FAMILIES[key]


def detect_family(sport: str, position: str | None = None) -> str:
    """
    Map (sport, position) → canonical family_id.
    position is required for NFL; ignored for other sports.
    Raises ValueError for unknown sport.
    """
    s = (sport or "").upper()
    p = (position or "").upper()

    if s == "NBA":
        return "NBA"
    if s == "WNBA":
        return "WNBA"
    if s == "NFL":
        if "QB" in p:
            return "NFL_QB"
        if "RB" in p or "RUNNING" in p or "RUSH" in p:
            return "NFL_RB"
        # WR, TE, K, and anything else
        return "NFL_WR_TE"
    if s == "MLB":
        if "PITCH" in p or p == "SP" or p == "RP" or p == "P":
            return "MLB_PITCHER"
        return "MLB_HITTER"

    raise ValueError(f"No calibration family for sport={sport!r}, position={position!r}")


def compute_bounds(
    raw_prob: float,
    family_id: str,
    sample_size: int,
    *,
    extra_penalty: float = 0.0,
) -> tuple[float, float]:
    """
    Compute (calibrated_lower_bound, calibrated_upper_bound) for raw_prob.

    Applies:
      - family uncertainty_base
      - thin-sample penalty when sample_size < thin_sample_threshold
      - any extra_penalty (e.g. from stress test or market-dependent flag)

    Thin calibration samples widen uncertainty and emit a thin-sample condition;
    they do NOT invent precision.
    """
    fam   = get_family(family_id)
    band  = fam.uncertainty_base
    thin  = sample_size < fam.thin_sample_threshold
    if thin:
        band += fam.thin_sample_penalty
    band += extra_penalty

    lb = max(0.0, round(raw_prob - band, 6))
    ub = min(1.0, round(raw_prob + band, 6))
    return lb, ub, thin


def qualifies(lb: float, identity_locked: bool, settlement_locked: bool) -> bool:
    """
    Return True iff the CLB meets the 65% floor AND identity + settlement are resolved.
    A ≥65% CLB does NOT qualify if scoring or settlement identity is unresolved.
    """
    return lb >= _YES_QUALIFIED_FLOOR and identity_locked and settlement_locked
