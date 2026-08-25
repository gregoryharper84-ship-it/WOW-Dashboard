"""
gate_engine/universal_agent/model_validation/calibration_scoreboard.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Family-Specific Calibration Scoreboard.

Tracks Brier scores per (sport, stat_key, model_id) and aggregates them
at the model-family level. Purely advisory — no automatic actions.

Brier score = mean( (predicted_prob − actual_outcome)² ) over settled bets.
Lower = better calibrated. 0.0 = perfect. 0.25 = random baseline (p=0.5).

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


@dataclass
class BrierRecord:
    """Running Brier score accumulator for one (sport, stat_key, model_id)."""
    sport:        str
    stat_key:     str
    model_id:     str
    model_family: str
    n_settled:    int   = 0
    sum_sq_error: float = 0.0

    @property
    def brier_score(self) -> float | None:
        if self.n_settled == 0:
            return None
        return self.sum_sq_error / self.n_settled

    def record(self, predicted_prob: float, actual_outcome: float) -> None:
        """
        Record one settled observation.
        predicted_prob ∈ [0, 1], actual_outcome ∈ {0.0, 1.0}.
        """
        if not (0.0 <= predicted_prob <= 1.0):
            raise ValueError(f"predicted_prob must be ∈ [0, 1], got {predicted_prob}")
        if actual_outcome not in (0.0, 1.0, 0, 1):
            raise ValueError(f"actual_outcome must be 0 or 1, got {actual_outcome}")
        self.sum_sq_error += (predicted_prob - float(actual_outcome)) ** 2
        self.n_settled    += 1


class CalibrationScoreboard:
    """
    Advisory-only calibration tracker.
    can_execute = False. No automatic retraining or model switching.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], BrierRecord] = {}

    def _key(self, sport: str, stat_key: str, model_id: str) -> tuple[str, str, str]:
        return (sport.upper().strip(), stat_key.lower().strip(), model_id.strip())

    def record_observation(
        self,
        *,
        sport:          str,
        stat_key:       str,
        model_id:       str,
        model_family:   str,
        predicted_prob: float,
        actual_outcome: float,
    ) -> None:
        """Record one settled observation. Creates the record on first call."""
        k = self._key(sport, stat_key, model_id)
        if k not in self._records:
            self._records[k] = BrierRecord(
                sport=sport, stat_key=stat_key,
                model_id=model_id, model_family=model_family,
            )
        self._records[k].record(predicted_prob, actual_outcome)

    def get_brier(self, sport: str, stat_key: str, model_id: str) -> float | None:
        k = self._key(sport, stat_key, model_id)
        rec = self._records.get(k)
        return rec.brier_score if rec else None

    def family_summary(self, model_family: str) -> dict[str, Any]:
        """
        Return aggregated Brier stats for a model family.
        Weighted by n_settled (each observation contributes equally).
        """
        total_sq = 0.0
        total_n  = 0
        slots: list[dict] = []
        for rec in self._records.values():
            if rec.model_family != model_family:
                continue
            slots.append({
                "sport": rec.sport, "stat_key": rec.stat_key,
                "model_id": rec.model_id, "n_settled": rec.n_settled,
                "brier_score": rec.brier_score,
            })
            total_sq += rec.sum_sq_error
            total_n  += rec.n_settled

        family_brier = (total_sq / total_n) if total_n > 0 else None
        return {
            "model_family":  model_family,
            "family_brier":  family_brier,
            "total_settled": total_n,
            "slots":         slots,
            "can_execute":   False,
        }

    def all_scores(self) -> list[dict[str, Any]]:
        return [
            {
                "sport": r.sport, "stat_key": r.stat_key,
                "model_id": r.model_id, "model_family": r.model_family,
                "n_settled": r.n_settled, "brier_score": r.brier_score,
            }
            for r in self._records.values()
        ]
