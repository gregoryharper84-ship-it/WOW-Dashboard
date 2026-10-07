"""Deterministic WOW ecosystem conductor and connection-integrity control plane.

Class A only: this module coordinates metadata, readiness, handoffs, and
truthful status reporting. It never scores sporting events, changes model
math/calibration, overrides specialist ownership, publishes a probability,
or grants wagering/trading execution authority.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REGISTRY_VERSION = "1.0"
CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

ALLOWED_STATUS = {
    "PASS",
    "DEGRADED",
    "FAIL",
    "BLOCKED",
    "UNKNOWN",
    "NOT_APPLICABLE",
}
FAIL_CLOSED_STATUS = {"FAIL", "BLOCKED", "UNKNOWN"}

REQUIRED_COMPONENTS = {
    "WOW_BETTING_INTELLIGENCE",
    "SCOUT",
    "WOW_BETTING_ENGINE",
    "LLP_TEAM_BETTING_ENGINE",
    "KALSHI_WEATHER_EXPERT",
    "SYSTEMS_INTELLIGENCE_RELIABILITY",
    "ENGINEERING_CLOSURE",
    "INDEPENDENT_VERIFICATION",
    "V17_TERMINAL_REDUCER",
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


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _normalized_status(value: Any) -> str:
    status = str(value or "UNKNOWN").upper()
    return status if status in ALLOWED_STATUS else "UNKNOWN"


def _golden_paths(registry: dict[str, Any]) -> dict[str, list[str]]:
    """Support the canonical mapping and the initial single-path bootstrap."""
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
        name for name, spec in components.items()
        if isinstance(spec, dict) and spec.get("terminal_authority") is True
    ]
    if terminal_owners != [TERMINAL_AUTHORITY]:
        errors.append(
            "exactly V17_TERMINAL_REDUCER must own global terminal authority"
        )

    for name, spec in sorted(components.items()):
        if not isinstance(spec, dict):
            errors.append(f"components.{name} must be an object")
            continue
        if "probability_authority" not in spec:
            errors.append(f"components.{name}.probability_authority is required")
        if spec.get("safe_hold_authority") is True and name != "SYSTEMS_INTELLIGENCE_RELIABILITY":
            errors.append(
                f"components.{name} may not own ecosystem SAFE_HOLD authority"
            )

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


def evaluate_ecosystem(
    registry: dict[str, Any],
    observed: dict[str, Any],
) -> dict[str, Any]:
    registry_errors = validate_registry(registry)
    if registry_errors:
        return {
            "ecosystem_status": "SAFE_HOLD",
            "safe_hold_required": True,
            "reason": "REGISTRY_INVALID",
            "registry_errors": registry_errors,
            "can_execute": CAN_EXECUTE,
            "terminal_authority": TERMINAL_AUTHORITY,
        }

    component_observed = observed.get("components")
    component_observed = component_observed if isinstance(component_observed, dict) else {}
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
        }
        if status in set(spec.get("hold_on", [])):
            broken_handoffs.append(item)
        elif status == "DEGRADED":
            degraded_handoffs.append(item)

    invariant_violations, invariant_state = _evaluate_runtime_invariants(observed)

    path_results: dict[str, dict[str, Any]] = {}
    any_false_green = False
    for path_name, path_handoffs in _golden_paths(registry).items():
        path_handoff_states = {
            handoff_id: handoff_states[handoff_id]
            for handoff_id in path_handoffs
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
        )

        false_green = components_ready and not handoffs_ready
        any_false_green = any_false_green or false_green

        path_results[path_name] = {
            "status": "READY" if ready else "NOT_READY",
            "handoffs": path_handoff_states,
            "components": path_component_states,
            "false_green_detected": false_green,
        }

    safe_hold_required = bool(invariant_violations or broken_handoffs)
    if safe_hold_required:
        ecosystem_status = "SAFE_HOLD"
    elif degraded_handoffs or any(state == "DEGRADED" for state in component_states.values()):
        ecosystem_status = "DEGRADED"
    elif all(item["status"] == "READY" for item in path_results.values()):
        ecosystem_status = "READY"
    else:
        ecosystem_status = "DEGRADED"

    return {
        "ecosystem_status": ecosystem_status,
        "safe_hold_required": safe_hold_required,
        "false_green_detected": any_false_green,
        "component_states": component_states,
        "handoff_states": handoff_states,
        "broken_handoffs": broken_handoffs,
        "degraded_handoffs": degraded_handoffs,
        "invariants": invariant_state,
        "invariant_violations": invariant_violations,
        "golden_paths": path_results,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate WOW ecosystem conductor readiness")
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
