"""Paired champion/challenger shadow evaluation for WOW V17 Core Intelligence."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from math import log
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from v17.core_intelligence import AUTHORITY, CAN_EXECUTE
from v17.core_intelligence_compounding import PromotionReview, evaluate_promotion_review

SHADOW_SCHEMA_VERSION = "WOW17_SHADOW_LAB_V1"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    return float(value)


def _prob(value: Any) -> float | None:
    value = _number(value)
    if value is None or not 0.0 < value < 1.0:
        return None
    return value


def _loss(probability: float, target: int) -> float:
    clipped = min(max(probability, 1e-12), 1.0 - 1e-12)
    return -(target * log(clipped) + (1 - target) * log(1.0 - clipped))


def _identity(*parts: Any) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(str(part) for part in parts)))


@dataclass(frozen=True)
class ShadowRowEvidence:
    shadow_row_id: str
    challenger_id: str
    target_key: str
    source_row_id: str
    outcome_target: int
    champion_probability: float
    challenger_probability: float
    champion_brier: float
    challenger_brier: float
    champion_log_loss: float
    challenger_log_loss: float
    schema_version: str = SHADOW_SCHEMA_VERSION
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowAudit:
    challenger_id: str
    target_key: str
    holdout_n: int
    champion_brier: float
    challenger_brier: float
    brier_improvement: float
    champion_log_loss: float
    challenger_log_loss: float
    log_loss_improvement: float
    champion_calibration_error: float
    challenger_calibration_error: float
    calibration_not_worse: bool
    row_ids_unique: bool
    automatic_promotion: bool = False
    probability_publishable: bool = False
    authority: str = AUTHORITY
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_shadow_rows(
    *,
    challenger_id: str,
    target_key: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[ShadowRowEvidence, ...]:
    if not str(challenger_id or "").strip():
        raise ValueError("CHALLENGER_ID_REQUIRED")
    if not str(target_key or "").strip():
        raise ValueError("TARGET_KEY_REQUIRED")
    out: list[ShadowRowEvidence] = []
    seen: set[str] = set()
    for raw in rows:
        source_row_id = str(raw.get("source_row_id") or raw.get("prediction_id") or raw.get("event_id") or "").strip()
        if not source_row_id:
            raise ValueError("SHADOW_SOURCE_ROW_ID_REQUIRED")
        if source_row_id in seen:
            raise ValueError("SHADOW_DUPLICATE_SOURCE_ROW")
        seen.add(source_row_id)
        target = raw.get("outcome_target")
        if target not in (0, 1):
            raise ValueError("SHADOW_BINARY_OUTCOME_REQUIRED")
        champion = _prob(raw.get("champion_probability"))
        challenger = _prob(raw.get("challenger_probability"))
        if champion is None or challenger is None:
            raise ValueError("SHADOW_PROBABILITIES_REQUIRED")
        champion_brier = (target - champion) ** 2
        challenger_brier = (target - challenger) ** 2
        out.append(ShadowRowEvidence(
            shadow_row_id=_identity(SHADOW_SCHEMA_VERSION, challenger_id, target_key, source_row_id),
            challenger_id=str(challenger_id),
            target_key=str(target_key),
            source_row_id=source_row_id,
            outcome_target=int(target),
            champion_probability=champion,
            challenger_probability=challenger,
            champion_brier=champion_brier,
            challenger_brier=challenger_brier,
            champion_log_loss=_loss(champion, int(target)),
            challenger_log_loss=_loss(challenger, int(target)),
            can_execute=False,
        ))
    return tuple(out)


def summarize_shadow_rows(rows: Sequence[ShadowRowEvidence]) -> ShadowAudit:
    if not rows:
        raise ValueError("SHADOW_ROWS_EMPTY")
    challenger_ids = {row.challenger_id for row in rows}
    target_keys = {row.target_key for row in rows}
    if len(challenger_ids) != 1 or len(target_keys) != 1:
        raise ValueError("SHADOW_MIXED_COHORT")
    n = len(rows)
    champion_brier = sum(row.champion_brier for row in rows) / n
    challenger_brier = sum(row.challenger_brier for row in rows) / n
    champion_log = sum(row.champion_log_loss for row in rows) / n
    challenger_log = sum(row.challenger_log_loss for row in rows) / n
    observed = sum(row.outcome_target for row in rows) / n
    champion_mean = sum(row.champion_probability for row in rows) / n
    challenger_mean = sum(row.challenger_probability for row in rows) / n
    champion_calibration = abs(observed - champion_mean)
    challenger_calibration = abs(observed - challenger_mean)
    return ShadowAudit(
        challenger_id=rows[0].challenger_id,
        target_key=rows[0].target_key,
        holdout_n=n,
        champion_brier=champion_brier,
        challenger_brier=challenger_brier,
        brier_improvement=champion_brier - challenger_brier,
        champion_log_loss=champion_log,
        challenger_log_loss=challenger_log,
        log_loss_improvement=champion_log - challenger_log,
        champion_calibration_error=champion_calibration,
        challenger_calibration_error=challenger_calibration,
        calibration_not_worse=challenger_calibration <= champion_calibration,
        row_ids_unique=len({row.source_row_id for row in rows}) == n,
        can_execute=False,
    )


def evaluate_shadow_package(
    *,
    challenger_id: str,
    target_key: str,
    rows: Sequence[Mapping[str, Any]],
    source_review_pass: bool,
    certification_replay_pass: bool,
    min_holdout_n: int = 100,
    min_brier_improvement: float = 0.01,
) -> tuple[tuple[ShadowRowEvidence, ...], ShadowAudit, PromotionReview]:
    evidence = build_shadow_rows(
        challenger_id=challenger_id,
        target_key=target_key,
        rows=rows,
    )
    audit = summarize_shadow_rows(evidence)
    review = evaluate_promotion_review(
        challenger_id=challenger_id,
        target_key=target_key,
        holdout_n=audit.holdout_n,
        champion_brier=audit.champion_brier,
        challenger_brier=audit.challenger_brier,
        champion_log_loss=audit.champion_log_loss,
        challenger_log_loss=audit.challenger_log_loss,
        champion_calibration_error=audit.champion_calibration_error,
        challenger_calibration_error=audit.challenger_calibration_error,
        source_review_pass=source_review_pass,
        certification_replay_pass=certification_replay_pass,
        min_holdout_n=min_holdout_n,
        min_brier_improvement=min_brier_improvement,
    )
    return evidence, audit, review


__all__ = [
    "SHADOW_SCHEMA_VERSION",
    "ShadowRowEvidence",
    "ShadowAudit",
    "build_shadow_rows",
    "summarize_shadow_rows",
    "evaluate_shadow_package",
]
