"""Read-only operator summary of the existing V17 prop lifecycle audit.

Not a scorer, source-rights registry, certification gate or probability authority.
All readiness decisions remain with the existing production registration audit.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SCHEMA = "WOW_PROP_OPERATOR_SUMMARY_V1"
REQUIRED_STAGES = (
    "DURABLE_FORWARD_EVIDENCE_AUDIT",
    "CALIBRATION_CERTIFICATION_READINESS",
    "PRODUCTION_REGISTRATION_AUDIT",
    "CALIBRATOR_CANDIDATE_EVIDENCE",
)
EXISTING_AUTHORITY_FLAGS = (
    "production_numerical_authority",
    "action_canary_verified",
    "model_adapter_registered",
    "calibrator_adapter_registered",
    "hydration_route_registered",
)


def _token(row: Mapping[str, Any]) -> str:
    return f"{str(row.get('sport') or '').strip().upper()}:{str(row.get('stat_type') or '').strip().upper()}"


def summarize_prop_routes(
    dashboard: Mapping[str, Any] | None,
    *,
    requested_routes: list[str],
    stage_statuses: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Show only observed registration and first HOLD; never authorize use.

    Missing audit stages or route rows are explicit HOLDs, not an empty slate.
    Output is a pure projection over already-authoritative V17 audit facts.
    """
    payload = dashboard if isinstance(dashboard, Mapping) else {}
    stages = stage_statuses if isinstance(stage_statuses, Mapping) else {}
    stage_blocker = next(
        (
            f"AUDIT_STAGE_{'NOT_VERIFIED' if name not in stages else 'FAILED'}:{name}"
            for name in REQUIRED_STAGES if stages.get(name) != "PASS"
        ),
        None,
    )

    indexed: dict[str, list[Mapping[str, Any]]] = {}
    raw_rows = payload.get("route_rows")
    for row in raw_rows if isinstance(raw_rows, list) else []:
        if isinstance(row, Mapping) and row.get("row_kind") == "ROUTE_STATUS":
            indexed.setdefault(_token(row), []).append(row)

    output = []
    seen = set()
    for raw_token in requested_routes:
        token = str(raw_token or "").strip().upper()
        if not token or token in seen:
            continue
        seen.add(token)
        matches = indexed.get(token, [])
        original = matches[0] if len(matches) == 1 else {}
        existing = original.get("blockers")
        blockers = [str(x) for x in existing if isinstance(x, str) and x.strip()] if isinstance(existing, list) else []
        lifecycle = str(original.get("lifecycle_status") or "NOT_OBSERVED")

        if payload.get("can_execute") is True or original.get("can_execute") is True:
            blocker = "EXECUTION_AUTHORITY_VIOLATION"
        elif payload.get("can_execute") is not False:
            blocker = "EXECUTION_AUTHORITY_UNVERIFIED"
        elif matches and len(matches) == 1 and original.get("can_execute") is not False:
            blocker = "EXECUTION_AUTHORITY_UNVERIFIED"
        elif stage_blocker:
            blocker = stage_blocker
        elif len(matches) > 1:
            blocker = "ROUTE_STATUS_AMBIGUOUS"
        elif not matches:
            blocker = "ROUTE_STATUS_NOT_OBSERVED"
        elif not isinstance(existing, list):
            blocker = "ROUTE_BLOCKERS_UNVERIFIED"
        elif (lifecycle == "PRODUCTION_REGISTERED" and (
            blockers or any(original.get(field) is not True for field in EXISTING_AUTHORITY_FLAGS)
        )):
            blocker = "INCONSISTENT_REGISTRATION_EVIDENCE"
        elif lifecycle != "PRODUCTION_REGISTERED":
            blocker = blockers[0] if blockers else f"LIFECYCLE_NOT_REGISTERED:{lifecycle}"
        else:
            blocker = None

        output.append({
            "route": token,
            "state": "HOLD" if blocker else "REGISTERED_OBSERVED",
            "lifecycle_status": lifecycle,
            "first_blocker": blocker,
            "blockers": list(dict.fromkeys(([blocker] if blocker and blocker not in blockers else []) + blockers)),
            "can_execute": False,
        })

    return {
        "schema": SCHEMA,
        "routes": output,
        "route_n": len(output),
        "held_n": sum(x["state"] == "HOLD" for x in output),
        "registered_observed_n": sum(x["state"] == "REGISTERED_OBSERVED" for x in output),
        "source_rights_status": "NOT_EVALUATED_BY_OPERATOR_VIEW",
        "operator_diagnostic_only": True,
        "probability_publishable": False,
        "authorizes_model_promotion": False,
        "can_execute": False,
    }
