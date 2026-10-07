"""Deterministic WOW ecosystem conductor and work-conservation control plane.

Class A only: this module coordinates metadata, readiness, handoffs, cross-system
ownership envelopes, and truthful status reporting. It never scores sporting events,
changes model math/calibration, overrides specialist ownership, publishes a
probability, or grants wagering/trading execution authority.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REGISTRY_VERSION = "1.0"
CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CONDUCTOR = "WOW_ECOSYSTEM_CONDUCTOR"
SAFE_HOLD_AUTHORITY = "SYSTEMS_INTELLIGENCE_RELIABILITY"
INDEPENDENT_VERIFIER = "INDEPENDENT_VERIFICATION"

ALLOWED_STATUS = {
    "PASS",
    "DEGRADED",
    "FAIL",
    "BLOCKED",
    "UNKNOWN",
    "NOT_APPLICABLE",
}

REQUIRED_COMPONENTS = {
    CONDUCTOR,
    "WOW_BETTING_INTELLIGENCE",
    "SCOUT",
    "WOW_BETTING_ENGINE",
    "LLP_TEAM_BETTING_ENGINE",
    "KALSHI_WEATHER_EXPERT",
    SAFE_HOLD_AUTHORITY,
    "ENGINEERING_CLOSURE",
    INDEPENDENT_VERIFIER,
    TERMINAL_AUTHORITY,
    "PERSISTENCE_PUBLICATION",
}

EXTERNAL_ENDPOINTS = {"USER", "RUNTIME_ECOSYSTEM"}

REQUIRED_RUNTIME_INVARIANTS = {
    "can_execute": False,
    "terminal_authority": TERMINAL_AUTHORITY,
    "specialist_ownership_preserved": True,
    "typed_failures_preserved": True,
    "self_verification_detected": False,
}

REQUIRED_ECOSYSTEM_WORK_ITEM_FIELDS = {
    "work_item_id",
    "request_id",
    "objective_id",
    "candidate_id",
    "source",
    "current_owner",
    "next_owner",
    "state",
    "blocking_reason",
    "evidence_refs",
    "specialist_route",
    "verification_state",
    "terminal_state",
    "authority_domain",
    "change_class",
    "decision_right",
    "required_verifier",
    "promotion_state",
}

WORK_ITEM_STATES = {"ADMITTED", "ROUTED", "IN_PROGRESS", "BLOCKED", "TERMINATED"}
ALLOWED_CHANGE_CLASSES = {"A", "B", "C", "NONE"}
ALLOWED_VERIFICATION_STATES = {"NONE", "NOT_REQUIRED", "PENDING", "VERIFIED", "REJECTED"}
ALLOWED_PROMOTION_STATES = {
    "NOT_APPLICABLE",
    "NOT_REQUESTED",
    "PENDING",
    "VERIFIED",
    "REJECTED",
    "APPROVED",
    "PROMOTED",
}
PROMOTED_STATES = {"APPROVED", "PROMOTED"}


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _normalized_status(value: Any) -> str:
    status = str(value or "UNKNOWN").upper()
    return status if status in ALLOWED_STATUS else "UNKNOWN"


def _golden_paths(registry: dict[str, Any]) -> dict[str, list[str]]:
    paths = registry.get("golden_paths")
    if isinstance(paths, dict):
        normalized: dict[str, list[str]] = {}
        for name, handoffs in paths.items():
            if isinstance(handoffs, list):
                normalized[str(name)] = [str(item) for item in handoffs]
        return normalized

    legacy = registry.get("golden_user_path")
    if isinstance(legacy, list):
        return {"WOW_PROP": [str(item) for item in legacy]}
    return {}


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if registry.get("version") != REGISTRY_VERSION:
        errors.append(f"registry version must be {REGISTRY_VERSION}")
    if registry.get("runtime_generation") != "V17_ACTIVE":
        errors.append("runtime_generation must remain V17_ACTIVE")
    if registry.get("terminal_authority") != TERMINAL_AUTHORITY:
        errors.append(f"terminal_authority must be {TERMINAL_AUTHORITY}")
    if registry.get("can_execute") is not False:
        errors.append("can_execute must remain false")

    invariants = registry.get("invariants")
    if not isinstance(invariants, dict):
        errors.append("invariants must be an object")
    else:
        required_false = {
            "conductor_probability_authority",
            "systems_intelligence_probability_authority",
            "engineering_probability_authority",
            "independent_verification_probability_authority",
        }
        for key in sorted(required_false):
            if invariants.get(key) is not False:
                errors.append(f"invariants.{key} must be false")

        required_true = {
            "typed_failures_must_be_preserved",
            "specialist_ownership_must_be_preserved",
            "market_evidence_cannot_substitute_for_governed_probability",
            "scout_research_cannot_substitute_for_governed_probability",
            "kalshi_market_price_cannot_mutate_weather_probability",
            "self_verification_forbidden",
            "work_conservation_required",
        }
        for key in sorted(required_true):
            if invariants.get(key) is not True:
                errors.append(f"invariants.{key} must be true")

    components = registry.get("components")
    if not isinstance(components, dict):
        return errors + ["components must be an object"]

    missing_components = REQUIRED_COMPONENTS - set(components)
    if missing_components:
        errors.append(f"missing required components: {sorted(missing_components)}")

    terminal_owners = [
        name
        for name, spec in components.items()
        if isinstance(spec, dict) and spec.get("terminal_authority") is True
    ]
    if terminal_owners != [TERMINAL_AUTHORITY]:
        errors.append("exactly V17_TERMINAL_REDUCER must own global terminal authority")

    safe_hold_owners = [
        name
        for name, spec in components.items()
        if isinstance(spec, dict) and spec.get("safe_hold_authority") is True
    ]
    if safe_hold_owners != [SAFE_HOLD_AUTHORITY]:
        errors.append(
            "exactly SYSTEMS_INTELLIGENCE_RELIABILITY must own ecosystem SAFE_HOLD authority"
        )

    for name, spec in sorted(components.items()):
        if not isinstance(spec, dict):
            errors.append(f"components.{name} must be an object")
            continue
        if "probability_authority" not in spec:
            errors.append(f"components.{name}.probability_authority is required")
        if not str(spec.get("authority_domain") or "").strip():
            errors.append(f"components.{name}.authority_domain is required")

    conductor_spec = components.get(CONDUCTOR) or {}
    if conductor_spec.get("probability_authority") is not False:
        errors.append("WOW_ECOSYSTEM_CONDUCTOR may not own probability authority")
    if conductor_spec.get("terminal_authority") is not False:
        errors.append("WOW_ECOSYSTEM_CONDUCTOR may not own terminal authority")
    if conductor_spec.get("safe_hold_authority") is not False:
        errors.append("WOW_ECOSYSTEM_CONDUCTOR may not own SAFE_HOLD authority")

    handoffs = registry.get("handoffs")
    if not isinstance(handoffs, dict) or not handoffs:
        return errors + ["handoffs must be a non-empty object"]

    valid_nodes = set(components) | EXTERNAL_ENDPOINTS
    for handoff_id, spec in sorted(handoffs.items()):
        prefix = f"handoffs.{handoff_id}"
        if not isinstance(spec, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if spec.get("source") not in valid_nodes:
            errors.append(f"{prefix}.source is not a registered endpoint")
        if spec.get("target") not in valid_nodes:
            errors.append(f"{prefix}.target is not a registered endpoint")
        if not isinstance(spec.get("critical"), bool):
            errors.append(f"{prefix}.critical must be boolean")
        hold_on = spec.get("hold_on")
        if not isinstance(hold_on, list):
            errors.append(f"{prefix}.hold_on must be a list")
        else:
            bad = sorted(set(map(str, hold_on)) - ALLOWED_STATUS)
            if bad:
                errors.append(f"{prefix}.hold_on contains invalid states: {bad}")
        if not str(spec.get("decision_right") or "").strip():
            errors.append(f"{prefix}.decision_right is required")
        required_verifier = spec.get("required_verifier")
        if required_verifier != "NONE" and required_verifier not in components:
            errors.append(f"{prefix}.required_verifier is not a registered component")

    paths = _golden_paths(registry)
    if not paths:
        errors.append("at least one golden path is required")
    for path_name, path_handoffs in sorted(paths.items()):
        if not path_handoffs:
            errors.append(f"golden path {path_name} must not be empty")
            continue
        for handoff_id in path_handoffs:
            if handoff_id not in handoffs:
                errors.append(
                    f"golden path {path_name} references unknown handoff {handoff_id}"
                )
        if path_handoffs and handoffs.get(path_handoffs[0], {}).get("source") != "USER":
            errors.append(f"golden path {path_name} must begin at USER")
        if path_handoffs and handoffs.get(path_handoffs[-1], {}).get("target") != "USER":
            errors.append(f"golden path {path_name} must terminate at USER")
        if not any(
            handoffs.get(handoff_id, {}).get("source") == CONDUCTOR
            or handoffs.get(handoff_id, {}).get("target") == CONDUCTOR
            for handoff_id in path_handoffs
        ):
            errors.append(f"golden path {path_name} must traverse {CONDUCTOR}")

    contract = registry.get("ecosystem_work_item_envelope_contract")
    if not isinstance(contract, dict):
        errors.append("ecosystem_work_item_envelope_contract must be an object")
    else:
        required = set(map(str, contract.get("required_fields") or []))
        missing_fields = REQUIRED_ECOSYSTEM_WORK_ITEM_FIELDS - required
        if missing_fields:
            errors.append(
                f"ecosystem_work_item_envelope_contract missing required fields: {sorted(missing_fields)}"
            )
        if contract.get("silent_drop_forbidden") is not True:
            errors.append("ecosystem_work_item_envelope_contract.silent_drop_forbidden must be true")
        if contract.get("self_verification_forbidden") is not True:
            errors.append("ecosystem_work_item_envelope_contract.self_verification_forbidden must be true")
        if (
            contract.get("class_c_requires_independent_verification_before_promotion")
            is not True
        ):
            errors.append(
                "ecosystem_work_item_envelope_contract.class_c_requires_independent_verification_before_promotion must be true"
            )

    return errors


def _evaluate_runtime_invariants(
    observed: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    supplied = observed.get("invariants")
    supplied = supplied if isinstance(supplied, dict) else {}

    violations: list[dict[str, Any]] = []
    normalized: dict[str, Any] = {}
    for key, expected in REQUIRED_RUNTIME_INVARIANTS.items():
        actual = supplied.get(key, "MISSING")
        normalized[key] = actual
        if actual != expected:
            violations.append(
                {"invariant": key, "expected": expected, "observed": actual}
            )
    return violations, normalized


def _path_component_names(
    registry: dict[str, Any],
    path_handoffs: list[str],
) -> set[str]:
    components = set(registry["components"])
    names: set[str] = set()
    for handoff_id in path_handoffs:
        spec = registry["handoffs"][handoff_id]
        if spec["source"] in components:
            names.add(spec["source"])
        if spec["target"] in components:
            names.add(spec["target"])
    return names


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_ecosystem_work_item_envelope(
    item: Any,
    registry: dict[str, Any],
    *,
    index: int | None = None,
) -> list[str]:
    label = f"ecosystem_work_items[{index}]" if index is not None else "ecosystem_work_item"
    if not isinstance(item, dict):
        return [f"{label} must be an object"]

    errors: list[str] = []
    missing = sorted(REQUIRED_ECOSYSTEM_WORK_ITEM_FIELDS - set(item))
    if missing:
        errors.append(f"{label} missing required fields: {missing}")
        return errors

    for key in ("work_item_id", "request_id", "objective_id", "source", "current_owner"):
        if not _nonempty(item.get(key)):
            errors.append(f"{label}.{key} must be a non-empty string")

    candidate_id = item.get("candidate_id")
    if candidate_id is not None and not _nonempty(candidate_id):
        errors.append(f"{label}.candidate_id must be null or a non-empty string")

    components = registry["components"]
    current_owner = item.get("current_owner")
    next_owner = item.get("next_owner")
    if current_owner not in components:
        errors.append(f"{label}.current_owner is not a registered component")
    if next_owner is not None and next_owner not in components and next_owner not in EXTERNAL_ENDPOINTS:
        errors.append(f"{label}.next_owner is not a registered endpoint")

    state = str(item.get("state") or "").upper()
    if state not in WORK_ITEM_STATES:
        errors.append(f"{label}.state must be one of {sorted(WORK_ITEM_STATES)}")

    evidence_refs = item.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        errors.append(f"{label}.evidence_refs must be a list")
    elif any(not _nonempty(ref) for ref in evidence_refs):
        errors.append(f"{label}.evidence_refs must contain non-empty strings")

    change_class = str(item.get("change_class") or "").upper()
    if change_class not in ALLOWED_CHANGE_CLASSES:
        errors.append(
            f"{label}.change_class must be one of {sorted(ALLOWED_CHANGE_CLASSES)}"
        )

    verification_state = str(item.get("verification_state") or "").upper()
    if verification_state not in ALLOWED_VERIFICATION_STATES:
        errors.append(
            f"{label}.verification_state must be one of {sorted(ALLOWED_VERIFICATION_STATES)}"
        )

    promotion_state = str(item.get("promotion_state") or "").upper()
    if promotion_state not in ALLOWED_PROMOTION_STATES:
        errors.append(
            f"{label}.promotion_state must be one of {sorted(ALLOWED_PROMOTION_STATES)}"
        )

    if not _nonempty(item.get("decision_right")):
        errors.append(f"{label}.decision_right must be a non-empty string")

    required_verifier = item.get("required_verifier")
    if required_verifier != "NONE" and required_verifier not in components:
        errors.append(f"{label}.required_verifier is not a registered component")

    if current_owner in components:
        expected_domain = components[current_owner].get("authority_domain")
        if item.get("authority_domain") != expected_domain:
            errors.append(
                f"{label}.authority_domain must match current_owner authority_domain"
            )

    if state == "BLOCKED" and not _nonempty(item.get("blocking_reason")):
        errors.append(f"{label}.blocking_reason is required when state=BLOCKED")
    elif state != "BLOCKED" and item.get("blocking_reason") not in (None, ""):
        errors.append(f"{label}.blocking_reason must be empty unless state=BLOCKED")

    terminal_state = item.get("terminal_state")
    if state == "TERMINATED":
        if not _nonempty(terminal_state):
            errors.append(f"{label}.terminal_state is required when state=TERMINATED")
        if next_owner is not None:
            errors.append(f"{label}.next_owner must be null when state=TERMINATED")
    elif terminal_state not in (None, ""):
        errors.append(f"{label}.terminal_state must be empty unless state=TERMINATED")

    if state in {"ADMITTED", "ROUTED"} and next_owner is None:
        errors.append(f"{label}.next_owner is required while work is awaiting transfer")

    if (
        current_owner == "ENGINEERING_CLOSURE"
        and verification_state == "VERIFIED"
        and required_verifier == "ENGINEERING_CLOSURE"
    ):
        errors.append(f"{label} may not self-verify Engineering closure")

    if (
        change_class == "C"
        and promotion_state in PROMOTED_STATES
        and (
            required_verifier != INDEPENDENT_VERIFIER
            or verification_state != "VERIFIED"
        )
    ):
        errors.append(
            f"{label} Class C promotion requires VERIFIED independent verification"
        )

    if str(terminal_state or "").upper() == "FIXED_AND_VERIFIED":
        if verification_state != "VERIFIED":
            errors.append(
                f"{label} FIXED_AND_VERIFIED requires verification_state=VERIFIED"
            )
        if required_verifier != INDEPENDENT_VERIFIER:
            errors.append(
                f"{label} FIXED_AND_VERIFIED requires INDEPENDENT_VERIFICATION"
            )

    return errors


def evaluate_ecosystem_work_conservation(
    registry: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    items = observed.get("ecosystem_work_items")
    if not isinstance(items, list):
        return {
            "status": "FAIL",
            "work_conservation_pass": False,
            "items_in": 0,
            "items_terminal": 0,
            "items_active": 0,
            "items_blocked": 0,
            "invalid_items": [
                {
                    "work_item_id": None,
                    "errors": ["ecosystem_work_items must be supplied as a list"],
                }
            ],
            "duplicate_work_item_ids": [],
            "terminal_disposition_rate": None,
        }

    invalid_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    terminal = 0
    blocked = 0

    for index, item in enumerate(items):
        errors = validate_ecosystem_work_item_envelope(item, registry, index=index)
        work_item_id = item.get("work_item_id") if isinstance(item, dict) else None
        if _nonempty(work_item_id):
            if work_item_id in seen_ids:
                duplicate_ids.add(work_item_id)
            seen_ids.add(work_item_id)
        if errors:
            invalid_items.append({"work_item_id": work_item_id, "errors": errors})
        if isinstance(item, dict):
            state = str(item.get("state") or "").upper()
            terminal += int(state == "TERMINATED")
            blocked += int(state == "BLOCKED")

    if duplicate_ids:
        invalid_items.append(
            {
                "work_item_id": None,
                "errors": [f"duplicate work_item_id values: {sorted(duplicate_ids)}"],
            }
        )

    total = len(items)
    active = total - terminal
    pass_state = not invalid_items
    return {
        "status": "PASS" if pass_state else "FAIL",
        "work_conservation_pass": pass_state,
        "items_in": total,
        "items_terminal": terminal,
        "items_active": active,
        "items_blocked": blocked,
        "invalid_items": invalid_items,
        "duplicate_work_item_ids": sorted(duplicate_ids),
        "terminal_disposition_rate": (terminal / total) if total else None,
    }


def evaluate_ecosystem(
    registry: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    registry_errors = validate_registry(registry)
    if registry_errors:
        return {
            "ecosystem_status": "SAFE_HOLD",
            "safe_hold_required": True,
            "safe_hold_authority": SAFE_HOLD_AUTHORITY,
            "reason": "REGISTRY_INVALID",
            "registry_errors": registry_errors,
            "can_execute": CAN_EXECUTE,
            "terminal_authority": TERMINAL_AUTHORITY,
        }

    component_observed = observed.get("components")
    component_observed = (
        component_observed if isinstance(component_observed, dict) else {}
    )
    component_states = {
        name: _normalized_status(component_observed.get(name))
        for name in registry["components"]
    }

    handoff_observed = observed.get("handoffs")
    handoff_observed = handoff_observed if isinstance(handoff_observed, dict) else {}

    handoff_states: dict[str, str] = {}
    broken_handoffs: list[dict[str, Any]] = []
    degraded_handoffs: list[dict[str, Any]] = []

    for handoff_id, spec in registry["handoffs"].items():
        status = _normalized_status(handoff_observed.get(handoff_id))
        handoff_states[handoff_id] = status
        item = {
            "handoff_id": handoff_id,
            "source": spec["source"],
            "target": spec["target"],
            "status": status,
            "critical": spec["critical"],
            "decision_right": spec["decision_right"],
            "required_verifier": spec["required_verifier"],
        }
        if status in set(spec.get("hold_on", [])):
            broken_handoffs.append(item)
        elif status == "DEGRADED":
            degraded_handoffs.append(item)

    invariant_violations, invariant_state = _evaluate_runtime_invariants(observed)
    work_conservation = evaluate_ecosystem_work_conservation(registry, observed)

    path_results: dict[str, dict[str, Any]] = {}
    any_false_green = False
    for path_name, path_handoffs in _golden_paths(registry).items():
        path_handoff_states = {
            handoff_id: handoff_states[handoff_id] for handoff_id in path_handoffs
        }
        path_components = _path_component_names(registry, path_handoffs)
        path_component_states = {
            name: component_states[name] for name in sorted(path_components)
        }

        handoffs_ready = all(
            status == "PASS" for status in path_handoff_states.values()
        )
        components_ready = all(
            status == "PASS" for status in path_component_states.values()
        )
        ready = (
            handoffs_ready
            and components_ready
            and not invariant_violations
            and work_conservation["work_conservation_pass"]
        )

        false_green = components_ready and (
            not handoffs_ready or not work_conservation["work_conservation_pass"]
        )
        any_false_green = any_false_green or false_green

        path_results[path_name] = {
            "status": "READY" if ready else "NOT_READY",
            "handoffs": path_handoff_states,
            "components": path_component_states,
            "false_green_detected": false_green,
        }

    safe_hold_required = bool(
        invariant_violations
        or broken_handoffs
        or not work_conservation["work_conservation_pass"]
    )
    if safe_hold_required:
        ecosystem_status = "SAFE_HOLD"
    elif degraded_handoffs or any(
        state == "DEGRADED" for state in component_states.values()
    ):
        ecosystem_status = "DEGRADED"
    elif all(item["status"] == "READY" for item in path_results.values()):
        ecosystem_status = "READY"
    else:
        ecosystem_status = "DEGRADED"

    return {
        "ecosystem_status": ecosystem_status,
        "safe_hold_required": safe_hold_required,
        "safe_hold_authority": SAFE_HOLD_AUTHORITY,
        "false_green_detected": any_false_green,
        "component_states": component_states,
        "handoff_states": handoff_states,
        "broken_handoffs": broken_handoffs,
        "degraded_handoffs": degraded_handoffs,
        "invariants": invariant_state,
        "invariant_violations": invariant_violations,
        "work_conservation": work_conservation,
        "golden_paths": path_results,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate WOW ecosystem conductor readiness"
    )
    parser.add_argument("--registry", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()

    registry = load_json(args.registry)
    observed = load_json(args.state)
    result = evaluate_ecosystem(registry, observed)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ecosystem_status"] != "SAFE_HOLD" else 2


if __name__ == "__main__":
    raise SystemExit(main())
