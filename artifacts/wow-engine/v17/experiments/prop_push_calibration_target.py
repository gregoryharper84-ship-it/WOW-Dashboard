"""Research-only discrete-prop PUSH probability-target tournament.

Issue #1425.

The governed V17 discrete-prop scorer persists selected-side PMF mass as the
raw probability while separately persisting PUSH mass. The current prospective
binary calibrator excludes PUSH outcomes. This experiment compares that
CURRENT_CONTRACT with an explicit CONDITIONAL_NO_PUSH target, while also
reporting proper three-way diagnostics. It never registers or promotes a
calibrator and never changes production scoring.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np

from calibration import (
    CalibrationStatus,
    PHASE_B_MIN_N,
    PHASE_C_MIN_N,
    PHASE_C_MIN_PER_REGION,
    PlattFitMetrics,
    phase_b_platt,
    phase_c_fit_isotonic,
    phase_c_promote,
)

EXPERIMENT_VERSION = "V17_PROP_PUSH_CALIBRATION_TARGET_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
RANK_ELIGIBLE = False
MIN_HOLDOUT_ROWS = 30
MIN_TIME_FOLDS = 6
DEFAULT_HOLDOUT_FRACTION = 0.20
TARGET_CURRENT = "CURRENT_CONTRACT"
TARGET_CONDITIONAL = "CONDITIONAL_NO_PUSH"


class PropPushCalibrationTargetError(ValueError):
    pass


@dataclass(frozen=True)
class PushTargetRow:
    prediction_id: str
    event_id: str
    player: str
    direction: str
    exact_line: float
    selected_side_probability: float
    push_probability: float
    settlement: str
    prediction_timestamp: str
    event_start_timestamp: str
    outcome_available_at: str
    model_family: str
    model_artifact_version: str

    @property
    def event_key(self) -> tuple[datetime, str]:
        return (_aware(self.event_start_timestamp), self.event_id)

    @property
    def binary_outcome(self) -> int | None:
        if self.settlement == "PUSH":
            return None
        return 1 if self.settlement == "WIN" else 0

    @property
    def current_probability(self) -> float:
        return self.selected_side_probability

    @property
    def conditional_probability(self) -> float:
        non_push = 1.0 - self.push_probability
        if non_push <= 0.0:
            raise PropPushCalibrationTargetError("NON_PUSH_MASS_NON_POSITIVE")
        value = self.selected_side_probability / non_push
        if not 0.0 < value < 1.0:
            raise PropPushCalibrationTargetError("CONDITIONAL_PROBABILITY_OUT_OF_RANGE")
        return value

    @property
    def loss_probability(self) -> float:
        return max(0.0, 1.0 - self.selected_side_probability - self.push_probability)


@dataclass(frozen=True)
class TargetFit:
    target: str
    selected_method: str
    parameters: Mapping[str, Any]
    oof_metrics: Mapping[str, float]
    raw_holdout_metrics: Mapping[str, float]
    calibrated_holdout_metrics: Mapping[str, float]
    holdout_line_type_metrics: Mapping[str, Mapping[str, Any]]
    training_n: int
    holdout_n: int


@dataclass(frozen=True)
class PushTargetTournament:
    experiment_version: str
    model_family: str
    model_artifact_version: str
    total_rows: int
    push_rows: int
    binary_rows: int
    training_rows: int
    holdout_rows: int
    current_contract: TargetFit
    conditional_no_push: TargetFit
    three_way_metrics: Mapping[str, float]
    holdout_tail_bands: Mapping[str, Mapping[str, Any]]
    evidence_sha256: str
    probability_publishable: bool = False
    promotion_authorized: bool = False
    rank_eligible: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aware(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PropPushCalibrationTargetError("TIMESTAMP_INVALID") from exc
    if parsed.utcoffset() is None:
        raise PropPushCalibrationTargetError("TIMESTAMP_MUST_BE_AWARE")
    return parsed


def _prob(value: Any, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise PropPushCalibrationTargetError(f"{field}_INVALID") from exc
    if not math.isfinite(out) or not 0.0 <= out <= 1.0:
        raise PropPushCalibrationTargetError(f"{field}_OUT_OF_RANGE")
    return out


def _text(value: Any, field: str) -> str:
    out = str(value or "").strip()
    if not out:
        raise PropPushCalibrationTargetError(f"{field}_MISSING")
    return out


def _settlement(raw: Mapping[str, Any]) -> str:
    if bool(raw.get("void", False)):
        raise PropPushCalibrationTargetError("VOID_ROW_NOT_ELIGIBLE")
    value = str(raw.get("settlement") or raw.get("official_result") or "").strip().upper()
    if value in {"WIN", "WON", "SETTLED_WIN", "HIT"}:
        return "WIN"
    if value in {"LOSS", "LOST", "SETTLED_LOSS", "MISS"}:
        return "LOSS"
    if value == "PUSH" or raw.get("push") is True:
        return "PUSH"
    if raw.get("hit") is True:
        return "WIN"
    if raw.get("hit") is False and raw.get("push") is not True:
        return "LOSS"
    raise PropPushCalibrationTargetError("SETTLEMENT_INVALID")


def ingest_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[PushTargetRow, ...]:
    if not rows:
        raise PropPushCalibrationTargetError("SETTLED_ROWS_REQUIRED")
    output: list[PushTargetRow] = []
    identity: tuple[str, str, str] | None = None
    seen: set[tuple[str, str, float, str]] = set()
    for raw in rows:
        if bool(raw.get("market_probability_substitution_used", False)):
            raise PropPushCalibrationTargetError("MARKET_PROBABILITY_SUBSTITUTION_FORBIDDEN")
        try:
            market_weight = float(raw.get("market_prior_weight", 0.0))
        except (TypeError, ValueError) as exc:
            raise PropPushCalibrationTargetError("MARKET_PRIOR_WEIGHT_INVALID") from exc
        if abs(market_weight) > 1e-15:
            raise PropPushCalibrationTargetError("MARKET_PROBABILITY_SUBSTITUTION_FORBIDDEN")

        model_family = _text(raw.get("model_family"), "MODEL_FAMILY")
        artifact = _text(raw.get("model_artifact_version"), "MODEL_ARTIFACT_VERSION")
        direction = _text(raw.get("direction"), "DIRECTION").upper()
        if direction not in {"MORE", "LESS"}:
            raise PropPushCalibrationTargetError("DIRECTION_INVALID")
        if identity is None:
            identity = (model_family, artifact, direction)
        elif identity != (model_family, artifact, direction):
            raise PropPushCalibrationTargetError("MODEL_ARTIFACT_OR_DIRECTION_IDENTITY_MIXED")

        prediction_ts = _text(raw.get("prediction_timestamp"), "PREDICTION_TIMESTAMP")
        event_start_ts = _text(raw.get("event_start_timestamp"), "EVENT_START_TIMESTAMP")
        outcome_at = _text(raw.get("outcome_available_at"), "OUTCOME_AVAILABLE_AT")
        prediction_dt = _aware(prediction_ts)
        event_dt = _aware(event_start_ts)
        outcome_dt = _aware(outcome_at)
        if prediction_dt >= event_dt:
            raise PropPushCalibrationTargetError("PREDICTION_NOT_PREGAME")
        if outcome_dt < event_dt:
            raise PropPushCalibrationTargetError("OUTCOME_AVAILABLE_BEFORE_EVENT")

        p_win = _prob(raw.get("selected_side_probability"), "SELECTED_SIDE_PROBABILITY")
        p_push = _prob(raw.get("push_probability", 0.0), "PUSH_PROBABILITY")
        if p_win <= 0.0 or p_win >= 1.0:
            raise PropPushCalibrationTargetError("SELECTED_SIDE_PROBABILITY_STRICT_RANGE")
        if p_win + p_push >= 1.0 + 1e-12:
            raise PropPushCalibrationTargetError("WIN_PLUS_PUSH_GT_ONE")

        row = PushTargetRow(
            prediction_id=_text(raw.get("prediction_id"), "PREDICTION_ID"),
            event_id=_text(raw.get("event_id"), "EVENT_ID"),
            player=_text(raw.get("player"), "PLAYER"),
            direction=direction,
            exact_line=float(raw.get("exact_line", raw.get("line"))),
            selected_side_probability=p_win,
            push_probability=p_push,
            settlement=_settlement(raw),
            prediction_timestamp=prediction_ts,
            event_start_timestamp=event_start_ts,
            outcome_available_at=outcome_at,
            model_family=model_family,
            model_artifact_version=artifact,
        )
        if not math.isfinite(row.exact_line):
            raise PropPushCalibrationTargetError("EXACT_LINE_INVALID")
        key = (row.event_id, row.player.casefold(), row.exact_line, row.direction)
        if key in seen:
            raise PropPushCalibrationTargetError("DUPLICATE_EXACT_LINE_THESIS")
        seen.add(key)
        output.append(row)

    output.sort(key=lambda row: (row.event_key, row.player.casefold(), row.exact_line, row.direction))
    return tuple(output)


def _chronological_holdout(
    rows: Sequence[PushTargetRow],
    fraction: float,
) -> tuple[list[PushTargetRow], list[PushTargetRow]]:
    if not 0.10 <= float(fraction) <= 0.30:
        raise PropPushCalibrationTargetError("HOLDOUT_FRACTION_INVALID")
    binary = [row for row in rows if row.binary_outcome is not None]
    starts = sorted({row.event_key[0] for row in binary})
    if len(starts) < MIN_TIME_FOLDS + 1:
        raise PropPushCalibrationTargetError("DISTINCT_EVENT_TIMES_INSUFFICIENT")
    holdout_event_n = max(1, int(math.ceil(len(starts) * float(fraction))))
    while holdout_event_n >= 1:
        holdout_starts = set(starts[-holdout_event_n:])
        training = [row for row in binary if row.event_key[0] not in holdout_starts]
        holdout = [row for row in binary if row.event_key[0] in holdout_starts]
        if len(training) >= PHASE_B_MIN_N and len(holdout) >= MIN_HOLDOUT_ROWS:
            return training, holdout
        holdout_event_n -= 1
    raise PropPushCalibrationTargetError("PHASE_B_PLUS_HOLDOUT_EVIDENCE_INSUFFICIENT")


def _folds(rows: Sequence[PushTargetRow]) -> list[int]:
    starts = sorted({row.event_key[0] for row in rows})
    if len(starts) < MIN_TIME_FOLDS:
        raise PropPushCalibrationTargetError("TIME_FOLDS_INSUFFICIENT")
    index = {value: i for i, value in enumerate(starts)}
    count = len(starts)
    output = [
        min(MIN_TIME_FOLDS - 1, int(index[row.event_key[0]] * MIN_TIME_FOLDS / count))
        for row in rows
    ]
    if sorted(set(output)) != list(range(MIN_TIME_FOLDS)):
        raise PropPushCalibrationTargetError("TIME_FOLDS_NOT_CONTIGUOUS")
    return output


def _metrics(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict[str, float]:
    if not probabilities or len(probabilities) != len(outcomes):
        raise PropPushCalibrationTargetError("METRICS_INPUT_INVALID")
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-9, 1 - 1e-9)
    y = np.asarray(outcomes, dtype=float)
    brier = float(np.mean((p - y) ** 2))
    log_loss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    bias = float(np.mean(p - y))
    edges = np.linspace(0.0, 1.0, 11)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi if hi < 1.0 else p <= hi)
        if np.any(mask):
            ece += float(np.sum(mask)) / len(p) * abs(float(np.mean(p[mask]) - np.mean(y[mask])))
    return {"brier": brier, "log_loss": log_loss, "ece": ece, "calibration_bias": bias}


def _metrics_dict(metrics: PlattFitMetrics) -> dict[str, float]:
    return {
        "brier": float(metrics.brier),
        "log_loss": float(metrics.log_loss),
        "ece": float(metrics.ece),
        "calibration_bias": float(metrics.calibration_bias),
    }


def _region_counts(probabilities: Sequence[float]) -> list[int]:
    counts: list[int] = []
    for index in range(10):
        lo, hi = index / 10, (index + 1) / 10
        counts.append(sum(1 for p in probabilities if lo <= p < hi or (index == 9 and p == 1.0)))
    return counts


def _line_type_metrics(
    rows: Sequence[PushTargetRow],
    raw_probabilities: Sequence[float],
    calibrated_probabilities: Sequence[float],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for label, whole in (("HALF", False), ("WHOLE", True)):
        indices = [
            index
            for index, row in enumerate(rows)
            if (abs(row.exact_line - round(row.exact_line)) <= 1e-12) is whole
        ]
        if not indices:
            continue
        outcomes = [int(rows[index].binary_outcome) for index in indices]
        output[label] = {
            "n": len(indices),
            "mean_push_probability": sum(rows[index].push_probability for index in indices)
            / len(indices),
            "raw": _metrics([raw_probabilities[index] for index in indices], outcomes),
            "calibrated": _metrics(
                [calibrated_probabilities[index] for index in indices],
                outcomes,
            ),
        }
    return output


def _fit_target(
    target: str,
    training: Sequence[PushTargetRow],
    holdout: Sequence[PushTargetRow],
) -> TargetFit:
    if target == TARGET_CURRENT:
        getter = lambda row: row.current_probability
    elif target == TARGET_CONDITIONAL:
        getter = lambda row: row.conditional_probability
    else:
        raise PropPushCalibrationTargetError("TARGET_INVALID")

    raw_train = [getter(row) for row in training]
    y_train = [int(row.binary_outcome) for row in training]
    timestamps = [row.event_start_timestamp for row in training]
    fold_assignments = _folds(training)
    platt = phase_b_platt(raw_train, y_train, fold_assignments, timestamps)

    method = CalibrationStatus.PLATT_TIME_SPLIT_V1
    apply = platt.coefficients.apply
    params: dict[str, Any] = {"a": float(platt.coefficients.a), "b": float(platt.coefficients.b)}
    oof = platt.metrics

    if len(training) >= PHASE_C_MIN_N:
        counts = _region_counts(raw_train)
        if counts and all(count >= PHASE_C_MIN_PER_REGION for count in counts):
            isotonic = phase_c_fit_isotonic(raw_train, y_train, fold_assignments, timestamps)
            if phase_c_promote(isotonic.metrics, platt.metrics):
                method = CalibrationStatus.ISOTONIC_V1
                apply = lambda p: float(isotonic.model.predict([p])[0])
                params = {
                    "x_thresholds": [float(v) for v in isotonic.model.X_thresholds_],
                    "y_thresholds": [float(v) for v in isotonic.model.y_thresholds_],
                }
                oof = isotonic.metrics

    raw_holdout = [getter(row) for row in holdout]
    y_holdout = [int(row.binary_outcome) for row in holdout]
    calibrated = [apply(value) for value in raw_holdout]
    line_type_metrics = _line_type_metrics(holdout, raw_holdout, calibrated)
    return TargetFit(
        target=target,
        selected_method=str(method),
        parameters=params,
        oof_metrics=_metrics_dict(oof),
        raw_holdout_metrics=_metrics(raw_holdout, y_holdout),
        calibrated_holdout_metrics=_metrics(calibrated, y_holdout),
        holdout_line_type_metrics=line_type_metrics,
        training_n=len(training),
        holdout_n=len(holdout),
    )


def _three_way_metrics(rows: Sequence[PushTargetRow]) -> dict[str, float]:
    if not rows:
        raise PropPushCalibrationTargetError("THREE_WAY_ROWS_EMPTY")
    brier_total = 0.0
    log_total = 0.0
    eps = 1e-12
    for row in rows:
        probs = (row.selected_side_probability, row.push_probability, row.loss_probability)
        target = (
            (1.0, 0.0, 0.0)
            if row.settlement == "WIN"
            else (0.0, 1.0, 0.0)
            if row.settlement == "PUSH"
            else (0.0, 0.0, 1.0)
        )
        brier_total += sum((p - y) ** 2 for p, y in zip(probs, target))
        observed_p = probs[0] if row.settlement == "WIN" else probs[1] if row.settlement == "PUSH" else probs[2]
        log_total += -math.log(max(observed_p, eps))
    return {
        "n": float(len(rows)),
        "brier": brier_total / len(rows),
        "log_loss": log_total / len(rows),
        "push_rate": sum(1 for row in rows if row.settlement == "PUSH") / len(rows),
    }


def _tail_bands(rows: Sequence[PushTargetRow]) -> dict[str, dict[str, Any]]:
    bands = {
        "P_LT_0_40": lambda p: p < 0.40,
        "P_0_40_0_50": lambda p: 0.40 <= p < 0.50,
        "P_0_50_0_60": lambda p: 0.50 <= p < 0.60,
        "P_GE_0_60": lambda p: p >= 0.60,
    }
    output: dict[str, dict[str, Any]] = {}
    for name, predicate in bands.items():
        subset = [row for row in rows if row.binary_outcome is not None and predicate(row.current_probability)]
        if not subset:
            output[name] = {"n": 0, "mean_probability": None, "hit_rate": None, "gap": None}
            continue
        mean_p = sum(row.current_probability for row in subset) / len(subset)
        hit = sum(int(row.binary_outcome) for row in subset) / len(subset)
        output[name] = {
            "n": len(subset),
            "mean_probability": mean_p,
            "hit_rate": hit,
            "gap": hit - mean_p,
        }
    return output


def run_tournament(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> PushTargetTournament:
    rows = ingest_rows(raw_rows)
    training, holdout = _chronological_holdout(rows, holdout_fraction)
    current = _fit_target(TARGET_CURRENT, training, holdout)
    conditional = _fit_target(TARGET_CONDITIONAL, training, holdout)

    holdout_event_times = {row.event_key[0] for row in holdout}
    all_holdout = [row for row in rows if row.event_key[0] in holdout_event_times]
    payload = {
        "version": EXPERIMENT_VERSION,
        "model_family": rows[0].model_family,
        "model_artifact_version": rows[0].model_artifact_version,
        "row_ids": [row.prediction_id for row in rows],
        "current": asdict(current),
        "conditional": asdict(conditional),
    }
    evidence_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode("utf-8")
    ).hexdigest()

    return PushTargetTournament(
        experiment_version=EXPERIMENT_VERSION,
        model_family=rows[0].model_family,
        model_artifact_version=rows[0].model_artifact_version,
        total_rows=len(rows),
        push_rows=sum(1 for row in rows if row.settlement == "PUSH"),
        binary_rows=sum(1 for row in rows if row.binary_outcome is not None),
        training_rows=len(training),
        holdout_rows=len(holdout),
        current_contract=current,
        conditional_no_push=conditional,
        three_way_metrics=_three_way_metrics(all_holdout),
        holdout_tail_bands=_tail_bands(holdout),
        evidence_sha256=evidence_hash,
    )


__all__ = [
    "CAN_EXECUTE",
    "EXPERIMENT_VERSION",
    "PROBABILITY_PUBLISHABLE",
    "PROMOTION_AUTHORIZED",
    "PropPushCalibrationTargetError",
    "PushTargetRow",
    "PushTargetTournament",
    "RANK_ELIGIBLE",
    "TARGET_CONDITIONAL",
    "TARGET_CURRENT",
    "TERMINAL_AUTHORITY",
    "ingest_rows",
    "run_tournament",
]
