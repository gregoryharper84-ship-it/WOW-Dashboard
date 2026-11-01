"""Provider-failover classifier and deterministic survival receipts for WOW V17.

This module is control-plane only. It never produces sporting probabilities and
never grants wager or market execution authority.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping

try:
    from .engineering_agent_team import load_ledger, select_priority_incident
except ImportError:  # direct-script execution
    from engineering_agent_team import load_ledger, select_priority_incident

OPENAI_FAILURE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "OPENAI_API_BILLING_REQUIRED",
        ("http 402", "status 402", "payment required", "payment_required"),
    ),
    (
        "OPENAI_API_QUOTA_EXCEEDED",
        (
            "quota exceeded",
            "insufficient_quota",
            "check your plan and billing details",
        ),
    ),
    (
        "OPENAI_API_KEY_MISSING",
        (
            "openai_api_key is required",
            "openai api key is required",
        ),
    ),
    (
        "OPENAI_API_AUTH_FAILED",
        (
            "invalid_api_key",
            "incorrect api key",
            "invalid authentication",
            "authentication failed",
        ),
    ),
    (
        "OPENAI_API_RATE_LIMITED",
        (
            "rate limit exceeded",
            "rate_limit_exceeded",
            "too many requests",
            "http 429",
            "status 429",
        ),
    ),
    (
        "OPENAI_API_UPSTREAM_UNAVAILABLE",
        (
            "openai upstream unavailable",
            "responses api unavailable",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        ),
    ),
)

ANTHROPIC_FAILURE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ANTHROPIC_API_BILLING_REQUIRED",
        ("http 402", "status 402", "payment required", "payment_required"),
    ),
    (
        "ANTHROPIC_API_CREDIT_EXHAUSTED",
        ("credit balance", "billing error", "spend limit reached"),
    ),
    (
        "ANTHROPIC_API_AUTH_FAILED",
        ("authentication_error", "invalid x-api-key", "invalid api key"),
    ),
    (
        "ANTHROPIC_API_RATE_LIMITED",
        ("rate_limit_error", "rate limit"),
    ),
    (
        "ANTHROPIC_API_UPSTREAM_UNAVAILABLE",
        ("overloaded_error", "api_error", "service unavailable"),
    ),
)

OPENAI_FAILOVER_ELIGIBLE = {
    "OPENAI_API_BILLING_REQUIRED",
    "OPENAI_API_QUOTA_EXCEEDED",
    "OPENAI_API_KEY_MISSING",
    "OPENAI_API_AUTH_FAILED",
    "OPENAI_API_RATE_LIMITED",
    "OPENAI_API_UPSTREAM_UNAVAILABLE",
}

# Typed worker delivery outcomes (incidents #1021/#1527). Matched only on the emitted
# workflow error command, never on the step's own script text echoed into the log.
DELIVERY_ERROR_RE = re.compile(r"(?:::error::|##\[error\])(ACTIONABLE_REPAIR_[A-Z_]+):")
GOVERNANCE_BLOCKED_CODES = frozenset({"ACTIONABLE_REPAIR_POLICY_BOUNDARY"})

NON_PROVIDER_MARKERS = (
    "eacces:",
    "permission denied",
    "no such file or directory",
    "syntaxerror",
    "assertionerror",
    "pytest",
    "yaml",
)


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    code: str
    failover_eligible: bool
    circuit_breaker_minutes: int
    can_execute: bool = False
    disposition: str | None = None
    provider_signal: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "failover_eligible": self.failover_eligible,
            "circuit_breaker_minutes": self.circuit_breaker_minutes,
            "disposition": self.disposition,
            "provider_signal": self.provider_signal,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }


def classify_provider_failure(provider: str, log_text: str) -> ProviderFailure:
    provider_key = str(provider or "").strip().lower()
    text = str(log_text or "").lower()

    patterns = (
        OPENAI_FAILURE_PATTERNS
        if provider_key == "openai"
        else ANTHROPIC_FAILURE_PATTERNS
        if provider_key in {"anthropic", "claude"}
        else ()
    )
    provider_code = next(
        (code for code, needles in patterns if any(needle in text for needle in needles)),
        None,
    )
    delivery = DELIVERY_ERROR_RE.search(str(log_text or ""))
    if delivery:
        # A typed delivery outcome is never relabelled as a provider failure. A governance
        # policy boundary never fails over or retries, whatever else the log contains.
        delivery_code = delivery.group(1)
        blocked = delivery_code in GOVERNANCE_BLOCKED_CODES
        eligible = not blocked and provider_code in OPENAI_FAILOVER_ELIGIBLE
        return ProviderFailure(
            provider=provider_key or "unknown",
            code=delivery_code,
            failover_eligible=eligible,
            circuit_breaker_minutes=120 if eligible else 0,
            disposition="BLOCKED_WITH_EXACT_REASON" if blocked else "UNRESOLVED_TYPED_FAILURE",
            provider_signal=provider_code,
        )

    for code, needles in patterns:
        if any(needle in text for needle in needles):
            eligible = code in OPENAI_FAILOVER_ELIGIBLE
            cooldown = 120 if eligible else 0
            return ProviderFailure(
                provider=provider_key or "unknown",
                code=code,
                failover_eligible=eligible,
                circuit_breaker_minutes=cooldown,
            )

    if any(marker in text for marker in NON_PROVIDER_MARKERS):
        return ProviderFailure(
            provider=provider_key or "unknown",
            code="NON_PROVIDER_FAILURE",
            failover_eligible=False,
            circuit_breaker_minutes=0,
        )

    return ProviderFailure(
        provider=provider_key or "unknown",
        code="UNCLASSIFIED_PROVIDER_OR_WORKFLOW_FAILURE",
        failover_eligible=False,
        circuit_breaker_minutes=0,
    )


def verify_gemini_worker_provenance(
    run: Any,
    artifacts: Any,
    *,
    expected_run_id: str,
    expected_run_attempt: str,
    repository: str,
) -> dict[str, Any]:
    """Validate GitHub's run and artifact metadata before downloading its receipt."""
    def held(code: str) -> dict[str, Any]:
        return {
            "status": "HELD",
            "code": code,
            "fallback_eligible": False,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }

    if not isinstance(run, dict) or not isinstance(artifacts, dict):
        return held("GEMINI_CHILD_EVIDENCE_INVALID")
    try:
        run_id = int(expected_run_id)
        run_attempt = int(expected_run_attempt)
    except (TypeError, ValueError):
        return held("GEMINI_CHILD_SOURCE_ID_INVALID")
    if (
        run_id <= 0
        or run_attempt <= 0
        or not isinstance(run.get("id"), int)
        or isinstance(run.get("id"), bool)
        or run.get("id") != run_id
    ):
        return held("GEMINI_CHILD_SOURCE_ID_MISMATCH")

    source_repo = run.get("repository")
    head_repo = run.get("head_repository")
    source_sha = run.get("head_sha")
    if (
        run.get("event") != "workflow_dispatch"
        or run.get("path") != GEMINI_WORKER_WORKFLOW_PATH
        or not isinstance(run.get("run_attempt"), int)
        or isinstance(run.get("run_attempt"), bool)
        or run.get("run_attempt") != run_attempt
        or run.get("status") != "completed"
        or run.get("conclusion") not in {"success", "failure"}
        or run.get("head_branch") != "main"
        or not isinstance(source_sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
        or not isinstance(source_repo, dict)
        or source_repo.get("full_name") != repository
        or not isinstance(source_repo.get("id"), int)
        or isinstance(source_repo.get("id"), bool)
        or not isinstance(head_repo, dict)
        or head_repo.get("full_name") != repository
        or not isinstance(head_repo.get("id"), int)
        or isinstance(head_repo.get("id"), bool)
        or head_repo.get("id") != source_repo.get("id")
    ):
        return held("GEMINI_CHILD_SOURCE_PROVENANCE_MISMATCH")

    artifact_rows = artifacts.get("artifacts")
    total_count = artifacts.get("total_count")
    if (
        not isinstance(artifact_rows, list)
        or not isinstance(total_count, int)
        or isinstance(total_count, bool)
        or total_count < 0
        or total_count > 100
        or total_count != len(artifact_rows)
    ):
        return held("GEMINI_CHILD_ARTIFACT_INVENTORY_UNVERIFIED")

    artifact_prefix = f"wow-gemini-candidate-{run_id}-"
    attempt_pattern = re.compile(
        rf"^{re.escape(artifact_prefix)}([1-9][0-9]*)$"
    )
    candidate_artifacts = []
    seen_attempts = set()
    seen_ids = set()
    for row in artifact_rows:
        if not isinstance(row, dict):
            return held("GEMINI_CHILD_ARTIFACT_INVENTORY_UNVERIFIED")
        name = row.get("name")
        if not isinstance(name, str) or not name.startswith(artifact_prefix):
            continue
        match = attempt_pattern.fullmatch(name)
        if not match:
            return held("GEMINI_CHILD_ARTIFACT_PROVENANCE_MISMATCH")
        artifact_attempt = int(match.group(1))
        artifact_run = row.get("workflow_run")
        artifact_id = row.get("id")
        if (
            artifact_attempt > run_attempt
            or artifact_attempt in seen_attempts
            or not isinstance(artifact_id, int)
            or isinstance(artifact_id, bool)
            or artifact_id <= 0
            or artifact_id in seen_ids
            or not isinstance(row.get("digest"), str)
            or not _SHA256.fullmatch(row["digest"])
            or not isinstance(artifact_run, dict)
            or not isinstance(artifact_run.get("id"), int)
            or isinstance(artifact_run.get("id"), bool)
            or artifact_run.get("id") != run_id
            or not isinstance(artifact_run.get("repository_id"), int)
            or isinstance(artifact_run.get("repository_id"), bool)
            or artifact_run.get("repository_id") != source_repo["id"]
            or not isinstance(artifact_run.get("head_repository_id"), int)
            or isinstance(artifact_run.get("head_repository_id"), bool)
            or artifact_run.get("head_repository_id") != source_repo["id"]
            or artifact_run.get("head_branch") != "main"
            or artifact_run.get("head_sha") != source_sha
        ):
            return held("GEMINI_CHILD_ARTIFACT_PROVENANCE_MISMATCH")
        seen_attempts.add(artifact_attempt)
        seen_ids.add(artifact_id)
        candidate_artifacts.append((artifact_attempt, row))

    matching_artifacts = [
        row for artifact_attempt, row in candidate_artifacts
        if artifact_attempt == run_attempt
    ]
    if not matching_artifacts:
        return held("GEMINI_CHILD_ARTIFACT_MISSING")
    if len(matching_artifacts) != 1:
        return held("GEMINI_CHILD_ARTIFACT_MISSING_OR_AMBIGUOUS")
    artifact = matching_artifacts[0]
    expected_name = f"wow-gemini-candidate-{run_id}-{run_attempt}"
    if (
        artifact.get("name") != expected_name
        or artifact.get("expired") is not False
    ):
        return held("GEMINI_CHILD_ARTIFACT_PROVENANCE_MISMATCH")

    return {
        "status": "VERIFIED_PROVENANCE",
        "code": "GEMINI_CHILD_PROVENANCE_VERIFIED",
        "source_run_id": str(run_id),
        "source_run_attempt": run_attempt,
        "source_head_sha": source_sha,
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
        "artifact_name": expected_name,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def verify_gemini_worker_completion(
    run: Any,
    artifacts: Any,
    receipts: Any,
    *,
    expected_run_id: str,
    expected_run_attempt: str,
    repository: str,
    manifest: Any,
    issues: Any,
) -> dict[str, Any]:
    """Accept only the typed result bound to this completed GitHub worker run."""
    def held(code: str) -> dict[str, Any]:
        return {
            "status": "HELD",
            "code": code,
            "fallback_eligible": False,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }

    provenance = verify_gemini_worker_provenance(
        run, artifacts, expected_run_id=expected_run_id,
        expected_run_attempt=expected_run_attempt, repository=repository,
    )
    if provenance.get("status") != "VERIFIED_PROVENANCE":
        return provenance
    if not isinstance(receipts, dict):
        return {
            "status": "HELD",
            "code": "GEMINI_CHILD_EVIDENCE_INVALID",
            "fallback_eligible": False,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        }
    run_id = int(expected_run_id)
    source_sha = provenance["source_head_sha"]
    required = ("admission.json", "selection.json", "workspace.json", "model-receipt.json")
    if any(not isinstance(receipts.get(name), dict) for name in required):
        return held("GEMINI_CHILD_ARTIFACT_RECEIPT_MISSING")
    admission, selection, workspace, model = (receipts[name] for name in required)
    if (
        admission.get("status") != "ADMITTED"
        or admission.get("code") != "ADMITTED"
        or admission.get("provider") != "gemini"
        or selection.get("status") != "SELECTED"
        or selection.get("code") != "EXISTING_CONDUCTOR_RESEARCH_ONLY"
        or selection.get("writer_lease_granted") is not False
        or selection.get("github_branch_write_allowed") is not False
        or workspace.get("status") != "TRUSTED"
        or workspace.get("code") != "GEMINI_WORKSPACE_TRUSTED"
    ):
        return held("GEMINI_CHILD_ADMISSION_OR_SELECTION_INVALID")
    for receipt in (admission, selection, workspace):
        if (
            receipt.get("can_execute") is not False
            or receipt.get("terminal_authority") != "V17_TERMINAL_REDUCER"
        ):
            return held("GEMINI_CHILD_SAFETY_RECEIPT_INVALID")

    issue_number = selection.get("issue_number")
    lease_group = selection.get("lease_group")
    if (
        not isinstance(issue_number, int)
        or isinstance(issue_number, bool)
        or issue_number <= 0
        or not isinstance(lease_group, str)
        or not lease_group
        or selection.get("incident_id") != str(issue_number)
    ):
        return held("GEMINI_CHILD_SELECTION_IDENTITY_INVALID")
    try:
        from .engineering_gemini_headless import select_incident
    except ImportError:  # direct-script execution
        from engineering_gemini_headless import select_incident
    try:
        current_selection = select_incident(
            manifest,
            issues,
            target_incident=str(issue_number),
            requested_lease=lease_group,
        )
    except (KeyError, TypeError, ValueError):
        return held("GEMINI_CHILD_CANONICAL_SELECTION_INVALID")
    if current_selection.get("status") != "SELECTED":
        return held("GEMINI_CHILD_INCIDENT_NOT_CURRENTLY_OPEN")
    for field in ("incident_id", "issue_number", "lease_group", "severity", "execution_lane"):
        if current_selection.get(field) != selection.get(field):
            return held("GEMINI_CHILD_CANONICAL_SELECTION_MISMATCH")

    if (
        model.get("source_head_sha") != source_sha
        or model.get("retry_allowed") is not False
        or model.get("can_execute") is not False
        or model.get("terminal_authority") != "V17_TERMINAL_REDUCER"
    ):
        return held("GEMINI_CHILD_MODEL_RECEIPT_PROVENANCE_MISMATCH")

    common = {
        "source_run_id": provenance["source_run_id"],
        "source_run_attempt": provenance["source_run_attempt"],
        "source_head_sha": source_sha,
        "artifact_id": provenance["artifact_id"],
        "artifact_digest": provenance["artifact_digest"],
        "target_incident": str(issue_number),
        "issue_number": issue_number,
        "lease_group": lease_group,
        "fallback_eligible": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
    if run["conclusion"] == "failure":
        if (
            model.get("status") == "HELD"
            and model.get("code") == "GEMINI_API_UPSTREAM_UNAVAILABLE"
            and model.get("exit_code") == 247
        ):
            return {
                **common,
                "status": "VERIFIED_PROVIDER_FAILURE",
                "code": "GEMINI_API_UPSTREAM_UNAVAILABLE",
                "fallback_eligible": True,
            }
        return held("GEMINI_CHILD_FAILURE_NOT_PROVIDER_RECOVERY")

    patch = receipts.get("patch-inspection.json")
    regression = receipts.get("regression-receipt.json")
    files = receipts.get("files.json")
    if (
        model.get("status") == "COMPLETED"
        and model.get("code") == "GEMINI_CLI_COMPLETE"
        and model.get("exit_code") == 0
        and isinstance(patch, dict)
        and patch.get("status") == "CANDIDATE_ONLY"
        and patch.get("can_execute") is False
        and isinstance(regression, dict)
        and regression.get("status") == "TESTED_CANDIDATE"
        and regression.get("code") == "GEMINI_REGRESSION_PASS"
        and regression.get("exit_code") == 0
        and regression.get("can_execute") is False
        and isinstance(files, list)
        and bool(files)
        and isinstance(receipts.get("candidate.patch"), str)
        and bool(receipts["candidate.patch"].strip())
    ):
        return {
            **common,
            "status": "VERIFIED_TESTED_CANDIDATE",
            "code": "GEMINI_TESTED_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW",
        }
    return held("GEMINI_CHILD_SUCCESS_RECEIPT_INVALID")


# Billing and credential failures cannot heal merely because 120 minutes elapsed.
# The last completed successful primary run clears this state; explicit force_provider
# remains the operator-controlled recovery probe, not an automatic retry loop.
OPENAI_PERSISTENT_BREAKER_CODES = frozenset({
    "OPENAI_API_BILLING_REQUIRED",
    "OPENAI_API_QUOTA_EXCEEDED",
    "OPENAI_API_KEY_MISSING",
    "OPENAI_API_AUTH_FAILED",
})

GEMINI_WORKER_WORKFLOW_PATH = ".github/workflows/wow-v17-gemini-engineering-worker.yml"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def circuit_breaker_active(
    failure_code: str,
    *,
    age_minutes: float,
    cooldown_minutes: int = 120,
) -> bool:
    return (
        failure_code in OPENAI_FAILOVER_ELIGIBLE
        and age_minutes >= 0
        and (
            failure_code in OPENAI_PERSISTENT_BREAKER_CODES
            or age_minutes < cooldown_minutes
        )
    )


def resident_provider_admission(route: dict[str, Any], *, updated_at: str, now: datetime) -> str:
    """Consume protected dispatcher's receipt; elapsed time never proves recovery."""
    if (route.get("can_execute") is not False
            or route.get("terminal_authority") != "V17_TERMINAL_REDUCER"
            or route.get("action") not in {"DISPATCH", "SURVIVAL", "NO_ACTION"}
            or not isinstance(route.get("reason"), str)):
        return "PROVIDER_STATUS_UNVERIFIED"
    try:
        stamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age = (now - stamp).total_seconds() / 60
    except (ValueError, TypeError, AttributeError):
        return "PROVIDER_STATUS_UNVERIFIED"
    if age < 0:
        return "PROVIDER_STATUS_UNVERIFIED"
    reason = route["reason"]
    if route["action"] == "SURVIVAL" and route.get("provider") == "deterministic":
        if any(code in reason.split(":") for code in OPENAI_PERSISTENT_BREAKER_CODES):
            return "PROVIDER_QUOTA_BLOCKED"
        if any(code in reason.split(":") for code in OPENAI_FAILOVER_ELIGIBLE):
            return "PROVIDER_THROTTLED" if age < 120 else "PROVIDER_RECOVERY_UNVERIFIED"
        return "PROVIDER_STATUS_UNVERIFIED"
    if (route["action"] == "DISPATCH" and route.get("provider") in {"openai", "anthropic"}
            and age <= 15):
        return "PROVIDER_ADMITTED"
    return "PROVIDER_RECOVERY_UNVERIFIED"


def survival_receipt(
    *,
    reason: str,
    parent_run_id: str,
    ledger_path: str | Path,
) -> dict[str, Any]:
    records = load_ledger(ledger_path)
    priority = select_priority_incident(records).as_dict()
    return {
        "status": "DEGRADED_DETERMINISTIC_SURVIVAL",
        "reason": reason or "UNSPECIFIED_PROVIDER_FAILURE",
        "parent_run_id": str(parent_run_id or ""),
        "priority": priority,
        "provider_generation_allowed": False,
        "deterministic_checks_allowed": True,
        "probability_authority": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "generated_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


FAILURE_CODES = frozenset({
    "OPENAI_API_BILLING_REQUIRED", "OPENAI_API_QUOTA_EXCEEDED",
    "OPENAI_API_RATE_LIMITED", "OPENAI_API_UPSTREAM_UNAVAILABLE",
    "ANTHROPIC_API_BILLING_REQUIRED", "ANTHROPIC_API_CREDIT_EXHAUSTED",
    "ANTHROPIC_API_RATE_LIMITED", "ANTHROPIC_API_UPSTREAM_UNAVAILABLE",
    "GEMINI_API_QUOTA_EXHAUSTED", "GEMINI_API_RATE_LIMITED",
    "GEMINI_API_UPSTREAM_UNAVAILABLE",
})
ALLOWED_PROVIDERS = frozenset({"openai", "anthropic", "gemini", "cursor"})
PRESSURE_LEVELS = ((90, "CRITICAL"), (80, "WARNING"), (70, "INFORMATIONAL"))


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _observed_failure(reason: Any) -> str | None:
    # Never serialize arbitrary worker stderr, the raw reason, or credentials.
    if not isinstance(reason, str):
        return None
    for piece in reason.split(":"):
        if piece in FAILURE_CODES:
            return piece
    return None


def assess_resource_pressure(
    route: Mapping[str, Any],
    *,
    usage: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Only fresh, numeric, same-provider usage can produce an advisory band.

    Usage is intentionally advisory until its provenance is independently
    attested. Missing data is UNKNOWN, never treated as zero token consumption.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if (
        not isinstance(route, Mapping)
        or route.get("can_execute") is not False
        or route.get("terminal_authority") != "V17_TERMINAL_REDUCER"
        or route.get("action") not in {"DISPATCH", "SURVIVAL", "NO_ACTION"}
    ):
        raise ValueError("untrusted V17 route receipt")

    action = route["action"]
    provider = route.get("provider")
    if (
        (action == "DISPATCH" and provider not in ALLOWED_PROVIDERS)
        or (action == "SURVIVAL" and provider != "deterministic")
        or (action == "NO_ACTION" and provider != "none")
    ):
        raise ValueError("route provider/action mismatch")

    observed_failure = _observed_failure(route.get("reason"))
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "route_action": action,
        "selected_provider": provider,
        "observed_provider_failure_code": observed_failure,
        "usage_status": "UNAVAILABLE",
        "usage_provenance": "NONE",
        "pressure_band": "UNKNOWN",
        "token_utilization_pct": None,
        "advisory_action": "PRESERVE_EXISTING_DISPATCH",
        "may_override_dispatch": False,
        "may_reset_circuit_breaker": False,
        "may_release_writer_lease": False,
        "may_approve_spend": False,
        "probability_authority": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }

    if usage is None:
        return receipt
    if not isinstance(usage, Mapping):
        receipt["usage_status"] = "INVALID"
        return receipt
    if action != "DISPATCH" or usage.get("provider") != provider:
        receipt["usage_status"] = "PROVIDER_MISMATCH"
        return receipt

    used, limit = usage.get("used_tokens"), usage.get("limit_tokens")
    captured = _parse_timestamp(usage.get("captured_at"))
    if (
        type(used) is not int or type(limit) is not int
        or used < 0 or limit <= 0 or captured is None
    ):
        receipt["usage_status"] = "INVALID"
        return receipt
    age = now.astimezone(timezone.utc) - captured
    if age < timedelta(seconds=-60) or age > timedelta(minutes=15):
        receipt["usage_status"] = "STALE_OR_FUTURE"
        return receipt

    ratio = used * 100.0 / limit
    band = next((name for threshold, name in PRESSURE_LEVELS if ratio >= threshold), "NORMAL")
    receipt.update({
        "usage_status": "FRESH_REPORTED_NOT_ATTESTED",
        "usage_provenance": "CALLER_SUPPLIED_METER",
        "pressure_band": band,
        "token_utilization_pct": round(ratio, 2),
        "advisory_action": (
            "DEFER_OPTIONAL_WORK_RETAIN_P0_RECOVERY"
            if band in {"WARNING", "CRITICAL"}
            else "PRESERVE_EXISTING_DISPATCH"
        ),
    })
    return receipt



def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    resource = sub.add_parser("resource-pressure")
    resource.add_argument("--route-receipt", type=Path, required=True)
    resource.add_argument("--usage-receipt", type=Path)
    resource.add_argument("--output", type=Path, required=True)

    classify = sub.add_parser("classify")
    classify.add_argument("--provider", required=True)
    classify.add_argument("--log-file", required=True)

    breaker = sub.add_parser("circuit-breaker")
    breaker.add_argument("--failure-code", required=True)
    breaker.add_argument("--age-minutes", type=float, required=True)
    breaker.add_argument("--cooldown-minutes", type=int, default=120)

    survival = sub.add_parser("survival-receipt")
    survival.add_argument("--reason", required=True)
    survival.add_argument("--parent-run-id", default="")
    survival.add_argument(
        "--ledger",
        default=str(Path(__file__).with_name("incident-ledger.json")),
    )
    survival.add_argument("--output")
    gemini_provenance = sub.add_parser("verify-gemini-provenance")
    gemini_provenance.add_argument("--run-json", required=True)
    gemini_provenance.add_argument("--artifacts-json", required=True)
    gemini_provenance.add_argument("--expected-run-id", required=True)
    gemini_provenance.add_argument("--expected-run-attempt", required=True)
    gemini_provenance.add_argument("--repository", required=True)
    gemini_child = sub.add_parser("verify-gemini-child")
    gemini_child.add_argument("--run-json", required=True)
    gemini_child.add_argument("--artifacts-json", required=True)
    gemini_child.add_argument("--receipt-dir", required=True)
    gemini_child.add_argument("--expected-run-id", required=True)
    gemini_child.add_argument("--expected-run-attempt", required=True)
    gemini_child.add_argument("--repository", required=True)
    gemini_child.add_argument("--manifest", required=True)
    gemini_child.add_argument("--issues", required=True)

    args = parser.parse_args()

    if args.command == "resource-pressure":
        route = json.loads(args.route_receipt.read_text(encoding="utf-8"))
        usage = (json.loads(args.usage_receipt.read_text(encoding="utf-8"))
                 if args.usage_receipt else None)
        result = assess_resource_pressure(route, usage=usage)
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(f"AI resource pressure: {result['pressure_band']}; usage: {result['usage_status']}")
        return

    if args.command == "classify":
        result = classify_provider_failure(
            args.provider,
            Path(args.log_file).read_text(errors="replace"),
        ).as_dict()
        print(json.dumps(result, sort_keys=True))
        return

    if args.command == "circuit-breaker":
        active = circuit_breaker_active(
            args.failure_code,
            age_minutes=args.age_minutes,
            cooldown_minutes=args.cooldown_minutes,
        )
        print(json.dumps({"active": active}, sort_keys=True))
        return

    if args.command == "verify-gemini-provenance":
        try:
            result = verify_gemini_worker_provenance(
                json.loads(Path(args.run_json).read_text(encoding="utf-8")),
                json.loads(Path(args.artifacts_json).read_text(encoding="utf-8")),
                expected_run_id=args.expected_run_id,
                expected_run_attempt=args.expected_run_attempt,
                repository=args.repository,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result = {
                "status": "HELD",
                "code": "GEMINI_CHILD_EVIDENCE_INVALID",
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            }
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status") == "VERIFIED_PROVENANCE" else 2

    if args.command == "verify-gemini-child":
        receipt_dir = Path(args.receipt_dir)
        receipt_names = (
            "admission.json",
            "selection.json",
            "workspace.json",
            "model-receipt.json",
            "patch-inspection.json",
            "regression-receipt.json",
            "files.json",
            "candidate.patch",
        )
        receipts: dict[str, Any] = {}
        for name in receipt_names:
            path = receipt_dir / name
            try:
                if name.endswith(".json"):
                    receipts[name] = json.loads(path.read_text(encoding="utf-8"))
                elif path.is_file():
                    receipts[name] = path.read_text(encoding="utf-8")
            except (OSError, json.JSONDecodeError):
                pass
        try:
            result = verify_gemini_worker_completion(
                json.loads(Path(args.run_json).read_text(encoding="utf-8")),
                json.loads(Path(args.artifacts_json).read_text(encoding="utf-8")),
                receipts,
                expected_run_id=args.expected_run_id,
                expected_run_attempt=args.expected_run_attempt,
                repository=args.repository,
                manifest=json.loads(Path(args.manifest).read_text(encoding="utf-8")),
                issues=json.loads(Path(args.issues).read_text(encoding="utf-8")),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            result = {
                "status": "HELD",
                "code": "GEMINI_CHILD_EVIDENCE_INVALID",
                "fallback_eligible": False,
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            }
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status", "").startswith("VERIFIED_") else 2

    receipt = survival_receipt(
        reason=args.reason,
        parent_run_id=args.parent_run_id,
        ledger_path=args.ledger,
    )
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
