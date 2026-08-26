"""
gate_engine/universal_agent/model_validation/model_manifest.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Immutable Model-Run Provenance Manifest.

Every model run produces a ManifestEntry capturing full provenance:
  model_id, version, param_hash, feature_snapshot_ids used,
  run_id (unique), created_at timestamp.

Once written, a ManifestEntry cannot be modified. Duplicate run_ids
are rejected (fail-closed).

can_execute = False
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


@dataclass(frozen=True)
class ManifestEntry:
    """
    Immutable provenance record for one model run.

    run_id                  Unique identifier for this execution.
    model_id                Logical model name (e.g. "wnba_rebounds_poisson_v1").
    model_version           Semver string.
    param_hash              SHA-256 of serialised model parameters.
    feature_snapshot_ids    Snapshot IDs used as inputs (point-in-time locked).
    stat_key                Stat this model targets.
    sport                   Sport this model targets.
    created_at              ISO-8601 timestamp of run creation.
    metadata                Arbitrary key-value annotations.
    """
    run_id:               str
    model_id:             str
    model_version:        str
    param_hash:           str
    feature_snapshot_ids: tuple          # immutable sequence of snapshot IDs
    stat_key:             str
    sport:                str
    created_at:           str
    metadata:             dict           # annotations; treated as immutable by contract


class ModelManifest:
    """
    Advisory-only in-memory manifest store.
    can_execute = False — no production mutations.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ManifestEntry] = {}

    def record(
        self,
        *,
        run_id:               str,
        model_id:             str,
        model_version:        str,
        params:               dict[str, Any],
        feature_snapshot_ids: list[str],
        stat_key:             str,
        sport:                str,
        created_at:           str | None = None,
        metadata:             dict[str, Any] | None = None,
    ) -> ManifestEntry:
        """
        Record an immutable provenance entry.
        Raises ValueError on duplicate run_id.
        """
        if run_id in self._entries:
            raise ValueError(
                f"ModelManifest: duplicate run_id {run_id!r}. "
                "Manifest entries are immutable once recorded."
            )
        if not run_id or not model_id:
            raise ValueError("run_id and model_id must be non-empty strings")

        param_hash = _hash_params(params)
        ts = created_at or datetime.now(timezone.utc).isoformat()

        entry = ManifestEntry(
            run_id=run_id,
            model_id=model_id,
            model_version=model_version,
            param_hash=param_hash,
            feature_snapshot_ids=tuple(feature_snapshot_ids),
            stat_key=stat_key,
            sport=sport,
            created_at=ts,
            metadata=dict(metadata or {}),
        )
        self._entries[run_id] = entry
        return entry

    def get(self, run_id: str) -> ManifestEntry | None:
        return self._entries.get(run_id)

    def list_by_model(self, model_id: str) -> list[ManifestEntry]:
        return [e for e in self._entries.values() if e.model_id == model_id]

    def list_by_sport_stat(self, sport: str, stat_key: str) -> list[ManifestEntry]:
        return [
            e for e in self._entries.values()
            if e.sport == sport and e.stat_key == stat_key
        ]

    def count(self) -> int:
        return len(self._entries)


def _hash_params(params: dict[str, Any]) -> str:
    """Deterministic SHA-256 of JSON-serialised parameter dict."""
    try:
        serialised = json.dumps(params, sort_keys=True, default=str).encode()
        return hashlib.sha256(serialised).hexdigest()[:16]
    except (TypeError, ValueError):
        return "UNHASHABLE"
