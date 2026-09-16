"""Governed calibration/certification-review evidence for Fantasy Score candidates.

This module advances the current Fantasy Score candidate family (NFL, NBA, WNBA,
MLB hitter, MLB pitcher) to a shared calibration-evidence contract without
self-certifying or self-promoting any lane.

The numerical calibration ladder is deliberately reused from the already-governed
NFL DFS evidence builder.  Calibration mathematics are sport-agnostic; this module
adds the missing exact lane/specialist/provenance identity around that numerical
step so evidence cannot be silently transferred between sports, roles, models, or
scoring profiles.

Governance invariants:
- immutable pregame exact-line predictions only;
- exact model source + exact scoring-profile identity;
- >= 50,000 simulations per prediction;
- untouched chronological event-level holdout;
- synthetic evidence can test the pipeline but can never become certification
  evidence;
- an external PromotionPolicy is required even to become review-eligible;
- review eligibility is not certification or promotion;
- probability_publishable=False, rank_eligible=False, can_execute=False always.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from nfl_dfs_calibration_evidence import (
    NflDfsCalibrationEvidenceError,
    PromotionPolicy,
    build_calibration_evidence as _build_nfl_numerical_evidence,
)

CAN_EXECUTE = False
MIN_SIMULATIONS = 50_000

NFL = "NFL"
NBA = "NBA"
WNBA = "WNBA"
MLB_HITTER = "MLB_HITTER"
MLB_PITCHER = "MLB_PITCHER"

IMMUTABLE_PREGAME_SETTLED = "IMMUTABLE_PREGAME_SETTLED"
SYNTHETIC_TEST_ONLY = "SYNTHETIC_TEST_ONLY"
SUPPORTED_EVIDENCE_SOURCES = frozenset({IMMUTABLE_PREGAME_SETTLED, SYNTHETIC_TEST_ONLY})


@dataclass(frozen=True)
class FantasyScoreLaneContract:
    lane: str
    sport: str
    stat_type: str
    market_family: str
    controlling_specialist: str


LANE_CONTRACTS: dict[str, FantasyScoreLaneContract] = {
    NFL: FantasyScoreLaneContract(
        lane=NFL,
        sport="NFL",
        stat_type="FANTASY_SCORE",
        market_family="NFL_DFS_FANTASY_SCORE",
        controlling_specialist="wow.nfl-dfs-fantasy-score-expert",
    ),
    NBA: FantasyScoreLaneContract(
        lane=NBA,
        sport="NBA",
        stat_type="FANTASY_SCORE",
        market_family="NBA_FANTASY_SCORE",
        controlling_specialist="wow.nba-dfs-fantasy-score-expert",
    ),
    WNBA: FantasyScoreLaneContract(
        lane=WNBA,
        sport="WNBA",
        stat_type="FANTASY_SCORE",
        market_family="WNBA_FANTASY_SCORE",
        controlling_specialist="wow.wnba-dfs-fantasy-score-expert",
    ),
    MLB_HITTER: FantasyScoreLaneContract(
        lane=MLB_HITTER,
        sport="MLB",
        stat_type="HITTER_FANTASY_SCORE",
        market_family="MLB_HITTER_FANTASY_SCORE",
        controlling_specialist="wow.mlb-hitter-fantasy-score-expert",
    ),
    MLB_PITCHER: FantasyScoreLaneContract(
        lane=MLB_PITCHER,
        sport="MLB",
        stat_type="PITCHER_FANTASY_SCORE",
        market_family="MLB_PITCHER_FANTASY_SCORE",
        controlling_specialist="wow.mlb-pitcher-fantasy-score-expert",
    ),
}


class FantasyScoreCalibrationEvidenceError(ValueError):
    """Typed validation failure for cross-sport Fantasy Score evidence."""


@dataclass(frozen=True)
class FantasyScoreCalibrationEvidencePacket:
    lane: str
    sport: str
    stat_type: str
    market_family: str
    controlling_specialist: str
    evidence_source_kind: str
    model_version: str
    model_source_sha256: str
    scoring_profile_id: str
    scoring_profile_sha256: str
    evidence_dataset_sha256: str
    calibration_candidate_sha256: str
    total_rows: int
    binary_rows: int
    pushes_excluded: int
    calibration_rows: int
    holdout_rows: int
    calibration_events: int
    holdout_events: int
    calibration_end: str
    holdout_start: str
    fold_count: int
    calibration_method: str | None
    raw_holdout_metrics: Mapping[str, float] | None
    calibrated_holdout_metrics: Mapping[str, float] | None
    oof_calibration_metrics: Mapping[str, float] | None
    promotion_policy_id: str | None
    certification_review_eligible: bool
    certification_status: str
    terminal_status: str
    blockers: tuple[str, ...]
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FantasyScorePromotionReadiness:
    lane: str
    certification_review_eligible: bool
    independent_certification_approved: bool
    certification_approval_id: str | None
    fitted_artifact_id: str | None
    calibration_artifact_id: str | None
    runtime_adapter_registered: bool
    hydration_verified: bool
    canonical_action_verified: bool
    promotion_review_ready: bool
    terminal_status: str
    blockers: tuple[str, ...]
    governed_promotion_authorized: bool = False
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def lane_contract(lane: str) -> FantasyScoreLaneContract:
    lane_n = str(lane or "").strip().upper()
    contract = LANE_CONTRACTS.get(lane_n)
    if contract is None:
        raise FantasyScoreCalibrationEvidenceError(
            f"unsupported Fantasy Score calibration lane: {lane_n or '<missing>'}"
        )
    return contract


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise FantasyScoreCalibrationEvidenceError(f"{field} is required")
    return value


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_lane_identity(
    rows: Sequence[Mapping[str, Any]],
    *,
    contract: FantasyScoreLaneContract,
    evidence_source_kind: str,
) -> None:
    if not rows:
        raise FantasyScoreCalibrationEvidenceError("settled historical rows are required")

    for row in rows:
        row_lane = _required_text(row, "lane").upper()
        if row_lane != contract.lane:
            raise FantasyScoreCalibrationEvidenceError(
                f"calibration cohort cannot mix lane identity: expected {contract.lane}, got {row_lane}"
            )
        row_family = _required_text(row, "market_family").upper()
        if row_family != contract.market_family:
            raise FantasyScoreCalibrationEvidenceError(
                f"market_family mismatch: expected {contract.market_family}, got {row_family}"
            )
        row_specialist = _required_text(row, "controlling_specialist")
        if row_specialist != contract.controlling_specialist:
            raise FantasyScoreCalibrationEvidenceError(
                "controlling_specialist mismatch for Fantasy Score calibration cohort"
            )
        row_source = _required_text(row, "evidence_source_kind").upper()
        if row_source != evidence_source_kind:
            raise FantasyScoreCalibrationEvidenceError(
                f"evidence source mismatch: expected {evidence_source_kind}, got {row_source}"
            )

        # MLB must stay role-explicit; generic MLB Fantasy Score is never inferred.
        if contract.sport == "MLB" and contract.lane not in {MLB_HITTER, MLB_PITCHER}:
            raise FantasyScoreCalibrationEvidenceError("MLB Fantasy Score role must be HITTER or PITCHER")


def _delegate_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    contract: FantasyScoreLaneContract,
) -> list[dict[str, Any]]:
    """Adapt only the NFL builder's validation-only position field.

    The governed NFL evidence builder's calibration math never conditions on
    position; it validates/stores it only.  Non-NFL lane identity remains bound by
    this module and by the original-row evidence hash.  We therefore use a fixed
    allowed sentinel solely at the numerical-adapter boundary rather than
    pretending a basketball/baseball role is an NFL role.
    """
    adapted: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if contract.lane == NFL:
            position = _required_text(row, "position").upper()
            if position not in {"QB", "RB", "WR", "TE"}:
                raise FantasyScoreCalibrationEvidenceError(f"unsupported NFL DFS position: {position}")
            row["position"] = position
        else:
            row["position"] = "WR"  # validation-only sentinel; never part of cross-sport identity/hash.
        adapted.append(row)
    return adapted


def _candidate_hash(
    *,
    contract: FantasyScoreLaneContract,
    evidence_source_kind: str,
    evidence_dataset_sha256: str,
    base: Any,
) -> str:
    return _canonical_hash(
        {
            "lane": contract.lane,
            "market_family": contract.market_family,
            "controlling_specialist": contract.controlling_specialist,
            "evidence_source_kind": evidence_source_kind,
            "evidence_dataset_sha256": evidence_dataset_sha256,
            "model_version": base.model_version,
            "model_source_sha256": base.model_source_sha256,
            "scoring_profile_id": base.scoring_profile_id,
            "scoring_profile_sha256": base.scoring_profile_sha256,
            "calibration_method": base.calibration_method,
            "calibration_rows": base.calibration_rows,
            "holdout_rows": base.holdout_rows,
            "calibrated_holdout_metrics": base.calibrated_holdout_metrics,
            "oof_calibration_metrics": base.oof_calibration_metrics,
        }
    )


def build_fantasy_score_calibration_evidence(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    lane: str,
    evidence_source_kind: str,
    promotion_policy: PromotionPolicy | None = None,
    calibration_fraction: float = 0.80,
) -> FantasyScoreCalibrationEvidencePacket:
    """Build one lane-bound calibration evidence packet without promotion authority."""
    contract = lane_contract(lane)
    source = str(evidence_source_kind or "").strip().upper()
    if source not in SUPPORTED_EVIDENCE_SOURCES:
        raise FantasyScoreCalibrationEvidenceError(
            f"unsupported evidence_source_kind: {source or '<missing>'}"
        )

    _validate_lane_identity(raw_rows, contract=contract, evidence_source_kind=source)
    original_hash = _canonical_hash(
        {
            "lane_contract": asdict(contract),
            "evidence_source_kind": source,
            "rows": [dict(row) for row in raw_rows],
        }
    )

    try:
        base = _build_nfl_numerical_evidence(
            _delegate_rows(raw_rows, contract=contract),
            promotion_policy=promotion_policy,
            calibration_fraction=calibration_fraction,
        )
    except NflDfsCalibrationEvidenceError as exc:
        raise FantasyScoreCalibrationEvidenceError(str(exc)) from exc

    blockers = list(base.blockers)
    review_eligible = bool(base.certification_review_eligible)
    terminal_status = str(base.terminal_status)

    if source == SYNTHETIC_TEST_ONLY:
        if "SYNTHETIC_EVIDENCE_NOT_CERTIFIABLE" not in blockers:
            blockers.append("SYNTHETIC_EVIDENCE_NOT_CERTIFIABLE")
        review_eligible = False
        if terminal_status != "MODEL_INPUTS_INSUFFICIENT":
            terminal_status = "CALIBRATION_EVALUATED_SYNTHETIC_TEST_ONLY"

    return FantasyScoreCalibrationEvidencePacket(
        lane=contract.lane,
        sport=contract.sport,
        stat_type=contract.stat_type,
        market_family=contract.market_family,
        controlling_specialist=contract.controlling_specialist,
        evidence_source_kind=source,
        model_version=base.model_version,
        model_source_sha256=base.model_source_sha256,
        scoring_profile_id=base.scoring_profile_id,
        scoring_profile_sha256=base.scoring_profile_sha256,
        evidence_dataset_sha256=original_hash,
        calibration_candidate_sha256=_candidate_hash(
            contract=contract,
            evidence_source_kind=source,
            evidence_dataset_sha256=original_hash,
            base=base,
        ),
        total_rows=base.total_rows,
        binary_rows=base.binary_rows,
        pushes_excluded=base.pushes_excluded,
        calibration_rows=base.calibration_rows,
        holdout_rows=base.holdout_rows,
        calibration_events=base.calibration_events,
        holdout_events=base.holdout_events,
        calibration_end=base.calibration_end,
        holdout_start=base.holdout_start,
        fold_count=base.fold_count,
        calibration_method=base.calibration_method,
        raw_holdout_metrics=base.raw_holdout_metrics,
        calibrated_holdout_metrics=base.calibrated_holdout_metrics,
        oof_calibration_metrics=base.oof_calibration_metrics,
        promotion_policy_id=base.promotion_policy_id,
        certification_review_eligible=review_eligible,
        certification_status="CANDIDATE_ONLY",
        terminal_status=terminal_status,
        blockers=tuple(blockers),
        probability_publishable=False,
        rank_eligible=False,
        can_execute=False,
    )


def assess_promotion_readiness(
    packet: FantasyScoreCalibrationEvidencePacket,
    *,
    independent_certification_approved: bool = False,
    certification_approval_id: str | None = None,
    fitted_artifact_id: str | None = None,
    calibration_artifact_id: str | None = None,
    runtime_adapter_registered: bool = False,
    hydration_verified: bool = False,
    canonical_action_verified: bool = False,
) -> FantasyScorePromotionReadiness:
    """Evaluate promotion prerequisites without promoting, registering, or publishing.

    A true ``promotion_review_ready`` means an external governed promotion workflow
    may review the package.  It never grants publication or execution authority.
    """
    blockers: list[str] = []
    if packet.evidence_source_kind != IMMUTABLE_PREGAME_SETTLED:
        blockers.append("IMMUTABLE_SETTLED_EVIDENCE_REQUIRED")
    if not packet.certification_review_eligible:
        blockers.append("CALIBRATION_CERTIFICATION_REVIEW_NOT_ELIGIBLE")
    if not independent_certification_approved:
        blockers.append("INDEPENDENT_CERTIFICATION_APPROVAL_REQUIRED")
    if independent_certification_approved and not str(certification_approval_id or "").strip():
        blockers.append("CERTIFICATION_APPROVAL_ID_REQUIRED")
    if not str(fitted_artifact_id or "").strip():
        blockers.append("FITTED_ARTIFACT_ID_REQUIRED")
    if not str(calibration_artifact_id or "").strip():
        blockers.append("CALIBRATION_ARTIFACT_ID_REQUIRED")
    if not runtime_adapter_registered:
        blockers.append("RUNTIME_ADAPTER_REGISTRATION_REQUIRED")
    if not hydration_verified:
        blockers.append("RUNTIME_HYDRATION_VERIFICATION_REQUIRED")
    if not canonical_action_verified:
        blockers.append("CANONICAL_ACTION_VERIFICATION_REQUIRED")

    ready = not blockers
    return FantasyScorePromotionReadiness(
        lane=packet.lane,
        certification_review_eligible=packet.certification_review_eligible,
        independent_certification_approved=independent_certification_approved,
        certification_approval_id=certification_approval_id,
        fitted_artifact_id=fitted_artifact_id,
        calibration_artifact_id=calibration_artifact_id,
        runtime_adapter_registered=runtime_adapter_registered,
        hydration_verified=hydration_verified,
        canonical_action_verified=canonical_action_verified,
        promotion_review_ready=ready,
        terminal_status="PROMOTION_REVIEW_READY" if ready else "PROMOTION_PREREQUISITES_INCOMPLETE",
        blockers=tuple(blockers),
        governed_promotion_authorized=False,
        probability_publishable=False,
        rank_eligible=False,
        can_execute=False,
    )


__all__ = [
    "CAN_EXECUTE",
    "IMMUTABLE_PREGAME_SETTLED",
    "LANE_CONTRACTS",
    "MLB_HITTER",
    "MLB_PITCHER",
    "NBA",
    "NFL",
    "SYNTHETIC_TEST_ONLY",
    "SUPPORTED_EVIDENCE_SOURCES",
    "WNBA",
    "FantasyScoreCalibrationEvidenceError",
    "FantasyScoreCalibrationEvidencePacket",
    "FantasyScoreLaneContract",
    "FantasyScorePromotionReadiness",
    "PromotionPolicy",
    "assess_promotion_readiness",
    "build_fantasy_score_calibration_evidence",
    "lane_contract",
]
