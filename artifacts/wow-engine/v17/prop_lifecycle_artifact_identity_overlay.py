"""Preserve exact feature-schema identity in V17 prop lifecycle health rows.

The base lifecycle dashboard historically projected certification artifact rows
without ``feature_schema_version``. The runtime enrichment layer correctly uses
that field as part of the exact artifact identity, so the omission prevented
forward-cohort and calibrator-candidate evidence from rejoining the same
artifact. This overlay repairs only that control-plane projection; it never
changes sporting probability, calibration, certification, promotion, ranking,
or execution authority.
"""
from __future__ import annotations

from typing import Any, Mapping

import v17.prop_lifecycle_autopilot as base

CAN_EXECUTE = False
_INSTALLED = False
_ORIGINAL_BUILD_LIFECYCLE_DASHBOARD = base.build_lifecycle_dashboard


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        str(row.get("sport") or "").strip().upper(),
        str(row.get("stat_type") or "").strip().upper(),
        str(row.get("model_family") or ""),
        str(row.get("model_artifact_version") or ""),
        str(row.get("artifact_checksum") or ""),
        str(row.get("calibrator_version") or ""),
    )


def preserve_artifact_feature_schema_identity(
    dashboard: dict[str, Any],
    certification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach feature schema only when the exact artifact identity is unique."""
    schema_by_identity: dict[tuple[str, str, str, str, str, str], set[str]] = {}
    for raw in (certification or {}).get("artifact_rows") or []:
        if not isinstance(raw, Mapping):
            continue
        schema = str(raw.get("feature_schema_version") or "").strip()
        if not schema:
            continue
        schema_by_identity.setdefault(_identity(raw), set()).add(schema)

    for row in dashboard.get("artifact_cohort_rows") or []:
        if not isinstance(row, dict):
            continue
        schemas = schema_by_identity.get(_identity(row), set())
        if len(schemas) == 1:
            row["feature_schema_version"] = next(iter(schemas))
        elif len(schemas) > 1:
            # Do not guess across an ambiguous identity. Downstream exact-artifact
            # joins will remain fail-closed until the ambiguity is resolved.
            row["feature_schema_version"] = None
    return dashboard


def _build_lifecycle_dashboard_with_feature_identity(*args: Any, **kwargs: Any) -> dict[str, Any]:
    dashboard = _ORIGINAL_BUILD_LIFECYCLE_DASHBOARD(*args, **kwargs)
    certification = kwargs.get("certification")
    if certification is None and len(args) >= 3:
        certification = args[2]
    return preserve_artifact_feature_schema_identity(dashboard, certification)


def install() -> None:
    """Install the projection repair once for all lifecycle callers."""
    global _INSTALLED
    if _INSTALLED:
        return
    base.build_lifecycle_dashboard = _build_lifecycle_dashboard_with_feature_identity
    _INSTALLED = True


__all__ = [
    "CAN_EXECUTE",
    "install",
    "preserve_artifact_feature_schema_identity",
]
