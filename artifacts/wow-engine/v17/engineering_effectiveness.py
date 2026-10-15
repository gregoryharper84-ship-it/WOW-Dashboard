"""Deterministic engineering-effectiveness contracts for WOW V17.

This module governs engineering workflow/acceptance behavior only. It never
produces sporting probability, never changes model authority, and never grants
execution permission.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
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


# Runtime markers deliberately include historical failure boundaries that caused
# same-day follow-up repairs (#803 and #807/#808). A gate that does not recognize
# those paths would not address the defect class it exists to prevent.
_RUNTIME_MARKERS = (
    "api_ncaaf_acceptance.py",
    "/runtime.py",
    "_runtime.py",
    "bridge",
    "handoff",
    "hydration",
    "multiscout",
    "auto_advance",
    "reconciliation",
    "final_refresh",
    "production_acceptance",
    "rank_fix",
)
_WORKFLOW_FULL_SLATE_MARKERS = (
    "nightly-multiscout",
    "full-slate",
    "scout-persist",
)
_WORKFLOW_RELEASE_MARKERS = (
    "release-production",
    "release-resume",
    "deploy",
    "render",
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
        is_workflow = path.startswith(".github/workflows/") or path.startswith(".github/actions/")

        if is_workflow:
            add(WORKFLOW_HANDOFF_ACCEPTANCE, path, "automation/handoff surface changed")
            if any(marker in lower for marker in _WORKFLOW_FULL_SLATE_MARKERS):
                add(FULL_SLATE_PRODUCTION_ACCEPTANCE, path, "full-slate/Scout orchestration changed")
            if any(marker in lower for marker in _WORKFLOW_RELEASE_MARKERS):
                add(EXACT_SHA_RENDER_VERIFICATION, path, "release/deployment orchestration changed")

        if path.startswith("artifacts/wow-engine/") and any(marker in lower for marker in _RUNTIME_MARKERS):
            add(FULL_SLATE_PRODUCTION_ACCEPTANCE, path, "governed runtime/handoff surface changed")
            add(EXACT_SHA_RENDER_VERIFICATION, path, "production runtime artifact may change")
            add(GOLDEN_BACKEND_ACCEPTANCE, path, "backend user-journey boundary may change")

        if path == "artifacts/wow-engine/v17/engineering_effectiveness.py":
            add(GOLDEN_BACKEND_ACCEPTANCE, path, "GPT Action acceptance validator changed")
            add(GOLDEN_LIVE_HOST_ACCEPTANCE, path, "real host result evidence must be reproven")
            add(EXACT_SHA_RENDER_VERIFICATION, path, "acceptance runtime SHA must match production")

        if any(marker.lower() in lower for marker in _EDITOR_MARKERS):
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


def evaluate_golden_user_journey(receipt: Any) -> dict[str, Any]:
    """Fail closed on actual Action, source, persistence and displayed receipt proof.

    Boolean stage summaries alone are NOT a production acceptance certificate.
    Even complete-shaped receipts can only produce STRUCTURAL_PASS_ONLY; the
    trusted production-host verifier must independently authenticate their
    provenance before the product can be labeled healthy.
    Proof below must originate from independently checked production evidence;
    this function validates its structure and consistency, not its issuer's
    credentials. Only authorized QA can attest that external origin.
    """
    if not isinstance(receipt, dict):
        return {
            "status": "FAIL_ACTION_INVOCATION",
            "blockers": ["FAIL_ACTION_INVOCATION:NON_OBJECT_RECEIPT"],
            "evidence_scope": "STRUCTURAL_ONLY",
            "production_verified": False,
            "independent_qa_verified": False,
            "valid_governed_row_count": 0,
            "empty_slate_proven": False,
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }
    blockers: list[str] = []
    operation = "scoreWowPickRequest"

    def nonempty(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def whole(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if 0 <= value < 10**18 else None
        # Bound parsing explicitly: Python 3.11+ raises ValueError on very
        # large integer literals, which cannot become an untyped 500 response.
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,18}", value):
            return int(value)
        return None

    def offset_timestamp(value: Any) -> datetime | None:
        if not nonempty(value):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None

    def typed(key: str, category: str) -> None:
        code = receipt.get(key)
        if code is None or code == "":
            return
        if not nonempty(code) or not re.fullmatch(r"[A-Z][A-Z0-9_]*(?::[A-Z0-9_-]+)?", code):
            blockers.append(f"{category}:FAILURE_CODE_MALFORMED")
            return
        blockers.append(f"{category}:{code}")
        blockers.append(code)

    if receipt.get("host_identity") != HOST_IDENTITY:
        blockers.append("FAIL_ACTION_INVOCATION:HOST_IDENTITY_NOT_PROVEN")
    if receipt.get("live_editor_verified") is not True:
        blockers.append("FAIL_ACTION_INVOCATION:LIVE_EDITOR_NOT_VERIFIED")
    if receipt.get("action_invoked") is not True:
        blockers.append("FAIL_ACTION_INVOCATION:ACTION_NOT_INVOKED")
    if receipt.get("action_operation_id") != operation:
        blockers.append("FAIL_ACTION_INVOCATION:CANONICAL_OPERATION_MISMATCH")
    if receipt.get("verification_skipped") is not False:
        blockers.append("FAIL_ACTION_INVOCATION:VERIFICATION_SKIPPED")
    if receipt.get("action_receipt_status") not in (None, "PASS", "MODEL_QUALIFIED", "FINAL_APPROVED"):
        blockers.append("FAIL_ACTION_INVOCATION:ACTION_RECEIPT_INVALID")

    action = receipt.get("action_receipt")
    request_id = receipt.get("request_id")
    if not isinstance(action, dict):
        blockers.append("FAIL_ACTION_INVOCATION:ACTION_RECEIPT_MISSING")
    else:
        if not nonempty(request_id) or action.get("request_id") != request_id:
            blockers.append("FAIL_ACTION_INVOCATION:ACTION_REQUEST_ID_MISMATCH")
        if action.get("action_operation_id") != operation:
            blockers.append("FAIL_ACTION_INVOCATION:ACTION_RECEIPT_OPERATION_MISMATCH")
        if not (isinstance(action.get("immutable_receipt_hash"), str) and
                re.fullmatch(r"[a-f0-9]{64}", action["immutable_receipt_hash"])):
            blockers.append("FAIL_PERSISTENCE:IMMUTABLE_ACTION_RECEIPT_MISSING")
        if action.get("readback_verified") is not True:
            blockers.append("FAIL_PERSISTENCE:ACTION_RECEIPT_READBACK_UNVERIFIED")
        if action.get("freshness_verified") is not True or action.get("stale") is not False:
            blockers.append("FAIL_ACTION_INVOCATION:ACTION_RECEIPT_STALE")
        if action.get("replayed") is not False:
            blockers.append("FAIL_ACTION_INVOCATION:ACTION_RECEIPT_REPLAYED")
        if action.get("can_execute") is not False:
            blockers.append("FAIL_GOVERNANCE:ACTION_RECEIPT_EXECUTION_INVALID")

    if receipt.get("inventory_accounted") is not True:
        blockers.append("FAIL_PROP_ACQUISITION:INVENTORY_NOT_ACCOUNTED")
    source = receipt.get("source_inventory_evidence")
    acquired = whole(receipt.get("acquired_row_count"))
    if acquired is None:
        blockers.append("FAIL_PROP_ACQUISITION:ACQUIRED_COUNT_UNVERIFIED")
    if not isinstance(source, dict) or not (
        nonempty(source.get("source_snapshot_id")) and
        source.get("request_id") == request_id and
        whole(source.get("acquired_row_count")) == acquired and
        type(source.get("acquired_row_count")) is int and
        source.get("source_accounted") is True and
        source.get("freshness_verified") is True
    ):
        blockers.append("FAIL_PROP_ACQUISITION:SOURCE_INVENTORY_EVIDENCE_MISSING")
    # Source observation must precede the invoked Action and the displayed
    # result, and must be correlated to that same request and source snapshot.
    # A separate trusted host/QA check must authenticate issuer and freshness;
    # these structures alone never prove live-host origin.
    invoked_at = offset_timestamp(action.get("invoked_at")) if isinstance(action, dict) else None
    observed_at = offset_timestamp(source.get("source_observed_at")) if isinstance(source, dict) else None
    display = receipt.get("display_receipt")
    displayed_at = offset_timestamp(display.get("displayed_at")) if isinstance(display, dict) else None
    if invoked_at is None or observed_at is None or observed_at > invoked_at:
        blockers.append("FAIL_PROP_ACQUISITION:SOURCE_TIMESTAMP_NOT_ATTESTED")
    # V17's existing cross-sport acceptance policy already bounds source
    # inventory to six hours. The golden *current* host journey may not claim
    # freshness from an old but correctly ordered source/Action pair.
    now = datetime.now(timezone.utc)
    source_age_cap = timedelta(hours=6)
    if observed_at is not None and (
        observed_at > now or now - observed_at > source_age_cap
    ):
        blockers.append("FAIL_PROP_ACQUISITION:SOURCE_STALE_OR_FUTURE")
    if invoked_at is not None and (
        invoked_at > now or now - invoked_at > source_age_cap
    ):
        blockers.append("FAIL_ACTION_INVOCATION:ACTION_STALE_OR_FUTURE")
    if displayed_at is not None and (
        displayed_at > now or now - displayed_at > source_age_cap
    ):
        blockers.append("FAIL_RESPONSE_HANDOFF:DISPLAY_STALE_OR_FUTURE")
    if displayed_at is None or invoked_at is None or displayed_at < invoked_at:
        blockers.append("FAIL_RESPONSE_HANDOFF:DISPLAY_TIMESTAMP_NOT_ATTESTED")
    if isinstance(action, dict) and isinstance(source, dict):
        if action.get("source_snapshot_id") != source.get("source_snapshot_id"):
            blockers.append("FAIL_PROP_ACQUISITION:ACTION_SOURCE_SNAPSHOT_MISMATCH")
    if isinstance(display, dict) and isinstance(action, dict):
        if display.get("immutable_receipt_hash") != action.get("immutable_receipt_hash"):
            blockers.append("FAIL_RESPONSE_HANDOFF:DISPLAY_RECEIPT_HASH_MISMATCH")

    if receipt.get("source_ingestion_complete") is not True:
        blockers.append("FAIL_SOURCE_INGESTION:SOURCE_NOT_FULLY_RECONCILED")
    typed("source_ingestion_failure_code", "FAIL_SOURCE_INGESTION")

    if receipt.get("all_rows_reconciled") is not True:
        blockers.append("FAIL_GOVERNED_SCORING:ROW_RECONCILIATION_INCOMPLETE")
    if receipt.get("governed_scoring_accounted") is not True:
        blockers.append("FAIL_GOVERNED_SCORING:SCORING_NOT_ACCOUNTED")
    typed("scorer_failure_code", "FAIL_GOVERNED_SCORING")
    typed("persisted_receipt_failure_code", "FAIL_PERSISTENCE")

    counts = [whole(receipt.get(k)) for k in
              ("rows_in", "rows_completed", "rows_held", "rows_rejected")]
    if any(n is None for n in counts) or counts[0] != sum(counts[1:]):
        blockers.append("FAIL_GOVERNED_SCORING:ROW_RECONCILIATION_MISMATCH")
    elif acquired is not None and counts[0] != acquired:
        blockers.append("FAIL_GOVERNED_SCORING:ROW_SOURCE_COUNT_MISMATCH")

    if receipt.get("response_handoff_complete") is not True:
        blockers.append("FAIL_RESPONSE_HANDOFF:LIVE_HOST_RESULT_NOT_DISPLAYED")
    typed("response_handoff_failure_code", "FAIL_RESPONSE_HANDOFF")
    displayed = receipt.get("display_receipt")
    if not isinstance(displayed, dict) or not (
        nonempty(request_id) and displayed.get("request_id") == request_id and
        displayed.get("host_identity") == HOST_IDENTITY and
        displayed.get("canonical_result_displayed") is True
    ):
        blockers.append("FAIL_RESPONSE_HANDOFF:DISPLAY_RECEIPT_NOT_VERIFIED")

    if receipt.get("terminal_authority") != TERMINAL_AUTHORITY:
        blockers.append("FAIL_GOVERNANCE:TERMINAL_AUTHORITY_MISMATCH")
    if receipt.get("can_execute") is not False:
        blockers.append("FAIL_GOVERNANCE:CAN_EXECUTE_MUST_BE_FALSE")
    if receipt.get("llm_substitution") is not False or receipt.get("probability_source") in (
        "SPORTSBOOK_IMPLIED", "LLM_ESTIMATE", "EXTERNAL_PREDICTION", "HIT_RATE"
    ):
        blockers.append("FAIL_GOVERNANCE:UNFITTED_PROBABILITY_SUBSTITUTION")

    governed_rows = whole(receipt.get("valid_governed_row_count"))
    if governed_rows is None:
        governed_rows = 0
        blockers.append("FAIL_GOVERNED_SCORING:VALID_GOVERNED_ROW_COUNT_INVALID")
    if type(receipt.get("empty_slate_proven")) is not bool:
        blockers.append("FAIL_PROP_ACQUISITION:EMPTY_SLATE_ATTESTATION_INVALID")
    explicit_empty = receipt.get("empty_slate_proven") is True
    if counts[1] is not None and governed_rows > counts[1]:
        blockers.append("FAIL_GOVERNED_SCORING:GOVERNED_COUNT_EXCEEDS_COMPLETED")
    if explicit_empty:
        if governed_rows != 0:
            blockers.append("FAIL_PROP_ACQUISITION:EMPTY_SLATE_INCONSISTENT")
        if (acquired != 0 or not isinstance(source, dict) or
                source.get("empty_slate_verified") is not True or
                counts[0] != 0):
            blockers.append("FAIL_PROP_ACQUISITION:EMPTY_SLATE_SOURCE_NOT_PROVEN")
    else:
        if governed_rows < 1:
            blockers.append("FAIL_GOVERNED_SCORING:NO_VALID_GOVERNED_ROW_OR_PROVEN_EMPTY_SLATE")
        if receipt.get("probability_source") != "GOVERNED_FITTED_SPECIALIST":
            blockers.append("FAIL_GOVERNANCE:FITTED_SPECIALIST_PROVENANCE_NOT_VERIFIED")
        rows = receipt.get("governed_row_receipts")
        if not isinstance(rows, list) or len(rows) != governed_rows:
            blockers.append("FAIL_GOVERNED_SCORING:GOVERNED_ROW_RECEIPTS_MISSING")
        else:
            for row in rows:
                if not isinstance(row, dict):
                    blockers.append("FAIL_GOVERNED_SCORING:GOVERNED_ROW_RECEIPT_INVALID")
                    continue
                p = row.get("calibrated_probability")
                lb = row.get("calibrated_probability_lower_bound")
                valid_numbers = (
                    type(p) in (float, int) and type(lb) in (float, int) and
                    # Bound comparisons reject NaN, infinities and huge
                    # integers without coercing an unbounded int to float.
                    0 < lb <= p < 1
                )
                if not (
                    nonempty(row.get("prediction_id")) and
                    nonempty(row.get("controlling_specialist")) and
                    isinstance(row.get("model_artifact_checksum"), str) and
                    re.fullmatch(r"sha256:[0-9a-f]{64}", row["model_artifact_checksum"]) is not None and
                    row.get("terminal_authority") == TERMINAL_AUTHORITY and
                    row.get("calibration_status") == "PASS" and
                    row.get("probability_publishable") is True and
                    row.get("can_execute") is False and
                    valid_numbers
                ):
                    blockers.append("FAIL_GOVERNED_SCORING:GOVERNED_ROW_RECEIPT_INVALID")

    # CI, backend HTTP-200 and workflow success are never PASS evidence.
    # The first typed boundary remains the reported status.
    blockers = list(dict.fromkeys(blockers))
    return {
        "status": "STRUCTURAL_PASS_ONLY" if not blockers else (
            blockers[0].split(":", 1)[0]
            if blockers[0].startswith("FAIL_") else
            "FAIL_SOURCE_INGESTION" if blockers[0].startswith("SOURCE_PAGE_") else
            "FAIL_GOVERNED_SCORING"
        ),
        "blockers": blockers,
        # Structural validation alone cannot authenticate an external issuer,
        # production GPT editor, real scoring request, or independent QA.
        # A live production acceptance authority must verify these separately.
        "evidence_scope": "STRUCTURAL_ONLY",
        "production_verified": False,
        "independent_qa_verified": False,
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
