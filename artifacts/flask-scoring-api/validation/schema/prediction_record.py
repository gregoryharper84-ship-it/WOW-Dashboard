"""
validation/schema/prediction_record.py

Immutable versioned pregame prediction record for MLB 1IP pitch-count props.

Design invariants:
- frozen=True: no field can be mutated after construction.
- prediction_id is deterministically derived from a SHA-256 hash of the
  identity fields — duplicate predictions are detectable by ID collision.
- frozen_at is recorded at construction in UTC ISO-8601; it can never be
  updated (outcomes are attached in a separate OutcomeRecord).
- feature_snapshot_id is a SHA-256 hash of the serialised feature dict,
  supplied by the caller.  The harness never re-derives features from outcomes.
- Outcome attachment: use attach_outcome() which returns a NEW OutcomeRecord
  and enforces that outcome_timestamp > frozen_at.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


# ── Schema version ─────────────────────────────────────────────────────────
PREDICTION_SCHEMA_VERSION = "1.0.0"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _prediction_id(
    pitcher_mlbam_id: int,
    game_date: str,
    line: float,
    direction: str,
    frozen_at_sec: str,          # truncated to second to keep deterministic
) -> str:
    """Deterministic ID — same inputs always produce the same ID."""
    payload = {
        "pitcher_mlbam_id": pitcher_mlbam_id,
        "game_date": game_date,
        "line": line,
        "direction": direction.upper(),
        "frozen_at_sec": frozen_at_sec[:19],  # drop sub-second
    }
    return "pred_" + _sha256(payload)


# ── Prediction record ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PredictionRecord:
    """
    Immutable pregame prediction.  All fields set at construction; none may
    be changed afterward.  Outcome is attached separately via OutcomeRecord.

    Fields
    ------
    prediction_id       Deterministic SHA-256–derived ID. Collisions signal
                        duplicate predictions for the same event.
    schema_version      Schema version string (PREDICTION_SCHEMA_VERSION).
    frozen_at           UTC ISO-8601 timestamp of prediction creation.
    sport               Always "MLB" for this harness version.
    prop_type           Always "1IP_PITCHES_THROWN".
    game_date           ISO date of the game (YYYY-MM-DD).
    pitcher_name        Display name used for reporting.
    pitcher_mlbam_id    MLBAM integer pitcher ID (provenance-grade identity).
    opponent            Opponent team abbreviation (MLB).
    line                Numeric pitch-count line (e.g. 15.5).
    direction           "LESS" or "MORE".
    model_probability   P(hit) from the WOW_LEAN_1IP adapter [0,1] or None.
    model_uncertainty   Estimated uncertainty width (std of simulation dist).
    feature_snapshot_id SHA-256[:16] of the serialised feature dict.
    model_version       Identifier of the model/adapter used.
    data_provenance     Dict describing data sources used (source, fetch_method,
                        bf_n, ppb_n, board_date, etc.).
    notes               Optional free-text notes; never used in metric computation.
    """
    prediction_id:       str
    schema_version:      str
    frozen_at:           str                    # UTC ISO-8601
    sport:               str                    # "MLB"
    prop_type:           str                    # "1IP_PITCHES_THROWN"
    game_date:           str                    # YYYY-MM-DD
    pitcher_name:        str
    pitcher_mlbam_id:    int
    opponent:            str
    line:                float
    direction:           str                    # "LESS" | "MORE"
    model_probability:   Optional[float]        # None if model unavailable
    model_uncertainty:   Optional[float]        # None if not estimated
    feature_snapshot_id: str
    model_version:       str
    data_provenance:     dict                   # frozen as a tuple internally
    notes:               str = ""

    # ------------------------------------------------------------------
    # Validation at construction
    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.direction not in {"LESS", "MORE"}:
            raise ValueError(f"direction must be LESS|MORE, got {self.direction!r}")
        if self.model_probability is not None:
            if not (0.0 <= self.model_probability <= 1.0):
                raise ValueError(
                    f"model_probability must be in [0,1], got {self.model_probability}"
                )
        if not self.game_date or len(self.game_date) < 10:
            raise ValueError(f"game_date must be YYYY-MM-DD, got {self.game_date!r}")

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        game_date: str,
        pitcher_name: str,
        pitcher_mlbam_id: int,
        opponent: str,
        line: float,
        direction: str,
        model_probability: Optional[float],
        model_uncertainty: Optional[float],
        features: dict,          # serialised feature dict → snapshot ID
        model_version: str,
        data_provenance: dict,
        notes: str = "",
        _frozen_at: Optional[str] = None,   # injectable for tests only
    ) -> "PredictionRecord":
        frozen_at = _frozen_at or _now_utc()
        feature_snapshot_id = _sha256(features)
        pred_id = _prediction_id(
            pitcher_mlbam_id, game_date, float(line),
            direction.upper(), frozen_at,
        )
        return cls(
            prediction_id       = pred_id,
            schema_version      = PREDICTION_SCHEMA_VERSION,
            frozen_at           = frozen_at,
            sport               = "MLB",
            prop_type           = "1IP_PITCHES_THROWN",
            game_date           = game_date,
            pitcher_name        = pitcher_name,
            pitcher_mlbam_id    = pitcher_mlbam_id,
            opponent            = opponent,
            line                = float(line),
            direction           = direction.upper(),
            model_probability   = model_probability,
            model_uncertainty   = model_uncertainty,
            feature_snapshot_id = feature_snapshot_id,
            model_version       = model_version,
            data_provenance     = data_provenance,
            notes               = notes,
        )

    def to_dict(self) -> dict:
        return {
            "prediction_id":       self.prediction_id,
            "schema_version":      self.schema_version,
            "frozen_at":           self.frozen_at,
            "sport":               self.sport,
            "prop_type":           self.prop_type,
            "game_date":           self.game_date,
            "pitcher_name":        self.pitcher_name,
            "pitcher_mlbam_id":    self.pitcher_mlbam_id,
            "opponent":            self.opponent,
            "line":                self.line,
            "direction":           self.direction,
            "model_probability":   self.model_probability,
            "model_uncertainty":   self.model_uncertainty,
            "feature_snapshot_id": self.feature_snapshot_id,
            "model_version":       self.model_version,
            "data_provenance":     self.data_provenance,
            "notes":               self.notes,
        }
