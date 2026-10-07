"""Read-only authoritative probes for the WOW Ecosystem Conductor.

Class A observability only. This module gathers proof and normalizes connection
state. It never scores events, changes probability/calibration, mutates runtime
state, writes wagers/trades, or grants execution authority.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import error, parse, request

import ecosystem_conductor

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
VALID_STATUSES = {
    "PASS",
    "DEGRADED",
    "FAIL",
    "BLOCKED",
    "UNKNOWN",
    "NOT_APPLICABLE",
}
FAIL_CLOSED_PRECEDENCE = {
    "FAIL": 6,
    "BLOCKED": 5,
    "UNKNOWN": 4,
    "DEGRADED": 3,
    "PASS": 2,
    "NOT_APPLICABLE": 1,
}


@dataclass(frozen=True)
class ProbeReceipt:
    probe_id: str
    probe_type: str
    observed_at: str
    status: str
    evidence_refs: tuple[str, ...] = ()
    first_failing_boundary: str | None = None
    typed_failure: str | None = None
    source_version: str | None = None
    deployed_sha: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_refs"] = list(self.evidence_refs)
        return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _status(value: Any) -> str:
    normalized = str(value or "UNKNOWN").upper()
    return normalized if normalized in VALID_STATUSES else "UNKNOWN"


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "UNKNOWN"
    return max((_status(value) for value in statuses), key=FAIL_CLOSED_PRECEDENCE.get)


def _dot_get(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _matches_predicates(payload: Any, predicates: Mapping[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for path, expected in predicates.items():
        actual = _dot_get(payload, path)
        if isinstance(expected, Mapping):
            if expected.get("present") is True and actual is None:
                failures.append(f"{path}:MISSING")
                continue
            if "in" in expected and actual not in expected["in"]:
                failures.append(f"{path}:{actual!r}:NOT_IN:{expected['in']!r}")
                continue
            if "equals" in expected and actual != expected["equals"]:
                failures.append(f"{path}:{actual!r}!={expected['equals']!r}")
                continue
        elif actual != expected:
            failures.append(f"{path}:{actual!r}!={expected!r}")
    return not failures, failures


def _headers(spec: Mapping[str, Any], env: Mapping[str, str]) -> tuple[dict[str, str], str | None]:
    headers = {"Accept": "application/json", "User-Agent": "wow-ecosystem-conductor/1.0"}
    token_env = str(spec.get("bearer_env") or "").strip()
    if token_env:
        token = str(env.get(token_env) or "").strip()
        if not token:
            return headers, token_env
        headers["Authorization"] = f"Bearer {token}"
    api_key_env = str(spec.get("api_key_env") or "").strip()
    if api_key_env:
        key = str(env.get(api_key_env) or "").strip()
        if not key:
            return headers, api_key_env
        headers["apikey"] = key
        headers.setdefault("Authorization", f"Bearer {key}")
    for key, value in (spec.get("headers") or {}).items():
        headers[str(key)] = str(value)
    return headers, None


def _open_json(
    url: str,
    headers: Mapping[str, str],
    *,
    opener: Callable[..., Any],
    timeout: float,
) -> tuple[int, Any]:
    req = request.Request(url, headers=dict(headers), method="GET")
    try:
        with opener(req, timeout=timeout) as response:
            code = int(getattr(response, "status", 200))
            raw = response.read()
    except error.HTTPError as exc:
        code = int(exc.code)
        raw = exc.read()
    if not raw:
        return code, None
    try:
        return code, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return code, {"_raw": raw.decode("utf-8", errors="replace")}


def _http_failure_status(code: int) -> tuple[str, str]:
    if code in {401, 403}:
        return "BLOCKED", "PROBE_AUTHORIZATION_BLOCKED"
    if code == 429:
        return "DEGRADED", "PROBE_RATE_LIMITED"
    if 400 <= code < 500:
        return "FAIL", f"PROBE_HTTP_{code}"
    if code >= 500:
        return "FAIL", f"PROBE_HTTP_{code}"
    return "UNKNOWN", "PROBE_HTTP_UNEXPECTED_STATUS"


def probe_http_json(
    probe_id: str,
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    opener: Callable[..., Any] = request.urlopen,
) -> ProbeReceipt:
    observed_at = _utc_now()
    url = str(spec.get("url") or "").strip()
    origin_env = str(spec.get("origin_env") or "").strip()
    if not url and origin_env:
        origin = str(env.get(origin_env) or "").strip().rstrip("/")
        path = str(spec.get("path") or "").strip()
        if origin:
            url = f"{origin}/{path.lstrip('/')}"
    if not url:
        return ProbeReceipt(
            probe_id,
            "http_json",
            observed_at,
            "UNKNOWN",
            first_failing_boundary="PROBE_CONFIGURATION",
            typed_failure="PROBE_ORIGIN_MISSING",
        )

    headers, missing_secret = _headers(spec, env)
    if missing_secret:
        return ProbeReceipt(
            probe_id,
            "http_json",
            observed_at,
            "BLOCKED",
            evidence_refs=(url,),
            first_failing_boundary="PROBE_AUTHENTICATION",
            typed_failure=f"PROBE_SECRET_MISSING:{missing_secret}",
        )

    try:
        code, payload = _open_json(
            url,
            headers,
            opener=opener,
            timeout=float(spec.get("timeout_seconds") or 15),
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeReceipt(
            probe_id,
            "http_json",
            observed_at,
            "UNKNOWN",
            evidence_refs=(url,),
            first_failing_boundary="PROBE_TRANSPORT",
            typed_failure="PROBE_TRANSPORT_UNREACHABLE",
            details={"error_type": type(exc).__name__},
        )

    if not 200 <= code < 300:
        status, typed = _http_failure_status(code)
        return ProbeReceipt(
            probe_id,
            "http_json",
            observed_at,
            status,
            evidence_refs=(url,),
            first_failing_boundary="PROBE_HTTP",
            typed_failure=typed,
            details={"http_status": code},
        )

    predicates = spec.get("predicates") or {}
    ok, failures = _matches_predicates(payload, predicates)
    return ProbeReceipt(
        probe_id,
        "http_json",
        observed_at,
        "PASS" if ok else "FAIL",
        evidence_refs=(url,),
        first_failing_boundary=None if ok else "PROBE_CONTRACT_PREDICATE",
        typed_failure=None if ok else "PROBE_CONTRACT_MISMATCH",
        source_version=str(_dot_get(payload, str(spec.get("source_version_path") or "")) or "") or None,
        deployed_sha=str(_dot_get(payload, str(spec.get("deployed_sha_path") or "")) or "") or None,
        details={"http_status": code, "predicate_failures": failures},
    )


def probe_render_runtime(
    probe_id: str,
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    opener: Callable[..., Any] = request.urlopen,
) -> ProbeReceipt:
    observed_at = _utc_now()
    origin_env = str(spec.get("origin_env") or "WOW_BACKEND_ORIGIN")
    origin = str(env.get(origin_env) or "").strip().rstrip("/")
    if not origin:
        return ProbeReceipt(
            probe_id,
            "render_runtime",
            observed_at,
            "UNKNOWN",
            first_failing_boundary="RENDER_ORIGIN",
            typed_failure=f"PROBE_ORIGIN_MISSING:{origin_env}",
        )

    base_spec = dict(spec)
    base_spec["origin_env"] = origin_env
    paths = spec.get("paths") or {
        "health": "/health",
        "governance": "/governance",
        "host_contract": "/v17/host-contract",
    }
    receipts: list[ProbeReceipt] = []
    for name, path in paths.items():
        child = dict(base_spec)
        child["path"] = path
        child["predicates"] = {}
        receipts.append(
            probe_http_json(
                f"{probe_id}:{name}",
                child,
                env=env,
                opener=opener,
            )
        )

    aggregate = _aggregate_status([item.status for item in receipts])
    details: dict[str, Any] = {
        "children": {item.probe_id: item.to_dict() for item in receipts},
    }
    deployed_sha_env = str(spec.get("deployed_sha_env") or "RENDER_GIT_COMMIT")
    deployed_sha = str(env.get(deployed_sha_env) or "").strip() or None
    if spec.get("require_deployed_sha", True) and not deployed_sha:
        aggregate = _aggregate_status([aggregate, "UNKNOWN"])
        details["deployed_sha_missing"] = deployed_sha_env

    typed = None
    boundary = None
    if aggregate != "PASS":
        first = next((item for item in receipts if item.status != "PASS"), None)
        if first:
            typed = first.typed_failure
            boundary = first.first_failing_boundary
        elif not deployed_sha:
            typed = f"PROBE_DEPLOYED_SHA_MISSING:{deployed_sha_env}"
            boundary = "RENDER_DEPLOY_IDENTITY"

    return ProbeReceipt(
        probe_id,
        "render_runtime",
        observed_at,
        aggregate,
        evidence_refs=tuple(f"{origin}{path}" for path in paths.values()),
        first_failing_boundary=boundary,
        typed_failure=typed,
        deployed_sha=deployed_sha,
        details=details,
    )


def probe_github_exact_head(
    probe_id: str,
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    opener: Callable[..., Any] = request.urlopen,
) -> ProbeReceipt:
    observed_at = _utc_now()
    repo_env = str(spec.get("repo_env") or "GITHUB_REPOSITORY")
    sha_env = str(spec.get("sha_env") or "GITHUB_SHA")
    token_env = str(spec.get("bearer_env") or "GITHUB_TOKEN")
    repo = str(env.get(repo_env) or spec.get("repository") or "").strip()
    sha = str(env.get(sha_env) or spec.get("sha") or "").strip()
    token = str(env.get(token_env) or "").strip()
    if not repo or not sha:
        return ProbeReceipt(
            probe_id,
            "github_exact_head",
            observed_at,
            "UNKNOWN",
            first_failing_boundary="GITHUB_EXACT_HEAD_IDENTITY",
            typed_failure="GITHUB_REPOSITORY_OR_SHA_MISSING",
            source_version=sha or None,
        )
    if not token:
        return ProbeReceipt(
            probe_id,
            "github_exact_head",
            observed_at,
            "BLOCKED",
            first_failing_boundary="GITHUB_AUTHENTICATION",
            typed_failure=f"PROBE_SECRET_MISSING:{token_env}",
            source_version=sha,
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "wow-ecosystem-conductor/1.0",
    }
    base = str(spec.get("api_origin") or "https://api.github.com").rstrip("/")
    encoded_repo = parse.quote(repo, safe="/")
    runs_url = (
        f"{base}/repos/{encoded_repo}/actions/runs"
        f"?head_sha={parse.quote(sha)}&event=pull_request&per_page=100"
    )
    try:
        code, payload = _open_json(
            runs_url,
            headers,
            opener=opener,
            timeout=float(spec.get("timeout_seconds") or 15),
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeReceipt(
            probe_id,
            "github_exact_head",
            observed_at,
            "UNKNOWN",
            evidence_refs=(runs_url,),
            first_failing_boundary="GITHUB_TRANSPORT",
            typed_failure="PROBE_TRANSPORT_UNREACHABLE",
            source_version=sha,
            details={"error_type": type(exc).__name__},
        )
    if not 200 <= code < 300:
        status, typed = _http_failure_status(code)
        return ProbeReceipt(
            probe_id,
            "github_exact_head",
            observed_at,
            status,
            evidence_refs=(runs_url,),
            first_failing_boundary="GITHUB_ACTIONS",
            typed_failure=typed,
            source_version=sha,
            details={"http_status": code},
        )

    workflow_runs = payload.get("workflow_runs", []) if isinstance(payload, Mapping) else []
    required = [str(item) for item in spec.get("required_workflows") or []]
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for row in workflow_runs:
        if isinstance(row, Mapping):
            by_name.setdefault(str(row.get("name") or ""), []).append(row)

    missing: list[str] = []
    pending: list[str] = []
    failed: list[str] = []
    for name in required:
        rows = by_name.get(name) or []
        if not rows:
            missing.append(name)
            continue
        latest = sorted(rows, key=lambda item: int(item.get("run_number") or 0), reverse=True)[0]
        if latest.get("status") != "completed":
            pending.append(name)
        elif latest.get("conclusion") != "success":
            failed.append(f"{name}:{latest.get('conclusion')}")

    if failed:
        status = "FAIL"
        typed = "GITHUB_REQUIRED_WORKFLOW_FAILED"
    elif missing or pending:
        status = "UNKNOWN"
        typed = "GITHUB_EXACT_HEAD_PROOF_INCOMPLETE"
    else:
        status = "PASS"
        typed = None

    return ProbeReceipt(
        probe_id,
        "github_exact_head",
        observed_at,
        status,
        evidence_refs=(runs_url,),
        first_failing_boundary=None if status == "PASS" else "GITHUB_EXACT_HEAD_CI",
        typed_failure=typed,
        source_version=sha,
        details={
            "required_workflows": required,
            "missing_workflows": missing,
            "pending_workflows": pending,
            "failed_workflows": failed,
            "observed_runs": len(workflow_runs),
        },
    )


def probe_supabase_receipt(
    probe_id: str,
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    opener: Callable[..., Any] = request.urlopen,
) -> ProbeReceipt:
    observed_at = _utc_now()
    url_env = str(spec.get("url_env") or "SUPABASE_URL")
    key_env = str(spec.get("api_key_env") or "SUPABASE_SERVICE_KEY")
    origin = str(env.get(url_env) or "").strip().rstrip("/")
    key = str(env.get(key_env) or "").strip()
    table = str(spec.get("table") or "").strip()
    if not origin or not table:
        return ProbeReceipt(
            probe_id,
            "supabase_receipt",
            observed_at,
            "UNKNOWN",
            first_failing_boundary="SUPABASE_CONFIGURATION",
            typed_failure="SUPABASE_URL_OR_TABLE_MISSING",
        )
    if not key:
        return ProbeReceipt(
            probe_id,
            "supabase_receipt",
            observed_at,
            "BLOCKED",
            evidence_refs=(f"{origin}/rest/v1/{table}",),
            first_failing_boundary="SUPABASE_AUTHENTICATION",
            typed_failure=f"PROBE_SECRET_MISSING:{key_env}",
        )

    params = {
        "select": str(spec.get("select") or "*"),
        "limit": str(int(spec.get("limit") or 1)),
    }
    order = str(spec.get("order") or "").strip()
    if order:
        params["order"] = order
    for key_name, value in (spec.get("query") or {}).items():
        params[str(key_name)] = str(value)
    url = f"{origin}/rest/v1/{parse.quote(table)}?{parse.urlencode(params, safe='.*(),:')}"
    headers = {
        "Accept": "application/json",
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "User-Agent": "wow-ecosystem-conductor/1.0",
    }
    try:
        code, payload = _open_json(
            url,
            headers,
            opener=opener,
            timeout=float(spec.get("timeout_seconds") or 15),
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeReceipt(
            probe_id,
            "supabase_receipt",
            observed_at,
            "UNKNOWN",
            evidence_refs=(f"{origin}/rest/v1/{table}",),
            first_failing_boundary="SUPABASE_TRANSPORT",
            typed_failure="PROBE_TRANSPORT_UNREACHABLE",
            details={"error_type": type(exc).__name__},
        )
    if not 200 <= code < 300:
        status, typed = _http_failure_status(code)
        return ProbeReceipt(
            probe_id,
            "supabase_receipt",
            observed_at,
            status,
            evidence_refs=(f"{origin}/rest/v1/{table}",),
            first_failing_boundary="SUPABASE_REST",
            typed_failure=typed,
            details={"http_status": code},
        )
    if not isinstance(payload, list) or not payload:
        return ProbeReceipt(
            probe_id,
            "supabase_receipt",
            observed_at,
            "UNKNOWN",
            evidence_refs=(f"{origin}/rest/v1/{table}",),
            first_failing_boundary="SUPABASE_DURABLE_RECEIPT",
            typed_failure="DURABLE_RECEIPT_NOT_FOUND",
            details={"row_count": 0},
        )

    row = payload[0]
    ok, failures = _matches_predicates(row, spec.get("predicates") or {})
    return ProbeReceipt(
        probe_id,
        "supabase_receipt",
        observed_at,
        "PASS" if ok else "FAIL",
        evidence_refs=(f"{origin}/rest/v1/{table}",),
        first_failing_boundary=None if ok else "SUPABASE_RECEIPT_CONTRACT",
        typed_failure=None if ok else "DURABLE_RECEIPT_CONTRACT_MISMATCH",
        source_version=str(_dot_get(row, str(spec.get("source_version_path") or "")) or "") or None,
        deployed_sha=str(_dot_get(row, str(spec.get("deployed_sha_path") or "")) or "") or None,
        details={"row_count": len(payload), "predicate_failures": failures},
    )


def probe_receipt_file(
    probe_id: str,
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    read_text: Callable[[str], str] | None = None,
) -> ProbeReceipt:
    observed_at = _utc_now()
    path_env = str(spec.get("path_env") or "").strip()
    path_value = str(env.get(path_env) or "").strip() if path_env else ""
    path_value = path_value or str(spec.get("path") or "").strip()
    if not path_value:
        return ProbeReceipt(
            probe_id,
            "receipt_file",
            observed_at,
            "UNKNOWN",
            first_failing_boundary="RECEIPT_PATH",
            typed_failure=f"PROBE_RECEIPT_PATH_MISSING:{path_env or probe_id}",
        )
    reader = read_text or (lambda value: Path(value).read_text())
    try:
        payload = json.loads(reader(path_value))
    except FileNotFoundError:
        return ProbeReceipt(
            probe_id,
            "receipt_file",
            observed_at,
            "UNKNOWN",
            evidence_refs=(f"file://{path_value}",),
            first_failing_boundary="RECEIPT_FILE",
            typed_failure="PROBE_RECEIPT_NOT_FOUND",
        )
    except (OSError, json.JSONDecodeError) as exc:
        return ProbeReceipt(
            probe_id,
            "receipt_file",
            observed_at,
            "FAIL",
            evidence_refs=(f"file://{path_value}",),
            first_failing_boundary="RECEIPT_FILE",
            typed_failure="PROBE_RECEIPT_INVALID",
            details={"error_type": type(exc).__name__},
        )

    ok, failures = _matches_predicates(payload, spec.get("predicates") or {})
    status_path = str(spec.get("status_path") or "").strip()
    explicit = _status(_dot_get(payload, status_path)) if status_path else "PASS"
    status = explicit if ok else "FAIL"
    return ProbeReceipt(
        probe_id,
        "receipt_file",
        observed_at,
        status,
        evidence_refs=(f"file://{path_value}",),
        first_failing_boundary=None if status == "PASS" else "RECEIPT_CONTRACT",
        typed_failure=None if status == "PASS" else "PROBE_RECEIPT_CONTRACT_MISMATCH",
        source_version=str(_dot_get(payload, str(spec.get("source_version_path") or "")) or "") or None,
        deployed_sha=str(_dot_get(payload, str(spec.get("deployed_sha_path") or "")) or "") or None,
        details={"predicate_failures": failures, "payload": payload},
    )


def run_probe(
    probe_id: str,
    spec: Mapping[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    opener: Callable[..., Any] = request.urlopen,
    read_text: Callable[[str], str] | None = None,
) -> ProbeReceipt:
    probe_type = str(spec.get("type") or "").strip()
    if probe_type == "http_json":
        return probe_http_json(probe_id, spec, env=env, opener=opener)
    if probe_type == "render_runtime":
        return probe_render_runtime(probe_id, spec, env=env, opener=opener)
    if probe_type == "github_exact_head":
        return probe_github_exact_head(probe_id, spec, env=env, opener=opener)
    if probe_type == "supabase_receipt":
        return probe_supabase_receipt(probe_id, spec, env=env, opener=opener)
    if probe_type == "receipt_file":
        return probe_receipt_file(probe_id, spec, env=env, read_text=read_text)
    return ProbeReceipt(
        probe_id,
        probe_type or "unknown",
        _utc_now(),
        "UNKNOWN",
        first_failing_boundary="PROBE_DISPATCH",
        typed_failure=f"UNSUPPORTED_PROBE_TYPE:{probe_type or 'MISSING'}",
    )


def collect_probes(
    config: Mapping[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    opener: Callable[..., Any] = request.urlopen,
    read_text: Callable[[str], str] | None = None,
) -> dict[str, ProbeReceipt]:
    specs = config.get("probes")
    if not isinstance(specs, Mapping):
        return {}
    return {
        str(probe_id): run_probe(
            str(probe_id),
            spec if isinstance(spec, Mapping) else {},
            env=env,
            opener=opener,
            read_text=read_text,
        )
        for probe_id, spec in specs.items()
    }


def _binding_status(
    probe_ids: list[str],
    receipts: Mapping[str, ProbeReceipt],
) -> tuple[str, list[str]]:
    statuses: list[str] = []
    missing: list[str] = []
    for probe_id in probe_ids:
        receipt = receipts.get(probe_id)
        if receipt is None:
            statuses.append("UNKNOWN")
            missing.append(probe_id)
        else:
            statuses.append(receipt.status)
    return _aggregate_status(statuses), missing


def validate_probe_config(
    registry: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    probes = config.get("probes")
    if not isinstance(probes, Mapping) or not probes:
        return ["probes must be a non-empty object"]
    probe_ids = set(map(str, probes))
    for binding_key, registry_key in (
        ("handoff_bindings", "handoffs"),
        ("component_bindings", "components"),
    ):
        bindings = config.get(binding_key)
        if not isinstance(bindings, Mapping):
            errors.append(f"{binding_key} must be an object")
            continue
        missing = set(registry.get(registry_key, {})) - set(bindings)
        if missing:
            errors.append(f"{binding_key} missing entries: {sorted(missing)}")
        for target, ids in bindings.items():
            if not isinstance(ids, list) or not ids:
                errors.append(f"{binding_key}.{target} must be a non-empty list")
                continue
            unknown = set(map(str, ids)) - probe_ids
            if unknown:
                errors.append(
                    f"{binding_key}.{target} references unknown probes: {sorted(unknown)}"
                )
    return errors


def build_observed_state(
    registry: Mapping[str, Any],
    config: Mapping[str, Any],
    receipts: Mapping[str, ProbeReceipt],
) -> dict[str, Any]:
    components: dict[str, str] = {}
    for component, probe_ids in (config.get("component_bindings") or {}).items():
        components[str(component)] = _binding_status(
            [str(item) for item in probe_ids], receipts
        )[0]

    handoffs: dict[str, str] = {}
    for handoff, probe_ids in (config.get("handoff_bindings") or {}).items():
        handoffs[str(handoff)] = _binding_status(
            [str(item) for item in probe_ids], receipts
        )[0]

    work_items: list[dict[str, Any]] = []
    work_probe = str(config.get("work_item_probe") or "").strip()
    if work_probe and work_probe in receipts:
        payload = receipts[work_probe].details.get("payload")
        if isinstance(payload, Mapping) and isinstance(payload.get("work_items"), list):
            work_items = list(payload["work_items"])

    invariant_config = config.get("invariants") or {}
    return {
        "components": components,
        "handoffs": handoffs,
        "invariants": {
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
            "specialist_ownership_preserved": bool(
                invariant_config.get("specialist_ownership_preserved", True)
            ),
            "typed_failures_preserved": bool(
                invariant_config.get("typed_failures_preserved", True)
            ),
            "self_verification_detected": bool(
                invariant_config.get("self_verification_detected", False)
            ),
        },
        "work_items": work_items,
    }


def evaluate_live(
    registry: dict[str, Any],
    config: dict[str, Any],
    *,
    env: Mapping[str, str] = os.environ,
    opener: Callable[..., Any] = request.urlopen,
    read_text: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    config_errors = validate_probe_config(registry, config)
    if config_errors:
        return {
            "ecosystem_status": "SAFE_HOLD",
            "safe_hold_required": True,
            "reason": "LIVE_PROBE_CONFIG_INVALID",
            "config_errors": config_errors,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }

    receipts = collect_probes(
        config,
        env=env,
        opener=opener,
        read_text=read_text,
    )
    observed = build_observed_state(registry, config, receipts)
    evaluation = ecosystem_conductor.evaluate_ecosystem(registry, observed)

    failed_probes = [
        receipt.to_dict()
        for receipt in receipts.values()
        if receipt.status != "PASS"
    ]
    return {
        "probe_results": {key: value.to_dict() for key, value in receipts.items()},
        "probe_failures": failed_probes,
        "observed_state": observed,
        "evaluation": evaluation,
        "ecosystem_status": evaluation["ecosystem_status"],
        "safe_hold_required": evaluation["safe_hold_required"],
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _load_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WOW ecosystem live read-only probes")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    result = evaluate_live(_load_json(args.registry), _load_json(args.config))
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n")
    print(rendered)
    return 0 if result["ecosystem_status"] != "SAFE_HOLD" else 2


if __name__ == "__main__":
    raise SystemExit(main())
