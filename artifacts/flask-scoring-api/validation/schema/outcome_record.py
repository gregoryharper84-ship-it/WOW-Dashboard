"""
validation/schema/outcome_record.py

Post-game outcome attachment for a PredictionRecord.

Design invariants:
- An OutcomeRecord can only be attached AFTER the prediction is frozen:
  outcome_timestamp > prediction.frozen_at.
- actual_pitches is the observed first-inning pitch count (integer).
- hit is derived deterministically from (actual_pitches, prediction.line,
  prediction.direction) — never set manually to avoid transcription errors.
- The joined pair (PredictionRecord, OutcomeRecord) is the unit of evaluation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

OUTCOME_SCHEMA_VERSION = "1.0.0"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_hit(actual_pitches: int, line: float, direction: str) -> bool:
    """
    Determine whether the prop hit.

    LESS: hit  ↔  actual_pitches <  line
    MORE: hit  ↔  actual_pitches >  line
    Exact line (push) is treated as a miss for both sides (conservative).
    """
    if direction == "LESS":
        return actual_pitches < line
    elif direction == "MORE":
        return actual_pitches > line
    else:
        raise ValueError(f"direction must be LESS|MORE, got {direction!r}")


@dataclass(frozen=True)
class OutcomeRecord:
    """
    Immutable post-game outcome attachment.

    Fields
    ------
    prediction_id       Must match the parent PredictionRecord.prediction_id.
    schema_version      OUTCOME_SCHEMA_VERSION.
    outcome_timestamp   UTC ISO-8601 timestamp when outcome was recorded.
                        Must be strictly after PredictionRecord.frozen_at.
    actual_pitches      Observed first-inning pitch count (integer ≥ 0).
    hit                 Derived from (actual_pitches, line, direction).
                        True if the prop resolved in the predicted direction.
    outcome_source      Where the pitch count came from (e.g. "baseball_savant",
                        "mlb_stats_api", "manual").
    outcome_verified    True if cross-confirmed from a second source.
    notes               Optional free-text for data-quality observations.
    """
    prediction_id:     str
    schema_version:    str
    outcome_timestamp: str          # UTC ISO-8601; must be > frozen_at
    actual_pitches:    int
    hit:               bool         # derived, not user-supplied
    outcome_source:    str
    outcome_verified:  bool
    notes:             str = ""

    def to_dict(self) -> dict:
        return {
            "prediction_id":     self.prediction_id,
            "schema_version":    self.schema_version,
            "outcome_timestamp": self.outcome_timestamp,
            "actual_pitches":    self.actual_pitches,
            "hit":               self.hit,
            "outcome_source":    self.outcome_source,
            "outcome_verified":  self.outcome_verified,
            "notes":             self.notes,
        }


def attach_outcome(
    prediction,                          # PredictionRecord
    *,
    actual_pitches: int,
    outcome_source: str,
    outcome_verified: bool = False,
    notes: str = "",
    _outcome_timestamp: Optional[str] = None,   # injectable for tests
) -> OutcomeRecord:
    """
    Attach a post-game outcome to a frozen prediction.

    Enforces:
    - outcome_timestamp > prediction.frozen_at  (no post-outcome leakage)
    - hit derived deterministically from actual_pitches × line × direction

    Raises
    ------
    ValueError  if outcome_timestamp ≤ frozen_at (leakage guard).
    """
    outcome_ts = _outcome_timestamp or _now_utc()

    # Leakage guard: outcome must come after prediction freeze
    pred_dt    = datetime.fromisoformat(prediction.frozen_at.replace("Z", "+00:00"))
    outcome_dt = datetime.fromisoformat(outcome_ts.replace("Z", "+00:00"))
    if outcome_dt <= pred_dt:
        raise ValueError(
            f"Leakage: outcome_timestamp ({outcome_ts}) must be strictly "
            f"after prediction frozen_at ({prediction.frozen_at})."
        )

    hit = _derive_hit(actual_pitches, prediction.line, prediction.direction)

    return OutcomeRecord(
        prediction_id     = prediction.prediction_id,
        schema_version    = OUTCOME_SCHEMA_VERSION,
        outcome_timestamp = outcome_ts,
        actual_pitches    = actual_pitches,
        hit               = hit,
        outcome_source    = outcome_source,
        outcome_verified  = outcome_verified,
        notes             = notes,
    )
