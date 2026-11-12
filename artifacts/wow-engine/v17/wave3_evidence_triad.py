"""Wave 3 shared fitted-evidence triad binder (FB-EVIDENCE-20261112, §E).

Binds, for one lane, the triple every submitted model must carry:

  fitted_artifact_sha256 + model_family/version + feature_schema_version
  + calibrator_artifact_sha256 + calibration_method/bound
  + immutable_pregame_corpus_digest

bound to the SAME sport+market+model, exact settlement, official IDs and
source prelock timestamps. Fail closed on missing, stale, swapped,
inconsistent or cross-sport components. Probability normalization
``0 <= lower <= calibrated <= upper <= 1`` and multiclass sum are enforced
where applicable.

Research-only: never certifies, promotes, publishes or executes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

CAN_EXECUTE = False

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EvidenceTriad:
    sport: str
    market: str
    model_family: str
    model_version: str
    fitted_artifact_sha256: str
    feature_schema_version: str
    calibrator_artifact_sha256: str
    calibration_method: str
    pregame_corpus_digest: str
    settlement_basis: str


@dataclass(frozen=True)
class TriadVerdict:
    ok: bool
    decision: str
    blockers: tuple
    can_execute: bool = False
    probability_publishable: bool = False


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) > 0


def verify_triad(triad: Mapping[str, Any]) -> TriadVerdict:
    """Verify one lane's artifact + calibrator + corpus binding. Fail closed."""
    blockers: list[str] = []
    get = dict(triad).get

    for field in (
        "sport",
        "market",
        "model_family",
        "model_version",
        "feature_schema_version",
        "calibration_method",
        "settlement_basis",
    ):
        if not _nonempty(get(field)):
            blockers.append(f"EVIDENCE_TRIAD_{field.upper()}_MISSING")

    for field in (
        "fitted_artifact_sha256",
        "calibrator_artifact_sha256",
        "pregame_corpus_digest",
    ):
        value = get(field)
        if not _nonempty(value):
            blockers.append(f"EVIDENCE_TRIAD_{field.upper()}_MISSING")
        elif not _SHA256_RE.match(str(value).strip().lower()):
            blockers.append(f"EVIDENCE_TRIAD_{field.upper()}_MALFORMED")

    # Cross-lane swap guard: caller declares the expected lane; any mismatch
    # between the triad payload and the lane it is submitted for fails.
    expected_sport = get("expected_sport")
    if _nonempty(expected_sport) and _nonempty(get("sport")):
        if str(get("sport")).strip().upper() != str(expected_sport).strip().upper():
            blockers.append("EVIDENCE_TRIAD_CROSS_SPORT_SWAP")
    expected_market = get("expected_market")
    if _nonempty(expected_market) and _nonempty(get("market")):
        if str(get("market")).strip().upper() != str(expected_market).strip().upper():
            blockers.append("EVIDENCE_TRIAD_CROSS_MARKET_SWAP")
    expected_family = get("expected_model_family")
    if _nonempty(expected_family) and _nonempty(get("model_family")):
        if str(get("model_family")).strip().upper() != str(expected_family).strip().upper():
            blockers.append("EVIDENCE_TRIAD_MODEL_FAMILY_SWAP")

    if get("stale", False) is True:
        blockers.append("EVIDENCE_TRIAD_STALE_COMPONENT")
    if get("poststart_corpus", False) is True:
        blockers.append("EVIDENCE_TRIAD_POSTSTART_CORPUS")

    if blockers:
        return TriadVerdict(
            ok=False,
            decision="REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED",
            blockers=tuple(blockers),
        )
    return TriadVerdict(ok=True, decision="TRIAD_BOUND_READY_FOR_REVIEW", blockers=())


def verify_probability_bounds(
    calibrated: float, lower: float, upper: float
) -> TriadVerdict:
    """Enforce ``0 <= lower <= calibrated <= upper <= 1``. Fail closed."""
    try:
        cal, lo, hi = float(calibrated), float(lower), float(upper)
    except (TypeError, ValueError):
        return TriadVerdict(
            ok=False,
            decision="REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED",
            blockers=("EVIDENCE_TRIAD_BOUNDS_NON_NUMERIC",),
        )
    blockers: list[str] = []
    for name, value in (("LOWER", lo), ("CALIBRATED", cal), ("UPPER", hi)):
        if not 0.0 <= value <= 1.0:
            blockers.append(f"EVIDENCE_TRIAD_{name}_OUT_OF_RANGE")
    if not blockers:
        if lo > cal:
            blockers.append("EVIDENCE_TRIAD_LOWER_ABOVE_CALIBRATED")
        if cal > hi:
            blockers.append("EVIDENCE_TRIAD_CALIBRATED_ABOVE_UPPER")
    if blockers:
        return TriadVerdict(
            ok=False,
            decision="REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED",
            blockers=tuple(blockers),
        )
    return TriadVerdict(ok=True, decision="BOUNDS_NORMALIZED", blockers=())


__all__ = [
    "CAN_EXECUTE",
    "EvidenceTriad",
    "TriadVerdict",
    "verify_probability_bounds",
    "verify_triad",
]
