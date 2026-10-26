"""Read-only V17 all-sport recovery proof preflight.

Joins the existing team/event readiness and exact-stat prop lifecycle inventories
to *supplied* Action/ledger evidence.  Never probes production, authenticates a
receipt, fits a model, certifies a route, or grants publication.  A complete
packet is only READY_FOR_INDEPENDENT_REVIEW, not FIXED_AND_VERIFIED.

Missing receipts and untested routes must never become MODEL_UNAVAILABLE.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
VERSION = "V17_ALL_SPORT_RECOVERY_PROOF_PREFLIGHT_V1"
TEAM = "TEAM_EVENT_WINNER"
PROP = "PLAYER_PROP"

# These are receipt fields, NOT proof of provenance. Independent verification
# must re-read each reference from its owning authenticated system.
_PROOF_FIELDS = (
    "official_event_id", "exact_selection", "model_artifact_id",
    "model_timestamp", "calibrated_probability", "calibrated_lower_bound",
    "action_receipt_id", "prediction_id", "source_sha", "deployed_sha",
    "source_snapshot_id", "terminal_disposition",
)
_PROP_PROOF_FIELDS = ("period", "exact_line", "direction", "stat_type")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sport(value: Any) -> str:
    value = _text(value).upper()
    return "PGA" if value == "GOLF" else value


def _route(sport: str, lane: str, stat: str = "") -> tuple[str, str, str]:
    return (_sport(sport), lane, _text(stat).upper())


def _readiness(row: Mapping[str, Any], lane: str) -> bool:
    if lane == TEAM:
        # No inferred readiness from a declared bridge, import, or static cert.
        return row.get("model_capability_ready") is True
    return row.get("production_numerical_authority") is True


def _source_blocker(row: Mapping[str, Any], lane: str) -> str:
    possible = row.get("readiness_blockers") if lane == TEAM else row.get("blockers")
    if not possible:
        possible = row.get("known_gaps") if lane == TEAM else None
    if isinstance(possible, (list, tuple)) and possible:
        return _text(possible[0]) or "CAPABILITY_PROOF_MISSING"
    if lane == TEAM:
        return "MODEL_CAPABILITY_NOT_PROVEN"
    return _text(row.get("status")) or "PROP_LIFECYCLE_NOT_PROVEN"


def _packet_defect(
    receipt: Mapping[str, Any], lane: str, stat: str,
) -> str | None:
    if receipt.get("can_execute") is True:
        return "EXECUTION_AUTHORITY_VIOLATION"
    if _text(receipt.get("terminal_authority")) != TERMINAL_AUTHORITY:
        return "TERMINAL_AUTHORITY_UNVERIFIED"
    if lane == PROP and _text(receipt.get("stat_type")).upper() != stat:
        return "PROP_STAT_IDENTITY_MISMATCH"
    for field in _PROOF_FIELDS + (_PROP_PROOF_FIELDS if lane == PROP else ()):
        if receipt.get(field) is None or (
            isinstance(receipt.get(field), str) and not receipt[field].strip()
        ):
            return "MISSING_PROOF_FIELD:" + field
    if lane == PROP and _text(receipt.get("direction")).upper() not in {"MORE", "LESS"}:
        return "PROP_DIRECTION_UNSUPPORTED"
    try:
        p = float(receipt["calibrated_probability"])
        lower = float(receipt["calibrated_lower_bound"])
    except (TypeError, ValueError, OverflowError):
        return "INVALID_CALIBRATION_NUMERIC"
    from math import isfinite
    if not (isfinite(p) and isfinite(lower) and 0 <= lower <= p <= 1):
        return "INVALID_CALIBRATION_BOUNDS"
    if _text(receipt["source_sha"]) != _text(receipt["deployed_sha"]):
        return "PRODUCTION_SHA_MISMATCH"
    return None


def build_all_sport_recovery_proof_matrix(
    *,
    team_rows: Iterable[Mapping[str, Any]],
    prop_rows: Iterable[Mapping[str, Any]],
    receipt_rows: Iterable[Mapping[str, Any]] = (),
    sports: Iterable[str] = EXPECTED_TEAM_EVENT_SPORTS,
) -> dict[str, Any]:
    """Build an exact-route handoff matrix; never assert external verification.

    team_rows must include the operational readiness overlay to prove model
    readiness. The repository-only moneyline gap matrix alone is insufficient.
    Prop readiness comes from the existing exact-route lifecycle assessment.
    An Action receipt is route-scoped here for *preflight* only: a second receipt
    for that route is ambiguous rather than silently treated as a replacement.
    """
    baseline_sports = tuple(dict.fromkeys(_sport(s) for s in sports))
    if not baseline_sports or any(not s for s in baseline_sports):
        raise ValueError("nonempty sport inventory required")

    routes: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in team_rows:
        if row.get("can_execute") is True:
            raise ValueError("execution authority forbidden in inventory")
        key = _route(row.get("sport"), TEAM)
        if not key[0] or key in routes:
            raise ValueError("duplicate or empty team-event route")
        routes[key] = row
    for row in prop_rows:
        if row.get("can_execute") is True:
            raise ValueError("execution authority forbidden in inventory")
        key = _route(row.get("sport"), PROP, row.get("stat_type"))
        if not key[0] or not key[2] or key in routes:
            raise ValueError("duplicate or empty exact-stat prop route")
        routes[key] = row

    proofs: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for receipt in receipt_rows:
        if receipt.get("can_execute") is True:
            raise ValueError("execution authority forbidden in receipt")
        key = _route(receipt.get("sport"), _text(receipt.get("lane")).upper(),
                     receipt.get("stat_type"))
        if key[1] not in {TEAM, PROP} or not key[0]:
            raise ValueError("invalid receipt route identity")
        proofs.setdefault(key, []).append(receipt)

    # Catalogued sports cannot disappear; additionally admit newly observed
    # specialist sports without changing this source file.
    extra_sports = sorted({key[0] for key in (*routes, *proofs)} - set(baseline_sports))
    inventory = (*baseline_sports, *extra_sports)
    result: list[dict[str, Any]] = []
    for sport in inventory:
        items = [(key, row) for key, row in routes.items() if key[0] == sport]
        if not any(key[1] == TEAM for key, _ in items):
            items.append((_route(sport, TEAM), {}))
        if not any(key[1] == PROP for key, _ in items):
            items.append((_route(sport, PROP, "NO_STAT_DECLARED"), {}))
        for key, row in sorted(items, key=lambda item: item[0]):
            _, lane, stat = key
            supplied = proofs.get(key, [])
            if not row:
                status = "INVENTORY_NOT_ESTABLISHED"
                blocker = "NO_PROP_ROUTE_OBSERVED" if lane == PROP else "TEAM_ROUTE_NOT_OBSERVED"
                stage = "INVENTORY"
            elif not _readiness(row, lane):
                status = "CAPABILITY_NOT_PROVEN"
                blocker = _source_blocker(row, lane)
                stage = "FITTED_MODEL_OR_CERTIFICATION"
            elif not supplied:
                status = "ACTION_NOT_TESTED"
                blocker = "AUTHENTICATED_ACTION_RECEIPT_MISSING"
                stage = "ACTION"
            elif len(supplied) != 1:
                status = "PROOF_PACKET_AMBIGUOUS"
                blocker = "MULTIPLE_RECEIPTS_REQUIRE_EXACT_EVENT_SELECTION"
                stage = "ACTION"
            else:
                blocker = _packet_defect(supplied[0], lane, stat)
                status = ("PROOF_PACKET_INCOMPLETE" if blocker else
                          "READY_FOR_INDEPENDENT_REVIEW")
                stage = "PROOF_VALIDATION" if blocker else "INDEPENDENT_PRODUCTION_VERIFICATION"
            result.append({
                "sport": sport,
                "lane": lane,
                "stat_type": stat or None,
                "readiness_status": status,
                "first_unproven_stage": stage,
                "blocker": blocker,
                "receipt_count": len(supplied),
                "source_capability_status": (
                    row.get("operational_readiness_status") if lane == TEAM else row.get("status")
                ) if row else None,
                "production_verified": False,
                "model_probability_produced_by_this_tool": False,
                "terminal_authority": TERMINAL_AUTHORITY,
                "can_execute": False,
            })
    unexpected = sorted(set(proofs).difference({key for key in routes}))
    counts = dict(Counter(row["readiness_status"] for row in result))
    return {
        "version": VERSION,
        "sports_in_inventory": list(inventory),
        "sports_reconciled": len(inventory),
        "rows": result,
        "status_counts": counts,
        "receipt_routes_not_in_inventory": [
            {"sport": key[0], "lane": key[1], "stat_type": key[2] or None}
            for key in unexpected
        ],
        "independent_production_verification_required": True,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = ["build_all_sport_recovery_proof_matrix", "VERSION", "CAN_EXECUTE"]
