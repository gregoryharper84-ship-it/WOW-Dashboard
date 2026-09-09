"""Offline calibration-evidence builder for the governed NFL DFS lane.

This module does not make the NFL DFS candidate model production-certified and
never publishes probability. It consumes immutable settled exact-line prediction
rows, verifies pregame identity/chronology, reserves an untouched event-level
holdout, fits only the ratified WOW calibration ladder from calibration.py, and
emits a candidate-only evidence packet for later independent certification.

Synthetic rows may exercise this module in tests, but synthetic evidence must
never be used to set a production lifecycle state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np

from calibration import (
    CalibrationStatus,
    PHASE_B_MIN_N,
    PlattFitMetrics,
    phase_b_platt,
    phase_c_fit_isotonic,
    phase_c_isotonic_eligible,
    phase_c_promote,
)

CAN_EXECUTE = False
MARKET_FAMILY = "NFL_DFS_FANTASY_SCORE"
CONTROLLING_SPECIALIST = "wow.nfl-dfs-fantasy-score-expert"
MIN_SIMULATIONS = 50_000
MIN_TIME_FOLDS = 6
DEFAULT_CALIBRATION_FRACTION = 0.80


class NflDfsCalibrationEvidenceError(ValueError):
    """Deterministic validation failure for candidate calibration evidence."""


@dataclass(frozen=True)
class SettledExactLineRow:
    event_id: str
    player_id: str
    position: str
    exact_line: float
    side: str
    raw_candidate_probability: float
    realized_fantasy_score: float
    prediction_timestamp: str
    event_start_timestamp: str
    model_version: str
    model_source_sha256: str
    scoring_profile_id: str
    scoring_profile_sha256: str
    simulation_count: int
    seed: int
    p_more: float | None = None
    p_less: float | None = None
    p_push: float | None = None

    @property
    def event_key(self) -> tuple[datetime, str]:
        return (_parse_aware(self.event_start_timestamp), self.event_id)

    @property
    def outcome(self) -> int | None:
        if self.realized_fantasy_score == self.exact_line:
            return None
        won = (
            self.realized_fantasy_score > self.exact_line
            if self.side == "MORE"
            else self.realized_fantasy_score < self.exact_line
        )
        return 1 if won else 0


@dataclass(frozen=True)
class PromotionPolicy:
    """Externally supplied certification-review thresholds.

    The evidence builder intentionally has no built-in promotion thresholds.
    Governance/certification must provide them explicitly rather than letting an
    offline fitter invent a new policy.
    """

    policy_id: str
    minimum_total_rows: int
    minimum_holdout_rows: int
    maximum_brier: float
    maximum_log_loss: float
    maximum_ece: float
    require_brier_improvement_over_raw: bool = True


@dataclass(frozen=True)
class CalibrationEvidencePacket:
    market_family: str
    controlling_specialist: str
    model_version: str
    model_source_sha256: str
    scoring_profile_id: str
    scoring_profile_sha256: str
    evidence_dataset_sha256: str
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


def _parse_aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise NflDfsCalibrationEvidenceError(f"invalid ISO timestamp: {value!r}") from exc
    if parsed.utcoffset() is None:
        raise NflDfsCalibrationEvidenceError("governed timestamps must be timezone-aware")
    return parsed


def _finite(value: Any, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise NflDfsCalibrationEvidenceError(f"{field} must be numeric") from exc
    if not math.isfinite(out):
        raise NflDfsCalibrationEvidenceError(f"{field} must be finite")
    return out


def _probability(value: Any, field: str) -> float:
    out = _finite(value, field)
    if not 0.0 <= out <= 1.0:
        raise NflDfsCalibrationEvidenceError(f"{field} must be in [0, 1]")
    return out


def _required_text(row: Mapping[str, Any], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise NflDfsCalibrationEvidenceError(f"{field} is required")
    return value


def _sha256_text(value: str, field: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise NflDfsCalibrationEvidenceError(f"{field} must be a 64-character SHA-256 hex digest")
    return normalized


def ingest_settled_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[SettledExactLineRow, ...]:
    """Validate immutable exact-line rows without silently repairing identities."""
    if not rows:
        raise NflDfsCalibrationEvidenceError("settled historical rows are required")

    ingested: list[SettledExactLineRow] = []
    for raw in rows:
        side = _required_text(raw, "side").upper()
        if side not in {"MORE", "LESS"}:
            raise NflDfsCalibrationEvidenceError("side must be MORE or LESS")
        prediction_ts = _required_text(raw, "prediction_timestamp")
        event_start_ts = _required_text(raw, "event_start_timestamp")
        prediction_dt = _parse_aware(prediction_ts)
        event_start_dt = _parse_aware(event_start_ts)
        if not prediction_dt < event_start_dt:
            raise NflDfsCalibrationEvidenceError(
                "prediction_timestamp must be strictly before event_start_timestamp"
            )

        simulation_count = int(raw.get("simulation_count") or 0)
        if simulation_count < MIN_SIMULATIONS:
            raise NflDfsCalibrationEvidenceError(
                f"simulation_count must be >= {MIN_SIMULATIONS}"
            )

        p_more = raw.get("p_more")
        p_less = raw.get("p_less")
        p_push = raw.get("p_push")
        probabilities_present = any(value is not None for value in (p_more, p_less, p_push))
        if probabilities_present:
            if any(value is None for value in (p_more, p_less, p_push)):
                raise NflDfsCalibrationEvidenceError(
                    "p_more, p_less, and p_push must be supplied together"
                )
            p_more = _probability(p_more, "p_more")
            p_less = _probability(p_less, "p_less")
            p_push = _probability(p_push, "p_push")
            if abs((p_more + p_less + p_push) - 1.0) > 1e-8:
                raise NflDfsCalibrationEvidenceError("P(MORE)+P(LESS)+P(PUSH) must equal 1")

        row = SettledExactLineRow(
            event_id=_required_text(raw, "event_id"),
            player_id=_required_text(raw, "player_id"),
            position=_required_text(raw, "position").upper(),
            exact_line=_finite(raw.get("exact_line"), "exact_line"),
            side=side,
            raw_candidate_probability=_probability(
                raw.get("raw_candidate_probability"), "raw_candidate_probability"
            ),
            realized_fantasy_score=_finite(
                raw.get("realized_fantasy_score"), "realized_fantasy_score"
            ),
            prediction_timestamp=prediction_ts,
            event_start_timestamp=event_start_ts,
            model_version=_required_text(raw, "model_version"),
            model_source_sha256=_sha256_text(
                _required_text(raw, "model_source_sha256"), "model_source_sha256"
            ),
            scoring_profile_id=_required_text(raw, "scoring_profile_id"),
            scoring_profile_sha256=_sha256_text(
                _required_text(raw, "scoring_profile_sha256"), "scoring_profile_sha256"
            ),
            simulation_count=simulation_count,
            seed=int(raw.get("seed") or 0),
            p_more=p_more,
            p_less=p_less,
            p_push=p_push,
        )
        ingested.append(row)

    _assert_single_identity(ingested)
    return tuple(sorted(ingested, key=lambda row: (row.event_key, row.player_id, row.exact_line, row.side)))


def _assert_single_identity(rows: Sequence[SettledExactLineRow]) -> None:
    identities = {
        (
            row.model_version,
            row.model_source_sha256,
            row.scoring_profile_id,
            row.scoring_profile_sha256,
        )
        for row in rows
    }
    if len(identities) != 1:
        raise NflDfsCalibrationEvidenceError(
            "calibration cohort cannot mix model/source/scoring-profile identities"
        )


def _dataset_hash(rows: Sequence[SettledExactLineRow]) -> str:
    payload = json.dumps(
        [asdict(row) for row in rows],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _metrics(probs: Sequence[float], outcomes: Sequence[int]) -> PlattFitMetrics:
    p = np.clip(np.asarray(probs, dtype=float), 1e-9, 1 - 1e-9)
    y = np.asarray(outcomes, dtype=float)
    if len(p) == 0:
        raise NflDfsCalibrationEvidenceError("cannot compute metrics on an empty cohort")
    brier = float(np.mean((p - y) ** 2))
    log_loss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    calibration_bias = float(np.mean(p - y))
    edges = np.linspace(0.0, 1.0, 11)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi if hi < 1.0 else p <= hi)
        if np.any(mask):
            ece += (float(np.sum(mask)) / len(p)) * abs(float(np.mean(p[mask]) - np.mean(y[mask])))
    return PlattFitMetrics(
        brier=brier,
        log_loss=log_loss,
        ece=ece,
        calibration_bias=calibration_bias,
    )


def _metrics_dict(metrics: PlattFitMetrics) -> dict[str, float]:
    return {
        "brier": float(metrics.brier),
        "log_loss": float(metrics.log_loss),
        "ece": float(metrics.ece),
        "calibration_bias": float(metrics.calibration_bias),
    }


def _event_level_holdout(
    rows: Sequence[SettledExactLineRow],
    fraction: float,
) -> tuple[list[SettledExactLineRow], list[SettledExactLineRow]]:
    if not 0.60 <= fraction <= 0.90:
        raise NflDfsCalibrationEvidenceError("calibration_fraction must be in [0.60, 0.90]")
    events = sorted({row.event_key for row in rows})
    if len(events) < 7:
        raise NflDfsCalibrationEvidenceError("at least seven chronological events are required")
    cut = int(len(events) * fraction)
    cut = min(max(cut, 6), len(events) - 1)
    calibration_events = set(events[:cut])
    holdout_events = set(events[cut:])
    calibration = [row for row in rows if row.event_key in calibration_events]
    holdout = [row for row in rows if row.event_key in holdout_events]
    return calibration, holdout


def _fold_assignments(rows: Sequence[SettledExactLineRow], fold_count: int = MIN_TIME_FOLDS) -> list[int]:
    events = sorted({row.event_key for row in rows})
    if len(events) < fold_count:
        raise NflDfsCalibrationEvidenceError(
            f"at least {fold_count} calibration events are required for time folds"
        )
    event_to_fold: dict[tuple[datetime, str], int] = {}
    for index, event in enumerate(events):
        fold = min(fold_count - 1, (index * fold_count) // len(events))
        event_to_fold[event] = fold
    assignments = [event_to_fold[row.event_key] for row in rows]
    if sorted(set(assignments)) != list(range(fold_count)):
        raise NflDfsCalibrationEvidenceError("chronological fold construction failed")
    return assignments


def _region_counts(probs: Sequence[float]) -> list[int]:
    counts = [0] * 10
    for value in probs:
        index = min(9, max(0, int(float(value) * 10)))
        counts[index] += 1
    return [count for count in counts if count > 0]


def _policy_blockers(
    policy: PromotionPolicy,
    *,
    total_rows: int,
    holdout_rows: int,
    raw: PlattFitMetrics,
    calibrated: PlattFitMetrics,
) -> list[str]:
    blockers: list[str] = []
    if total_rows < policy.minimum_total_rows:
        blockers.append("PROMOTION_POLICY_MIN_TOTAL_ROWS_NOT_MET")
    if holdout_rows < policy.minimum_holdout_rows:
        blockers.append("PROMOTION_POLICY_MIN_HOLDOUT_ROWS_NOT_MET")
    if calibrated.brier > policy.maximum_brier:
        blockers.append("PROMOTION_POLICY_BRIER_NOT_MET")
    if calibrated.log_loss > policy.maximum_log_loss:
        blockers.append("PROMOTION_POLICY_LOG_LOSS_NOT_MET")
    if calibrated.ece > policy.maximum_ece:
        blockers.append("PROMOTION_POLICY_ECE_NOT_MET")
    if policy.require_brier_improvement_over_raw and calibrated.brier >= raw.brier:
        blockers.append("PROMOTION_POLICY_NO_BRIER_IMPROVEMENT")
    return blockers


def build_calibration_evidence(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    promotion_policy: PromotionPolicy | None = None,
    calibration_fraction: float = DEFAULT_CALIBRATION_FRACTION,
) -> CalibrationEvidencePacket:
    """Build candidate-only held-out calibration evidence.

    Pushes are preserved in the immutable dataset hash/audit counts but excluded
    from binary win-probability fitting. No result from this function is a
    certification receipt or a publishable governed probability package.
    """
    rows = ingest_settled_rows(raw_rows)
    identity = rows[0]
    dataset_hash = _dataset_hash(rows)
    binary = [row for row in rows if row.outcome is not None]
    pushes = len(rows) - len(binary)

    blockers: list[str] = []
    if len(binary) < PHASE_B_MIN_N:
        blockers.append(f"PHASE_B_REQUIRES_{PHASE_B_MIN_N}_SETTLED_BINARY_ROWS")
        return CalibrationEvidencePacket(
            market_family=MARKET_FAMILY,
            controlling_specialist=CONTROLLING_SPECIALIST,
            model_version=identity.model_version,
            model_source_sha256=identity.model_source_sha256,
            scoring_profile_id=identity.scoring_profile_id,
            scoring_profile_sha256=identity.scoring_profile_sha256,
            evidence_dataset_sha256=dataset_hash,
            total_rows=len(rows),
            binary_rows=len(binary),
            pushes_excluded=pushes,
            calibration_rows=0,
            holdout_rows=0,
            calibration_events=0,
            holdout_events=0,
            calibration_end="",
            holdout_start="",
            fold_count=0,
            calibration_method=None,
            raw_holdout_metrics=None,
            calibrated_holdout_metrics=None,
            oof_calibration_metrics=None,
            promotion_policy_id=promotion_policy.policy_id if promotion_policy else None,
            certification_review_eligible=False,
            certification_status="CANDIDATE_ONLY",
            terminal_status="MODEL_INPUTS_INSUFFICIENT",
            blockers=tuple(blockers),
        )

    calibration, holdout = _event_level_holdout(binary, calibration_fraction)
    if len(calibration) < PHASE_B_MIN_N:
        blockers.append(f"CALIBRATION_COHORT_REQUIRES_{PHASE_B_MIN_N}_ROWS")
    if not holdout:
        blockers.append("UNTOUCHED_HOLDOUT_EMPTY")
    if blockers:
        return CalibrationEvidencePacket(
            market_family=MARKET_FAMILY,
            controlling_specialist=CONTROLLING_SPECIALIST,
            model_version=identity.model_version,
            model_source_sha256=identity.model_source_sha256,
            scoring_profile_id=identity.scoring_profile_id,
            scoring_profile_sha256=identity.scoring_profile_sha256,
            evidence_dataset_sha256=dataset_hash,
            total_rows=len(rows),
            binary_rows=len(binary),
            pushes_excluded=pushes,
            calibration_rows=len(calibration),
            holdout_rows=len(holdout),
            calibration_events=len({row.event_key for row in calibration}),
            holdout_events=len({row.event_key for row in holdout}),
            calibration_end=max(row.event_start_timestamp for row in calibration),
            holdout_start=min(row.event_start_timestamp for row in holdout),
            fold_count=0,
            calibration_method=None,
            raw_holdout_metrics=None,
            calibrated_holdout_metrics=None,
            oof_calibration_metrics=None,
            promotion_policy_id=promotion_policy.policy_id if promotion_policy else None,
            certification_review_eligible=False,
            certification_status="CANDIDATE_ONLY",
            terminal_status="MODEL_INPUTS_INSUFFICIENT",
            blockers=tuple(blockers),
        )

    folds = _fold_assignments(calibration)
    raw_probs = [row.raw_candidate_probability for row in calibration]
    outcomes = [int(row.outcome) for row in calibration]
    timestamps = [row.event_start_timestamp for row in calibration]
    platt = phase_b_platt(raw_probs, outcomes, folds, timestamps)

    selected_method = CalibrationStatus.PLATT_TIME_SPLIT_V1
    selected_model: Any = platt.coefficients
    selected_oof = platt.metrics

    if phase_c_isotonic_eligible(len(calibration), _region_counts(raw_probs)):
        isotonic = phase_c_fit_isotonic(raw_probs, outcomes, folds, timestamps)
        if phase_c_promote(isotonic.metrics, platt.metrics):
            selected_method = CalibrationStatus.ISOTONIC_V1
            selected_model = isotonic.model
            selected_oof = isotonic.metrics

    holdout_raw = [row.raw_candidate_probability for row in holdout]
    holdout_y = [int(row.outcome) for row in holdout]
    if selected_method == CalibrationStatus.PLATT_TIME_SPLIT_V1:
        holdout_calibrated = [selected_model.apply(value) for value in holdout_raw]
    else:
        holdout_calibrated = [float(value) for value in selected_model.predict(holdout_raw)]

    raw_metrics = _metrics(holdout_raw, holdout_y)
    calibrated_metrics = _metrics(holdout_calibrated, holdout_y)

    if promotion_policy is None:
        blockers.append("PROMOTION_POLICY_NOT_CONFIGURED")
    else:
        blockers.extend(
            _policy_blockers(
                promotion_policy,
                total_rows=len(binary),
                holdout_rows=len(holdout),
                raw=raw_metrics,
                calibrated=calibrated_metrics,
            )
        )

    certification_review_eligible = promotion_policy is not None and not blockers
    terminal_status = (
        "CERTIFICATION_REVIEW_ELIGIBLE"
        if certification_review_eligible
        else "CALIBRATION_EVALUATED_CANDIDATE_ONLY"
        if promotion_policy is None
        else "CALIBRATION_EVALUATED_POLICY_BLOCKED"
    )

    return CalibrationEvidencePacket(
        market_family=MARKET_FAMILY,
        controlling_specialist=CONTROLLING_SPECIALIST,
        model_version=identity.model_version,
        model_source_sha256=identity.model_source_sha256,
        scoring_profile_id=identity.scoring_profile_id,
        scoring_profile_sha256=identity.scoring_profile_sha256,
        evidence_dataset_sha256=dataset_hash,
        total_rows=len(rows),
        binary_rows=len(binary),
        pushes_excluded=pushes,
        calibration_rows=len(calibration),
        holdout_rows=len(holdout),
        calibration_events=len({row.event_key for row in calibration}),
        holdout_events=len({row.event_key for row in holdout}),
        calibration_end=max(row.event_start_timestamp for row in calibration),
        holdout_start=min(row.event_start_timestamp for row in holdout),
        fold_count=len(set(folds)),
        calibration_method=selected_method,
        raw_holdout_metrics=_metrics_dict(raw_metrics),
        calibrated_holdout_metrics=_metrics_dict(calibrated_metrics),
        oof_calibration_metrics=_metrics_dict(selected_oof),
        promotion_policy_id=promotion_policy.policy_id if promotion_policy else None,
        certification_review_eligible=certification_review_eligible,
        certification_status="CANDIDATE_ONLY",
        terminal_status=terminal_status,
        blockers=tuple(blockers),
    )
