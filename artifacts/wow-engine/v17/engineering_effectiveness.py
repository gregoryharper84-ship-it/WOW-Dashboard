"""Deterministic engineering-effectiveness contracts for WOW V17.

This module governs engineering workflow/acceptance behavior only. It never
produces sporting probability, never changes model authority, and never grants
execution permission.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

EFFECTIVENESS_VERSION = "1.0"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
HOST_IDENTITY = "WOW_BETTING_ENGINE"

BASE_REGRESSION = "BASE_REGRESSION"
WORKFLOW_HANDOFF_ACCEPTANCE = "WORKFLOW_HANDOFF_ACCEPTANCE"
FULL_SLATE_PRODUCTION_ACCEPTANCE = "FULL_SLATE_PRODUCTION_ACCEPTANCE"
EXACT_SHA_RENDER_VERIFICATION = "EXACT_SHA_RENDER_VERIFICATION"
GOLDEN_BACKEND_ACCEPTANCE = "GOLDEN_BACKEND_ACCEPTANCE"
GPT_EDITOR_SYNC_ACCEPTANCE = "GPT_EDITOR_SYNC_ACCEPTANCE"
GOLDEN_LIVE_HOST_ACCEPTANCE = "GOLDEN_LIVE_HOST_ACCEPTANCE"
DATABASE_MIGRATION_VERIFICATION = "DATABASE_MIGRATION_VERIFICATION"
PROBABILITY_BEHAVIOR_REVIEW = "PROBABILITY_BEHAVIOR_REVIEW"


_RUNTIME_MARKERS = (
    "api_ncaaf_acceptance.py",
    "/runtime.py",
    "_runtime.py",
    "team_event_bridge",
    "prop_auto_hydration",
    "interactive_pick_parallel",
    "final_refresh",
)
_EDITOR_MARKERS = (
    "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt",
    "WOW_V17_PRIZEPICKS_HOST_CONTRACT",
    "openapi.wow-betting-engine",
    "host_routing.py",
    "build_gpt_editor_sync_packet.py",
    "V17_CUSTOM_GPT_EDITOR_SYNC.md",
    "WOW_BETTING_ENGINE_EDITOR_ATTESTATION.md",
)
_PROBABILITY_MARKERS = (
    "specialist",
    "model.py",
    "models.py",
    "calibrator",
    "calibration",
    "lower_bound",
    "probability",
)


def _norm(path: str) -> str:
    return path.strip().replace("\\", "/")


def classify_change_impact(paths: Iterable[str]) -> dict[str, Any]:
    """Map changed paths to the minimum adjacent acceptance surfaces.

    The classifier intentionally over-requests verification rather than
    inferring that a local regression proves a remote/runtime/editor boundary.
    It does not authorize any production or Class C change.
    """

    normalized = sorted({_norm(path) for path in paths if str(path).strip()})
    gates: set[str] = {BASE_REGRESSION}
    reasons: dict[str, list[str]] = {BASE_REGRESSION: ["all changes require repository regression"]}

    def add(gate: str, path: str, reason: str) -> None:
        gates.add(gate)
        reasons.setdefault(gate, []).append(f"{path}: {reason}")

    for path in normalized:
        lower = path.lower()

        if path.startswith(".github/workflows/") or path.startswith(".github/actions/"):
            add(WORKFLOW_HANDOFF_ACCEPTANCE, path, "automation/handoff surface changed")

        if path.startswith("artifacts/wow-engine/") and any(marker in path for marker in _RUNTIME_MARKERS):
            add(FULL_SLATE_PRODUCTION_ACCEPTANCE, path, "governed runtime/handoff surface changed")
            add(EXACT_SHA_RENDER_VERIFICATION, path, "production runtime artifact may change")
            add(GOLDEN_BACKEND_ACCEPTANCE, path, "backend user-journey boundary may change")

        if any(marker in path for marker in _EDITOR_MARKERS):
            add(GPT_EDITOR_SYNC_ACCEPTANCE, path, "live host/editor contract changed")
            add(GOLDEN_LIVE_HOST_ACCEPTANCE, path, "live ChatGPT host parity must be reproven")

        if path.startswith("migrations/") or "/migrations/" in path:
            add(DATABASE_MIGRATION_VERIFICATION, path, "persistent/runtime database contract changed")
            add(FULL_SLATE_PRODUCTION_ACCEPTANCE, path, "database change can affect governed runtime")

        if path.startswith("artifacts/wow-engine/") and any(marker in lower for marker in _PROBABILITY_MARKERS):
            # Review is required; this classifier never decides that the change is Class C.
            add(PROBABILITY_BEHAVIOR_REVIEW, path, "probability-adjacent code requires explicit behavior classification")

    return {
        "version": EFFECTIVENESS_VERSION,
        "changed_paths": normalized,
        "required_gates": sorted(gates),
        "reasons": {gate: sorted(values) for gate, values in sorted(reasons.items())},
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def evaluate_golden_user_journey(receipt: dict[str, Any]) -> dict[str, Any]:
    """Strictly validate a production golden-user-journey receipt.

    PASS is intentionally impossible to infer from CI, backend health, route
    mounting, or a model registry alone. A real live-host handoff and a valid
    governed row (or explicitly proven empty slate) are required.
    """

    blockers: list[str] = []

    if receipt.get("host_identity") != HOST_IDENTITY:
        blockers.append("FAIL_ACTION_INVOCATION:HOST_IDENTITY_NOT_PROVEN")
    if receipt.get("live_editor_verified") is not True:
        blockers.append("FAIL_ACTION_INVOCATION:LIVE_EDITOR_NOT_VERIFIED")
    if receipt.get("action_invoked") is not True:
        blockers.append("FAIL_ACTION_INVOCATION:ACTION_NOT_INVOKED")
    if receipt.get("inventory_accounted") is not True:
        blockers.append("FAIL_PROP_ACQUISITION:INVENTORY_NOT_ACCOUNTED")
    if receipt.get("source_ingestion_complete") is not True:
        blockers.append("FAIL_SOURCE_INGESTION:SOURCE_NOT_FULLY_RECONCILED")
    if receipt.get("all_rows_reconciled") is not True:
        blockers.append("FAIL_GOVERNED_SCORING:ROW_RECONCILIATION_INCOMPLETE")
    if receipt.get("governed_scoring_accounted") is not True:
        blockers.append("FAIL_GOVERNED_SCORING:SCORING_NOT_ACCOUNTED")
    if receipt.get("response_handoff_complete") is not True:
        blockers.append("FAIL_RESPONSE_HANDOFF:LIVE_HOST_RESULT_NOT_DISPLAYED")
    if receipt.get("terminal_authority") != TERMINAL_AUTHORITY:
        blockers.append("FAIL_GOVERNANCE:TERMINAL_AUTHORITY_MISMATCH")
    if receipt.get("can_execute") is not False:
        blockers.append("FAIL_GOVERNANCE:CAN_EXECUTE_MUST_BE_FALSE")

    raw_governed_rows = receipt.get("valid_governed_row_count", 0)
    try:
        governed_rows = int(raw_governed_rows)
    except (TypeError, ValueError):
        governed_rows = 0
        blockers.append("FAIL_GOVERNED_SCORING:VALID_GOVERNED_ROW_COUNT_INVALID")
    if governed_rows < 0:
        governed_rows = 0
        blockers.append("FAIL_GOVERNED_SCORING:VALID_GOVERNED_ROW_COUNT_INVALID")

    explicit_empty = receipt.get("empty_slate_proven") is True
    if governed_rows < 1 and not explicit_empty:
        blockers.append("FAIL_GOVERNED_SCORING:NO_VALID_GOVERNED_ROW_OR_PROVEN_EMPTY_SLATE")

    # These fields are deliberately ignored as PASS evidence. Keeping them in
    # the receipt is useful diagnostics, but they may never satisfy a blocker.
    _ = receipt.get("ci_success")
    _ = receipt.get("backend_health_success")
    _ = receipt.get("workflow_conclusion")

    status = "PASS" if not blockers else blockers[0].split(":", 1)[0]
    return {
        "status": status,
        "blockers": blockers,
        "valid_governed_row_count": governed_rows,
        "empty_slate_proven": explicit_empty,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def declared_user_journey_status(text: str) -> str:
    """Read the repository's explicit user-journey declaration fail-closed."""

    match = re.search(r"^Status:\s*\*\*(PASS|FAIL[^*]*)\*\*", text, flags=re.MULTILINE | re.IGNORECASE)
    if not match:
        return "UNKNOWN"
    value = match.group(1).strip().upper()
    return "PASS" if value == "PASS" else "FAIL"


def product_health_allows_model_improvement(status: str) -> bool:
    """Discretionary model research is allowed only after product health PASS."""

    return str(status).strip().upper() == "PASS"


def _main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    impact = sub.add_parser("impact")
    impact.add_argument("paths", nargs="*")

    journey = sub.add_parser("journey")
    journey.add_argument("receipt")

    health = sub.add_parser("health")
    health.add_argument("path")

    args = parser.parse_args()
    if args.command == "impact":
        print(json.dumps(classify_change_impact(args.paths), indent=2, sort_keys=True))
        return
    if args.command == "journey":
        payload = json.loads(Path(args.receipt).read_text())
        print(json.dumps(evaluate_golden_user_journey(payload), indent=2, sort_keys=True))
        return

    text = Path(args.path).read_text(encoding="utf-8")
    status = declared_user_journey_status(text)
    print(
        json.dumps(
            {
                "user_journey_status": status,
                "model_improvement_allowed": product_health_allows_model_improvement(status),
                "can_execute": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    _main()
