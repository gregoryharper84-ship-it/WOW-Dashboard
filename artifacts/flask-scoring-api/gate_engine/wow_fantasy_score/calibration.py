from dataclasses import dataclass, asdict
from pathlib import Path
from math import log
import json

@dataclass(frozen=True)
class PredictionRecord:
    prediction_id: str
    created_at: str
    sport: str
    player: str
    line: float
    direction: str
    formula_version: str
    raw_probability: float
    calibrated_probability: float
    calibrated_lower_bound: float
    terminal_label: str
    model_status: str

@dataclass(frozen=True)
class OutcomeRecord:
    prediction_id: str
    settled_at: str
    outcome: int
    actual_fantasy_score: float
    settlement_source: str

class CalibrationLedger:
    def __init__(self, prediction_path, outcome_path):
        self.prediction_path, self.outcome_path = Path(prediction_path), Path(outcome_path)

    def _append(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def append_prediction(self, record):
        self._append(self.prediction_path, asdict(record))

    def append_outcome(self, record):
        self._append(self.outcome_path, asdict(record))

    @staticmethod
    def metrics(rows, bins=10):
        rows = list(rows)
        if not rows:
            raise ValueError("No settled predictions")
        pairs = [(min(max(p, 1e-6), 1-1e-6), int(y)) for p, y in rows]
        n = len(pairs)
        brier = sum((p-y)**2 for p,y in pairs)/n
        log_loss = -sum(y*log(p)+(1-y)*log(1-p) for p,y in pairs)/n
        mean_p = sum(p for p,_ in pairs)/n
        actual = sum(y for _,y in pairs)/n
        buckets = [[] for _ in range(bins)]
        for p,y in pairs:
            buckets[min(int(p*bins), bins-1)].append((p,y))
        ece = 0.0
        for b in buckets:
            if b:
                bp = sum(p for p,_ in b)/len(b)
                by = sum(y for _,y in b)/len(b)
                ece += len(b)/n * abs(bp-by)
        return {"count": n, "brier_score": brier, "log_loss": log_loss,
                "calibration_bias": mean_p-actual, "ece": ece}
