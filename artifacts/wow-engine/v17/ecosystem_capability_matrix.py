"""Cross-product capability/readiness matrix for the WOW Ecosystem Conductor.

Class A truth model only. Readiness never creates probability authority and never
turns runtime health into model or product readiness.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

READY_STATES = {"READY", "PASS", "HEALTHY", "COMPLETE"}
NONREADY_STATES = {"DEGRADED", "BLOCKED", "FAIL", "UNKNOWN", "UNSUPPORTED", "NOT_READY"}
ALLOWED_DIMENSION_STATES = READY_STATES | NONREADY_STATES | {"NOT_APPLICABLE"}

PRODUCT_REQUIRED_DIMENSIONS = {
    "WOW_PROP": (
        "discovery",
        "identity",
        "data",
        "model",
        "calibration",
        "routing",
        "terminal",
        "persistence",
        "verification",
        "user_workflow",
    ),
    "LLP_TEAM_EVENT": (
        "discovery",
        "identity",
        "data",
        "model",
        "calibration",
        "routing",
        "terminal",
        "persistence",
        "verification",
        "user_workflow",
    ),
    "KALSHI_WEATHER": (
        "contract",
        "settlement",
        "data",
        "model",
        "calibration",
        "routing",
        "terminal",
        "persistence",
        "verification",
        "user_workflow",
    ),
}


@dataclass(frozen=True)
class CapabilityInput:
    capability_id: str
    product: str
    domain: str
    route: str
    controlling_specialist: str
    dimensions: Mapping[str, str]
    blockers: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    intended: bool = True
    required_dimensions: tuple[str, ...] = ()
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY

    def validate(self) -> None:
        for name in (
            self.capability_id,
            self.product,
            self.domain,
            self.route,
            self.controlling_specialist,
        ):
            if not str(name).strip():
                raise ValueError("capability identity fields must be populated")
        if self.can_execute:
            raise ValueError("can_execute must remain false")
        if self.terminal_authority != TERMINAL_AUTHORITY:
            raise ValueError("terminal authority cannot be overridden")
        for key, value in self.dimensions.items():
            state = str(value or "UNKNOWN").upper()
            if state not in ALLOWED_DIMENSION_STATES:
                raise ValueError(f"invalid readiness state for {key}: {value}")


@dataclass(frozen=True)
class CapabilityReadiness:
    capability_id: str
    product: str
    domain: str
    route: str
    controlling_specialist: str
    intended: bool
    status: str
    product_ready: bool
    first_failing_dimension: str | None
    dimensions: Mapping[str, str]
    required_dimensions: tuple[str, ...]
    blockers: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY


def _required_dimensions(item: CapabilityInput) -> tuple[str, ...]:
    if item.required_dimensions:
        return item.required_dimensions
    return tuple(PRODUCT_REQUIRED_DIMENSIONS.get(item.product, tuple(item.dimensions)))


def _normalize_dimensions(
    item: CapabilityInput,
    required: Sequence[str],
) -> dict[str, str]:
    normalized = {str(k): str(v or "UNKNOWN").upper() for k, v in item.dimensions.items()}
    for dimension in required:
        normalized.setdefault(dimension, "UNKNOWN")
    return normalized


def evaluate_capability(item: CapabilityInput) -> CapabilityReadiness:
    item.validate()
    required = _required_dimensions(item)
    dimensions = _normalize_dimensions(item, required)

    failures: list[str] = []
    degraded: list[str] = []
    unknown: list[str] = []
    unsupported: list[str] = []

    for dimension in required:
        state = dimensions.get(dimension, "UNKNOWN")
        if state in READY_STATES:
            continue
        if state == "DEGRADED":
            degraded.append(dimension)
        elif state == "UNSUPPORTED":
            unsupported.append(dimension)
        elif state in {"FAIL", "BLOCKED", "NOT_READY"}:
            failures.append(dimension)
        else:
            unknown.append(dimension)

    first_failure = (
        failures[0]
        if failures
        else unsupported[0]
        if unsupported
        else unknown[0]
        if unknown
        else degraded[0]
        if degraded
        else None
    )

    blockers = list(item.blockers)
    for dimension in failures:
        code = f"DIMENSION_FAILED:{dimension}"
        if code not in blockers:
            blockers.append(code)
    for dimension in unsupported:
        code = f"DIMENSION_UNSUPPORTED:{dimension}"
        if code not in blockers:
            blockers.append(code)
    for dimension in unknown:
        code = f"DIMENSION_UNKNOWN:{dimension}"
        if code not in blockers:
            blockers.append(code)
    for dimension in degraded:
        code = f"DIMENSION_DEGRADED:{dimension}"
        if code not in blockers:
            blockers.append(code)

    if unsupported:
        status = "UNSUPPORTED"
    elif failures:
        status = "NOT_READY"
    elif unknown:
        status = "UNKNOWN"
    elif degraded:
        status = "DEGRADED"
    elif blockers:
        status = "NOT_READY"
    else:
        status = "READY"

    product_ready = status == "READY" and item.intended
    return CapabilityReadiness(
        capability_id=item.capability_id,
        product=item.product,
        domain=item.domain,
        route=item.route,
        controlling_specialist=item.controlling_specialist,
        intended=item.intended,
        status=status,
        product_ready=product_ready,
        first_failing_dimension=first_failure,
        dimensions=dimensions,
        required_dimensions=tuple(required),
        blockers=tuple(blockers),
        evidence_refs=tuple(dict.fromkeys(item.evidence_refs)),
    )


def capability_from_mapping(value: Mapping[str, Any]) -> CapabilityInput:
    return CapabilityInput(
        capability_id=str(value.get("capability_id") or ""),
        product=str(value.get("product") or ""),
        domain=str(value.get("domain") or value.get("sport_or_domain") or ""),
        route=str(value.get("route") or value.get("market_or_route") or ""),
        controlling_specialist=str(value.get("controlling_specialist") or ""),
        dimensions=dict(value.get("dimensions") or {}),
        blockers=tuple(map(str, value.get("blockers") or ())),
        evidence_refs=tuple(map(str, value.get("evidence_refs") or ())),
        intended=bool(value.get("intended", True)),
        required_dimensions=tuple(map(str, value.get("required_dimensions") or ())),
        can_execute=bool(value.get("can_execute", False)),
        terminal_authority=str(value.get("terminal_authority") or TERMINAL_AUTHORITY),
    )


def build_matrix(
    items: Iterable[CapabilityInput | Mapping[str, Any]],
) -> list[CapabilityReadiness]:
    output: list[CapabilityReadiness] = []
    seen: set[str] = set()
    for raw in items:
        item = raw if isinstance(raw, CapabilityInput) else capability_from_mapping(raw)
        if item.capability_id in seen:
            raise ValueError(f"duplicate capability_id: {item.capability_id}")
        seen.add(item.capability_id)
        output.append(evaluate_capability(item))
    return output


def matrix_summary(
    matrix: Sequence[CapabilityReadiness],
) -> dict[str, Any]:
    intended = [row for row in matrix if row.intended]
    ready = [row for row in intended if row.product_ready]
    by_status: dict[str, int] = {}
    for row in intended:
        by_status[row.status] = by_status.get(row.status, 0) + 1

    denominator = len(intended)
    availability = (len(ready) / denominator) if denominator else None
    return {
        "intended_capabilities": denominator,
        "ready_capabilities": len(ready),
        "reliable_decision_availability": availability,
        "metric_status": "PASS" if denominator else "UNKNOWN",
        "by_status": dict(sorted(by_status.items())),
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def matrix_by_product(
    matrix: Sequence[CapabilityReadiness],
) -> dict[str, dict[str, Any]]:
    products: dict[str, list[CapabilityReadiness]] = {}
    for row in matrix:
        products.setdefault(row.product, []).append(row)
    result: dict[str, dict[str, Any]] = {}
    for product, rows in sorted(products.items()):
        summary = matrix_summary(rows)
        summary["capabilities"] = [asdict(row) for row in rows]
        result[product] = summary
    return result


def serialize_matrix(
    matrix: Sequence[CapabilityReadiness],
) -> list[dict[str, Any]]:
    return [asdict(row) for row in matrix]


__all__ = [
    "ALLOWED_DIMENSION_STATES",
    "CAN_EXECUTE",
    "CapabilityInput",
    "CapabilityReadiness",
    "PRODUCT_REQUIRED_DIMENSIONS",
    "READY_STATES",
    "TERMINAL_AUTHORITY",
    "build_matrix",
    "capability_from_mapping",
    "evaluate_capability",
    "matrix_by_product",
    "matrix_summary",
    "serialize_matrix",
]
