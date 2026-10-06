#!/usr/bin/env python3
"""Generate and verify the V17 production acceptance route registry.

The registry is an allowlist for the read-only Production Acceptance Sentinel.
It is derived from the canonical Render entrypoint's mounted FastAPI routes and
cross-referenced with canonical WOW/LLP Action OpenAPI contracts where those
routes are intentionally public. Internal acceptance-only routes remain
runtime-mounted contracts and are never added to public Action schemas.

This module intentionally does not call the application's full OpenAPI builder.
The repository has historically carried legacy forward-reference seams that can
make full OpenAPI materialization fail even while mounted production routes are
valid. Route discovery therefore reads app.router.routes directly and hashes
request/response annotations with Pydantic TypeAdapter where resolvable.

can_execute=false remains invariant. This tool has no wager/order authority.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, get_type_hints

import yaml
from fastapi.routing import APIRoute
from pydantic import TypeAdapter


ENGINE_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ENGINE_ROOT / "v17" / "production_acceptance_routes.json"
WOW_OPENAPI = ENGINE_ROOT / "v17" / "openapi.wow-betting-engine.v17.yaml"
LLP_OPENAPI = ENGINE_ROOT / "v17" / "openapi.llp-team-engine.v17.yaml"
PRODUCTION_ENTRYPOINT = "api_ncaaf_acceptance:app"
LLP_GATEWAY_PREFIX = "/functions/v1/wow-llp-action-gateway"
SCHEMA_VERSION = "1.0.0"
POLICY_VERSION = "WOW_V17_PRODUCTION_ACCEPTANCE_ALLOWLIST_V1"

ACCEPTANCE_POLICY: dict[tuple[str, str], dict[str, Any]] = {
    ("/internal/v17/wnba-spread-forward-auto-canary", "POST"): {
        "acceptance_role": "WNBA_SPREAD",
        "contract_scope": "RUNTIME_INTERNAL_ACCEPTANCE",
        "required_headers": {
            "X-WOW-Acceptance-Mode": "true",
            "X-WOW-Runtime-Generation": "V17_ACTIVE",
        },
        "required_response_invariants": {
            "can_execute": False,
            "probability_publishable": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "dry_run_only_no_live_trading_no_market_orders": True,
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "identity_acquisition_location": "BACKEND_RUNTIME",
        },
    },
    ("/internal/v17/wnba-prop-forward-evidence/acquire", "POST"): {
        "acceptance_role": "WNBA_PROP_EVIDENCE",
        "contract_scope": "RUNTIME_INTERNAL_ACCEPTANCE",
        "required_headers": {
            "X-WOW-Acceptance-Mode": "true",
            "X-WOW-Runtime-Generation": "V17_ACTIVE",
        },
        "required_response_invariants": {
            "can_execute": False,
            "probability_publishable": False,
            "automatic_certification": False,
            "automatic_promotion": False,
        },
    },
}


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_type_schema(annotation: Any) -> dict[str, Any]:
    if annotation in (None, inspect.Signature.empty):
        return {}
    try:
        return TypeAdapter(annotation).json_schema()
    except Exception as exc:
        return {
            "unresolved_annotation": repr(annotation),
            "error_type": type(exc).__name__,
        }


def _request_schema(route: APIRoute) -> dict[str, Any]:
    body = getattr(route, "body_field", None)
    if body is None:
        return {}
    annotation = getattr(getattr(body, "field_info", None), "annotation", None)
    if annotation is None:
        annotation = getattr(body, "type_", None)
    return _safe_type_schema(annotation)


def _response_schema(route: APIRoute) -> dict[str, Any]:
    if route.response_model is not None:
        return _safe_type_schema(route.response_model)
    try:
        hints = get_type_hints(route.endpoint)
        return _safe_type_schema(hints.get("return"))
    except Exception:
        return _safe_type_schema(inspect.signature(route.endpoint).return_annotation)


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"INVALID_OPENAPI_DOCUMENT:{path}")
    return payload


def _canonical_contract_routes() -> dict[tuple[str, str], dict[str, Any]]:
    contracts: dict[tuple[str, str], dict[str, Any]] = {}
    for source, path in (("WOW_ACTION", WOW_OPENAPI), ("LLP_ACTION", LLP_OPENAPI)):
        spec = _load_yaml(path)
        for raw_path, methods in (spec.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            runtime_path = str(raw_path)
            if source == "LLP_ACTION" and runtime_path.startswith(LLP_GATEWAY_PREFIX):
                runtime_path = runtime_path[len(LLP_GATEWAY_PREFIX):] or "/"
            for method, operation in methods.items():
                method_upper = str(method).upper()
                if method_upper not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                if not isinstance(operation, dict):
                    continue
                contracts[(runtime_path, method_upper)] = {
                    "contract_source": source,
                    "operation_id": operation.get("operationId"),
                }
    return contracts


def _load_production_app() -> Any:
    os.environ.setdefault("WOW_V17_ACTIVE", "1")
    os.environ.setdefault("WOW_CAN_EXECUTE", "false")
    os.environ.setdefault("WOW_DRY_RUN_ONLY", "true")
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    module_name, attr = PRODUCTION_ENTRYPOINT.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def build_registry(app: Any) -> dict[str, Any]:
    mounted: dict[tuple[str, str], APIRoute] = {}
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or set()):
            mounted[(str(route.path), str(method).upper())] = route

    canonical = _canonical_contract_routes()
    entries: list[dict[str, Any]] = []

    for key, policy in sorted(ACCEPTANCE_POLICY.items()):
        path, method = key
        route = mounted.get(key)
        if route is None:
            raise RuntimeError(f"ACCEPTANCE_ROUTE_NOT_MOUNTED:{method}:{path}")

        request_schema = _request_schema(route)
        response_schema = _response_schema(route)
        contract = canonical.get(key)
        contract_scope = str(policy["contract_scope"])

        if contract_scope != "RUNTIME_INTERNAL_ACCEPTANCE" and contract is None:
            raise RuntimeError(f"ACCEPTANCE_ROUTE_MISSING_CANONICAL_CONTRACT:{method}:{path}")

        operation_id = str(route.operation_id or route.name or "")
        if contract and contract.get("operation_id") and operation_id != contract["operation_id"]:
            raise RuntimeError(
                f"ACCEPTANCE_OPERATION_ID_DRIFT:{method}:{path}:"
                f"{operation_id}:{contract['operation_id']}"
            )

        request_hash = _canonical_hash(request_schema)
        response_hash = _canonical_hash(response_schema)
        acceptance_contract_hash = _canonical_hash(
            {
                "request_schema_hash": request_hash,
                "response_schema_hash": response_hash,
                "required_headers": policy["required_headers"],
                "required_response_invariants": policy["required_response_invariants"],
            }
        )

        entries.append(
            {
                "path": path,
                "method": method,
                "operation_id": operation_id,
                "acceptance_role": policy["acceptance_role"],
                "contract_scope": contract_scope,
                "contract_source": (contract or {}).get("contract_source", "RUNTIME_MOUNT"),
                "request_schema_hash": request_hash,
                "response_schema_hash": response_hash,
                "acceptance_contract_hash": acceptance_contract_hash,
                "required_headers": policy["required_headers"],
                "required_response_invariants": policy["required_response_invariants"],
                "can_execute_required": False,
            }
        )

    return {
        "runtime_generation": "V17_ACTIVE",
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "production_entrypoint": PRODUCTION_ENTRYPOINT,
        "total_acceptance_routes": len(entries),
        "routes": entries,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()

    registry = build_registry(_load_production_app())

    if args.write:
        REGISTRY_PATH.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"WROTE:{REGISTRY_PATH}")
        return 0

    if not REGISTRY_PATH.exists():
        print(f"FAIL:REGISTRY_MISSING:{REGISTRY_PATH}", file=sys.stderr)
        return 1

    existing = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if existing != registry:
        print("FAIL:PRODUCTION_ACCEPTANCE_ROUTE_REGISTRY_DRIFT", file=sys.stderr)
        print(json.dumps({"expected": existing, "generated": registry}, indent=2, sort_keys=True))
        return 1

    print(
        json.dumps(
            {
                "status": "PASS",
                "code": "PRODUCTION_ACCEPTANCE_ROUTE_REGISTRY_MATCH",
                "route_count": registry["total_acceptance_routes"],
                "can_execute": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
