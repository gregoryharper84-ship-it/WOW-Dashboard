"""Pandera contracts for external V17 evidence envelopes.

Passing this schema proves structural intake quality only. It never certifies a
source as model-authoritative and never makes a row probability-publishable.
"""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series


class ExternalEvidenceEnvelope(pa.DataFrameModel):
    source_id: Series[str]
    source_kind: Series[str]
    captured_at_utc: Series[pd.Timestamp]
    source_published_at_utc: Series[pd.Timestamp] = pa.Field(nullable=True)
    schema_fingerprint: Series[str]
    payload_sha256: Series[str] = pa.Field(str_matches=r"^[0-9a-f]{64}$")
    completeness_score: Series[float] = pa.Field(ge=0.0, le=1.0)
    can_execute: Series[bool] = pa.Field(eq=False)

    class Config:
        strict = True
        coerce = True


def validate_external_evidence(rows: list[dict]) -> pd.DataFrame:
    """Return validated evidence or raise a Pandera schema error."""
    for row in rows:
        for column in ("captured_at_utc", "source_published_at_utc"):
            value = row.get(column)
            if value is None:
                continue
            parsed = pd.Timestamp(value)
            if parsed.utcoffset() is None:
                raise ValueError(f"TIMEZONE_REQUIRED:{column}")
    frame = pd.DataFrame(rows)
    return ExternalEvidenceEnvelope.validate(frame)
