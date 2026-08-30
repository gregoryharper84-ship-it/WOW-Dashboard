"""Calibration/publication capability lane separation for WOW v16 Clean Core.

Implements WOW-PATCH-2026-08-30-CALIBRATION-PUBLICATION-LANE-SEPARATION.
This module classifies governed publication/calibration state without
conflating it with controlling-specialist availability.

A sport/market runtime lane may be AVAILABLE while the global governed
publication latch is UNAVAILABLE. Therefore the classifier consumes both the
lane status and, when supplied, the global publication/calibration evidence.
Only explicitly calibration/publication-scoped failures are eligible for the
research-only continuation path. Unknown, global, confidence/model, provider,
artifact, or specialist failures remain fail-closed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

CALIBRATION_SCOPE = "CALIBRATION"
PUBLICATION_SCOPE = "PUBLICATION"
_ALLOWED_RESEARCH_SCOPES = {CALIBRATION_SCOPE, PUBLICATION_SCOPE}

_PUBLICATION_ONLY_CODES = {
    "FORWARD_SHADOW_NOT_COMPLETED",
    "CALIBRATION_HEALTH_BLOCKED",
    "CALIBRATION_HEALTH_NOT_PASS",
    "CALIBRATION_BLOCKED",
    "PROBABILITY_PUBLICATION_HELD",
    "GOVERNED_PROBABILITY_NOT_PUBLISHABLE",
    "PUBLICATION_NOT_RATIFIED",
    "PRODUCTION_FEATURE_READY_FALSE",
}

_MODEL_INVALIDATING_TOKENS = (
    "MODEL_UNAVAILABLE",
    "SPECIALIST_ROUTING_UNAVAILABLE",
    "NO_SPECIALIST",
    "MODEL_ARTIFACT",
    "CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
    "MODEL_REGISTRY_UNAVAILABLE",
    "MODEL_FAMILY_ADAPTER_UNAVAILABLE",
    "PROVIDER_UNAVAILABLE",
    "DATA_PROVIDER_OUTAGE",
    "CONFIDENCE",
)


@dataclass(frozen=True)
class CapabilitySeparation:
    source_capability_status: str
    global_governed_probability_capability: str
    routing_capability_status: str
    specialist_model_capability: str
    calibration_capability: str
    governed_publication_capability: str
    governed_publishable: bool
    failed_contract_scope: tuple[str, ...]
    blocker_codes: tuple[str, ...]
    publication_only_lock: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["failed_contract_scope"] = list(self.failed_contract_scope)
        value["blocker_codes"] = list(self.blocker_codes)
        return value


def _code_strings(value: Any, *, parent_key: str = "") -> Iterable[str]:
    """Yield blocker-shaped strings only; ordinary evidence text is ignored."""
    key = parent_key.casefold()
    interesting = any(token in key for token in ("block", "reason", "code", "failure", "scope"))
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _code_strings(child, parent_key=str(child_key))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _code_strings(child, parent_key=parent_key)
    elif isinstance(value, str) and interesting:
        text = value.strip().upper()
        if text:
            yield text


def _normalize_explicit_scopes(evidence: Any) -> set[str]:
    scopes: set[str] = set()
    if not isinstance(evidence, dict):
        return scopes
    value = evidence.get("failed_contract_scope")
    values = value if isinstance(value, (list, tuple, set)) else [value] if value is not None else []
    for item in values:
        if not isinstance(item, str):
            continue
        for piece in item.replace("|", ",").split(","):
            normalized = piece.strip().upper()
            if normalized:
                scopes.add(normalized)
    return scopes


def classify_probability_capability(lane: dict[str, Any] | None) -> CapabilitySeparation:
    """Classify lane capability plus optional global publication state.

    Expected optional global fields on ``lane``:
      governed_probability_capability
      governed_publishable
      calibration_health_status

    Their blocker evidence belongs under ``evidence`` (for example
    ``global_calibration_blockers``). A raw lane AVAILABLE result therefore
    does not automatically mean governed publication is available.
    """
    lane = dict(lane or {})
    source_status = str(
        lane.get("source_capability_status")
        or lane.get("capability_status")
        or "UNAVAILABLE"
    ).upper()
    global_capability = str(
        lane.get("global_governed_probability_capability")
        or lane.get("governed_probability_capability")
        or ("AVAILABLE" if source_status == "AVAILABLE" else "UNAVAILABLE")
    ).upper()
    governed_publishable_flag = lane.get("governed_publishable")
    if governed_publishable_flag is None:
        governed_publishable_flag = lane.get("probability_publishable")

    evidence = lane.get("evidence") or {}
    codes = {code for code in _code_strings(evidence)}
    canonical_codes = {
        canonical
        for canonical in _PUBLICATION_ONLY_CODES
        if any(canonical in code for code in codes)
    }
    codes.update(canonical_codes)
    explicit_scopes = _normalize_explicit_scopes(evidence)

    model_invalidating = any(
        token in code
        for code in codes
        for token in _MODEL_INVALIDATING_TOKENS
    )
    explicit_global = "GLOBAL" in explicit_scopes
    explicit_publication_only = bool(explicit_scopes) and explicit_scopes.issubset(_ALLOWED_RESEARCH_SCOPES)
    forward_shadow_lock = "FORWARD_SHADOW_NOT_COMPLETED" in canonical_codes
    publication_latch_blocked = (
        global_capability != "AVAILABLE"
        or governed_publishable_flag is False
        or str(lane.get("calibration_health_status") or "").upper() in {"BLOCKED", "UNKNOWN", "UNAVAILABLE"}
    )
    publication_only = (
        publication_latch_blocked
        and not model_invalidating
        and not explicit_global
        and (explicit_publication_only or forward_shadow_lock)
    )

    if publication_only:
        scopes = explicit_scopes or _ALLOWED_RESEARCH_SCOPES
        return CapabilitySeparation(
            source_capability_status=source_status,
            global_governed_probability_capability=global_capability,
            routing_capability_status="AVAILABLE_FOR_RESEARCH",
            specialist_model_capability="ROUTE_DEPENDENT",
            calibration_capability="BLOCKED_OR_UNKNOWN",
            governed_publication_capability="UNAVAILABLE",
            governed_publishable=False,
            failed_contract_scope=tuple(sorted(scopes)),
            blocker_codes=tuple(sorted(codes)),
            publication_only_lock=True,
        )

    if source_status == "AVAILABLE" and not publication_latch_blocked:
        return CapabilitySeparation(
            source_capability_status=source_status,
            global_governed_probability_capability=global_capability,
            routing_capability_status="AVAILABLE",
            specialist_model_capability="ROUTE_DEPENDENT",
            calibration_capability="AVAILABLE",
            governed_publication_capability="AVAILABLE",
            governed_publishable=True,
            failed_contract_scope=(),
            blocker_codes=tuple(sorted(codes)),
            publication_only_lock=False,
        )

    # A lane-level AVAILABLE state with an unknown global publication failure
    # still preserves route identity, but cannot use the special research-only
    # exception unless the failure scope is proven calibration/publication.
    routing_status = "AVAILABLE_ROUTE_PUBLICATION_SCOPE_UNRESOLVED" if source_status == "AVAILABLE" else "UNAVAILABLE"
    return CapabilitySeparation(
        source_capability_status=source_status,
        global_governed_probability_capability=global_capability,
        routing_capability_status=routing_status,
        specialist_model_capability="ROUTE_DEPENDENT",
        calibration_capability="UNKNOWN_OR_UNAVAILABLE",
        governed_publication_capability="UNAVAILABLE",
        governed_publishable=False,
        failed_contract_scope=tuple(sorted(explicit_scopes or {"GLOBAL"})),
        blocker_codes=tuple(sorted(codes)),
        publication_only_lock=False,
    )


def publication_scope_only(scopes: Iterable[str]) -> bool:
    normalized = {str(scope).strip().upper() for scope in scopes if str(scope).strip()}
    return bool(normalized) and normalized.issubset(_ALLOWED_RESEARCH_SCOPES)
