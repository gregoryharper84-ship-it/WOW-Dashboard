"""
validation/splitting/chronological_split.py

Chronological TRAIN / VALIDATION / TRUE_HOLDOUT splitting.

Rules (from eval_rules.yaml):
- Sort ascending by game_date.
- Split by fractional index (default 60/20/20).
- Holdout is immutable and must never be used for threshold tuning.
- A split_manifest records the date boundaries and record counts for audit.

The splitter accepts a list of (PredictionRecord, OutcomeRecord) pairs —
both must be present; unpaired records are excluded and reported.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from validation.schema.prediction_record import PredictionRecord
from validation.schema.outcome_record     import OutcomeRecord

# Type alias for a joined pair
Pair = Tuple[PredictionRecord, OutcomeRecord]


@dataclass
class SplitManifest:
    """Audit record describing how the split was performed."""
    total_records:      int
    train_count:        int
    validation_count:   int
    holdout_count:      int
    excluded_count:     int   # unpaired or invalid
    train_date_range:   Tuple[str, str]   # (earliest, latest)
    validation_date_range: Tuple[str, str]
    holdout_date_range: Tuple[str, str]
    split_fractions:    dict  # {"train": f, "validation": f, "holdout": f}
    holdout_immutable:  bool  # always True; attestation field


@dataclass
class DataSplit:
    train:      List[Pair]
    validation: List[Pair]
    holdout:    List[Pair]
    manifest:   SplitManifest

    def _date_range(self, pairs: List[Pair]) -> Tuple[str, str]:
        if not pairs:
            return ("", "")
        dates = sorted(p.game_date for p, _ in pairs)
        return (dates[0], dates[-1])


def chronological_split(
    pairs: List[Pair],
    *,
    train_fraction:      float = 0.60,
    validation_fraction: float = 0.20,
    holdout_fraction:    float = 0.20,
    excluded: Optional[List[Pair]] = None,
) -> DataSplit:
    """
    Split *pairs* chronologically by game_date.

    Parameters
    ----------
    pairs               List of (PredictionRecord, OutcomeRecord).  Records
                        without a matching OutcomeRecord must be filtered out
                        by the caller before passing here.
    train_fraction      Fraction of records for training (default 0.60).
    validation_fraction Fraction for validation/dev set (default 0.20).
    holdout_fraction    Fraction for true holdout (default 0.20).

    Raises
    ------
    ValueError  if fractions do not sum to 1.0 (±0.001 tolerance).
    """
    total_frac = train_fraction + validation_fraction + holdout_fraction
    if abs(total_frac - 1.0) > 0.001:
        raise ValueError(
            f"Split fractions must sum to 1.0, got {total_frac:.4f}"
        )

    # Sort chronologically
    sorted_pairs = sorted(pairs, key=lambda p: (p[0].game_date, p[0].frozen_at))
    n = len(sorted_pairs)

    train_end = math.floor(n * train_fraction)
    val_end   = train_end + math.floor(n * validation_fraction)

    train      = sorted_pairs[:train_end]
    validation = sorted_pairs[train_end:val_end]
    holdout    = sorted_pairs[val_end:]

    excluded_count = len(excluded) if excluded else 0

    def _dr(lst: List[Pair]) -> Tuple[str, str]:
        if not lst:
            return ("", "")
        dates = sorted(p.game_date for p, _ in lst)
        return (dates[0], dates[-1])

    manifest = SplitManifest(
        total_records       = n,
        train_count         = len(train),
        validation_count    = len(validation),
        holdout_count       = len(holdout),
        excluded_count      = excluded_count,
        train_date_range    = _dr(train),
        validation_date_range = _dr(validation),
        holdout_date_range  = _dr(holdout),
        split_fractions     = {
            "train":      train_fraction,
            "validation": validation_fraction,
            "holdout":    holdout_fraction,
        },
        holdout_immutable   = True,
    )

    return DataSplit(
        train      = train,
        validation = validation,
        holdout    = holdout,
        manifest   = manifest,
    )
