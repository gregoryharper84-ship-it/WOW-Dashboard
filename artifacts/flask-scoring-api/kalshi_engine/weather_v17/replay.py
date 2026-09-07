"""Offline historical replay for Weather V17. No network calls or production mutation."""
from __future__ import annotations

import math
from typing import Any

from .core import WeatherV17Engine

EPS = 1e-12


def replay_rows(rows: list[dict[str, Any]], engine: WeatherV17Engine | None = None) -> dict[str, Any]:
    engine = engine or WeatherV17Engine()
    results = []
    briers, log_losses, temp_maes = [], [], []
    for row in rows:
        package = engine.score(dict(row.get("input") or {}))
        outcome = row.get("outcome") or {}
        official_high = outcome.get("official_final_high_f")
        contract_yes = outcome.get("contract_yes")
        scored = {"id": row.get("id"), "package": package, "outcome": outcome}
        if package.get("probability_status") == "COMPLETED" and contract_yes is not None:
            p = package.get("calibrated_probability")
            if p is None: p = package.get("raw_probability")
            p = max(EPS, min(1.0 - EPS, float(p)))
            y = 1.0 if bool(contract_yes) else 0.0
            brier = (p - y) ** 2
            log_loss = -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
            scored["brier_score"], scored["log_loss"] = brier, log_loss
            briers.append(brier); log_losses.append(log_loss)
        if package.get("probability_status") == "COMPLETED" and official_high is not None:
            mean_f = package.get("distribution_summary", {}).get("mean_f")
            if mean_f is not None:
                mae = abs(float(mean_f) - float(official_high)); scored["final_high_mae"] = mae; temp_maes.append(mae)
            pmf = package.get("final_high_pmf") or {}
            scored["official_high_probability"] = float(pmf.get(str(int(round(float(official_high)))), 0.0))
        results.append(scored)
    def avg(vals: list[float]) -> float | None: return sum(vals) / len(vals) if vals else None
    return {"rows": results, "summary": {"replay_count": len(rows), "scored_count": sum(1 for r in results if r["package"].get("probability_status") == "COMPLETED"),
        "mean_brier_score": avg(briers), "mean_log_loss": avg(log_losses), "mean_final_high_mae": avg(temp_maes),
        "mode": "OFFLINE_REPLAY", "can_execute": False}}
