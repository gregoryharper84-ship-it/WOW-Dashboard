"""Solo-maintainer-compatible automated verification for WOW V17.

Replaces the human-only SIRT reviewer assumption with a reproducible,
fail-closed automated verification contract using existing GitHub checks,
distinct QA (5245832) and Release Authority (5245853) GitHub App identities,
immutable exact-head receipts, adversarial negative-test evidence, and
(for Class C model/calibration changes) immutable source/artifact replay.

Crucial honesty invariants:
- Normal model operation: already-certified V17 specialists scoring ordinary
  rows require NO per-pick human reviewer.
- Routine engineering (Class R): exact ``CLASS_A_EXACT_PATHS`` allowlist uses
  the existing three native App-15368 checks + change impact gate and the
  authorized owner-signed release path where permitted.
- Protected engineering (Class P) and Model/Calibration (Class C):
  QA and Release Apps running from protected ``main`` prove execution isolation
  from PR code and distinct App-token boundaries, NOT independent human review
  and NOT independent administration when the sole repository owner administers
  both Apps and environments. By default (``require_independent_admin=True``),
  P/C changes HOLD with ``SOLO_PROTECTED_INDEPENDENT_ADMIN_UNPROVEN`` unless
  independent administration is proven or an explicit protected-environment
  solo-owner-admin policy is intentionally enabled.
- Automated source verification NEVER authorizes merge, deployment, fitted
  model certification, probability publication, or live execution.
  ``can_execute`` is always False. ``V17_TERMINAL_REDUCER`` remains sole
  terminal authority.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_identity_policy import (  # noqa: E402
    REQUIRED_REGRESSION_CHECKS,
    governance_trust_roots,
    is_trust_root,
)

_CLASS_A_SCRIPT = REPO_ROOT / ".github/scripts/verify_existing_pr_governance_receipt.py"
_class_a_spec = importlib.util.spec_from_file_location("_solo_class_a_policy", _CLASS_A_SCRIPT)
assert _class_a_spec and _class_a_spec.loader
_class_a_mod = importlib.util.module_from_spec(_class_a_spec)
_class_a_spec.loader.exec_module(_class_a_mod)

CLASS_A_EXACT_PATHS: frozenset[str] = _class_a_mod.CLASS_A_EXACT_PATHS
PROTECTED_PATHS: frozenset[str] = frozenset(
    set(_class_a_mod.PROTECTED_PATHS)
    | {"artifacts/wow-engine/v17/solo_automated_verification.py"}
)

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CUSTOM_GPT_IDENTITY = "WOW_BETTING_ENGINE"
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
HEX64_RE = re.compile(r"^[a-f0-9]{64}$")
ACTIONS_APP = 15368

REQUIRED_NATIVE_CHECKS = tuple(REQUIRED_REGRESSION_CHECKS)
CHANGE_IMPACT_CHECK = "WOW V17 change impact gate"
SECURITY_SCAN_CHECK = "WOW CodeQL / python"
R_REQUIRED_CHECKS = (*REQUIRED_NATIVE_CHECKS, CHANGE_IMPACT_CHECK)
PROTECTED_REQUIRED_CHECKS = (*R_REQUIRED_CHECKS, SECURITY_SCAN_CHECK)

DISALLOWED_R_FILE_STATUSES = frozenset({"removed", "renamed"})
ALLOWED_R_FILE_STATUSES = frozenset({"added", "modified"})

CLASS_C_PATH_MARKERS = (
    "specialist",
    "calibrat",
    "model.py",
    "models.py",
    "lower_bound",
    "probability",
    "certification",
    "fitted",
    "certified_model_factory",
    "certified_numerical_engine",
)
MODEL_ARTIFACT_SUFFIXES = (".pkl", ".joblib", ".onnx", ".pt", ".safetensors")


def _int_id(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _norm_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _is_class_c_path(path: str) -> bool:
    low = _norm_path(path).lower()
    if any(marker in low for marker in CLASS_C_PATH_MARKERS):
        return True
    if low.startswith("artifacts/wow-engine/v17/") and low.endswith(MODEL_ARTIFACT_SUFFIXES):
        return True
    return False


def classify_change_scope(
    files: list[str],
    *,
    file_statuses: Mapping[str, str] | None = None,
    governance_files: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Fail-closed diff classifier separating Routine (R) from Protected (P/C)."""
    if governance_files is None:
        governance_files = frozenset(governance_trust_roots(REPO_ROOT))
    cleaned = [_norm_path(f) for f in files if isinstance(f, str) and f.strip()]
    if not cleaned or len(cleaned) != len(files):
        return {
            "change_class": "P",
            "r_eligible": False,
            "protected_paths": [],
            "model_calibration_paths": [],
            "non_allowlisted_paths": [],
            "disallowed_r_operations": True,
        }

    protected: list[str] = []
    model_cal: list[str] = []
    non_allowlisted: list[str] = []
    disallowed_ops = False

    for path in cleaned:
        if _is_class_c_path(path):
            model_cal.append(path)
        if is_trust_root(path, governance_files) or path in PROTECTED_PATHS:
            protected.append(path)
        if path not in CLASS_A_EXACT_PATHS:
            non_allowlisted.append(path)
        if file_statuses is not None:
            status = file_statuses.get(path)
            if status not in ALLOWED_R_FILE_STATUSES:
                disallowed_ops = True

    is_c = bool(model_cal)
    is_p = bool(protected) or bool(non_allowlisted) or disallowed_ops
    if not is_c and not is_p:
        change_class = "R"
    elif is_c and (bool(protected) or any(p not in model_cal for p in non_allowlisted) or disallowed_ops):
        change_class = "P+C"
    elif is_c:
        change_class = "C"
    else:
        change_class = "P"

    return {
        "change_class": change_class,
        "r_eligible": change_class == "R",
        "protected_paths": sorted(set(protected)),
        "model_calibration_paths": sorted(set(model_cal)),
        "non_allowlisted_paths": sorted(set(non_allowlisted)),
        "disallowed_r_operations": disallowed_ops,
    }


def assess_normal_model_operation(
    *,
    specialist_certified: bool,
    calibration_valid: bool,
    inputs_fresh: bool,
    pregame_eligible: bool,
    terminal_authority: str = TERMINAL_AUTHORITY,
    can_execute: bool = False,
    heuristic_fallback_used: bool = False,
) -> dict[str, Any]:
    """Verify ordinary scoring by an already-certified V17 specialist.

    Normal model operation requires NO per-pick human reviewer, while strictly
    preserving fitted-model certification, calibration, freshness, pregame
    eligibility, V17_TERMINAL_REDUCER authority, and can_execute=False.
    """
    findings: list[str] = []
    if specialist_certified is not True:
        findings.append("MODEL_UNAVAILABLE")
    if calibration_valid is not True:
        findings.append("MODEL_OUTPUT_INVALID")
    if inputs_fresh is not True:
        findings.append("MODEL_INPUTS_INSUFFICIENT")
    if pregame_eligible is not True:
        findings.append("PREGAME_ELIGIBILITY_FAILED")
    if terminal_authority != TERMINAL_AUTHORITY:
        findings.append("TERMINAL_AUTHORITY_INVALID")
    if can_execute is not False:
        findings.append("CAN_EXECUTE_MUST_BE_FALSE")
    if heuristic_fallback_used is not False:
        findings.append("HEURISTIC_FALLBACK_FORBIDDEN")

    return {
        "schema_version": 1,
        "operation_status": "SCORING_ALLOWED" if not findings else "HOLD",
        "human_reviewer_required": False,
        "findings": sorted(set(findings)),
        "custom_gpt_identity": CUSTOM_GPT_IDENTITY,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
        "dry_run_only": True,
    }


def _validate_adversarial_receipt(receipt: Any, expected_sha: str) -> list[str]:
    if not isinstance(receipt, Mapping):
        return ["SOLO_ADVERSARIAL_RECEIPT_MISSING"]
    bad: list[str] = []
    if receipt.get("schema_version") != 1:
        bad.append("SOLO_ADVERSARIAL_RECEIPT_INVALID")
    if receipt.get("head_sha") != expected_sha:
        bad.append("SOLO_ADVERSARIAL_RECEIPT_HEAD_MISMATCH")
    if receipt.get("status") != "PASS":
        bad.append("SOLO_ADVERSARIAL_TESTS_NOT_PASSED")
    passed = receipt.get("passed_count")
    failed = receipt.get("failed_count")
    if type(passed) is not int or passed <= 0 or type(failed) is not int or failed != 0:
        bad.append("SOLO_ADVERSARIAL_TESTS_NOT_PASSED")
    cases = receipt.get("negative_cases_verified")
    if not isinstance(cases, list) or not cases or not all(isinstance(c, str) and c.strip() for c in cases):
        bad.append("SOLO_ADVERSARIAL_NEGATIVE_CASES_MISSING")
    unresolved = receipt.get("unresolved_high_critical_findings")
    if type(unresolved) is not int or unresolved != 0:
        bad.append("SOLO_UNRESOLVED_SECURITY_FINDINGS")
    digest = receipt.get("payload_sha256")
    if digest is not None and (not isinstance(digest, str) or not HEX64_RE.fullmatch(digest)):
        bad.append("SOLO_ADVERSARIAL_RECEIPT_DIGEST_INVALID")
    return bad


def _validate_calibration_replay_receipt(receipt: Any, expected_sha: str) -> list[str]:
    if not isinstance(receipt, Mapping):
        return ["SOLO_CALIBRATION_REPLAY_RECEIPT_MISSING"]
    bad: list[str] = []
    if receipt.get("schema_version") != 1:
        bad.append("SOLO_CALIBRATION_REPLAY_RECEIPT_INVALID")
    if receipt.get("head_sha") != expected_sha:
        bad.append("SOLO_CALIBRATION_REPLAY_HEAD_MISMATCH")
    if receipt.get("status") != "REPLAY_CONSISTENT":
        bad.append("SOLO_CALIBRATION_REPLAY_NOT_CONSISTENT")
    if not isinstance(receipt.get("model_version"), str) or not receipt["model_version"].strip():
        bad.append("SOLO_CALIBRATION_REPLAY_MODEL_VERSION_MISSING")
    for field in ("artifact_sha256", "calibrated_lower_bound_sha256", "source_snapshot_sha256"):
        val = receipt.get(field)
        if not isinstance(val, str) or not HEX64_RE.fullmatch(val):
            bad.append(f"SOLO_CALIBRATION_REPLAY_DIGEST_INVALID:{field}")
    if (
        receipt.get("chronology_verified") is not True
        or receipt.get("leakage_free") is not True
        or receipt.get("calibration_bounds_valid") is not True
    ):
        bad.append("SOLO_CALIBRATION_REPLAY_INTEGRITY_FAILED")
    if receipt.get("heuristic_fallback_used") is not False:
        bad.append("SOLO_CALIBRATION_REPLAY_HEURISTIC_FALLBACK_FORBIDDEN")
    if (
        receipt.get("model_certified") is not False
        or receipt.get("probability_publishable") is not False
        or receipt.get("can_execute") is not False
    ):
        bad.append("SOLO_CALIBRATION_REPLAY_AUTHORITY_INVARIANT_VIOLATED")
    return bad


def _validate_external_admin_attestation(
    attestation: Any,
    *,
    expected_sha: str,
    owner_id: int | None,
    author_id: int | None,
    head_commit_author_id: int | None,
) -> tuple[bool, list[str]]:
    if attestation is None:
        return False, []
    if not isinstance(attestation, Mapping):
        return False, ["SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID"]
    bad: list[str] = []
    admin_id = _int_id(attestation.get("admin_user_id"))
    if (
        attestation.get("schema_version") != 1
        or attestation.get("verified") is not True
        or attestation.get("head_sha") != expected_sha
        or admin_id is None
        or not isinstance(attestation.get("attestation_sha256"), str)
        or not HEX64_RE.fullmatch(attestation["attestation_sha256"])
    ):
        bad.append("SOLO_EXTERNAL_ADMIN_ATTESTATION_INVALID")
    if admin_id is not None and admin_id in {owner_id, author_id, head_commit_author_id}:
        bad.append("SOLO_EXTERNAL_ADMIN_NOT_INDEPENDENT")
    return (not bad), bad


def decision(
    code: str,
    *,
    sha: Any,
    change_class: str = "P",
    findings: list[str] | tuple[str, ...] = (),
    checks: tuple[str, ...] | list[str] = REQUIRED_NATIVE_CHECKS,
    assurance_level: str = "HOLD",
    administration_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ready = code in {
        "SOLO_R_EVIDENCE_READY",
        "SOLO_PROTECTED_EVIDENCE_READY",
        "SOLO_EXTERNALLY_ATTESTED_EVIDENCE_READY",
    }
    audit = administration_audit or {
        "execution_isolated_from_pr_code": True,
        "distinct_qa_release_apps": False,
        "independent_administration_proven": False,
        "independent_human_review_performed": False,
    }
    return {
        "schema_version": 1,
        "verification_status": "EVIDENCE_READY" if ready else "HOLD",
        "code": code,
        "head_sha": sha,
        "change_class": change_class,
        "assurance_level": assurance_level if ready else "HOLD",
        "required_check_names": list(checks),
        "findings": sorted(set(findings)),
        "administration_audit": audit,
        "independent_human_review_performed": False,
        "independent_administration_proven": bool(audit.get("independent_administration_proven")),
        "independent_scientific_acceptance_required": True,
        "protected_release_authority_required": True,
        "merge_or_release_authorized": False,
        "model_certified": False,
        "probability_publishable": False,
        "can_execute": False,
        "dry_run_only": True,
        "custom_gpt_identity": CUSTOM_GPT_IDENTITY,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def evaluate(
    *,
    repository: str,
    expected_sha: str,
    pr: Any,
    files: list[str],
    check_runs: Any,
    owner_id: Any,
    qa_app_id: Any,
    release_app_id: Any,
    head_commit_author_id: Any = None,
    file_statuses: Mapping[str, str] | None = None,
    governance_files: frozenset[str] | None = None,
    adversarial_receipt: Mapping[str, Any] | None = None,
    calibration_replay_receipt: Mapping[str, Any] | None = None,
    external_admin_attestation: Mapping[str, Any] | None = None,
    require_independent_admin: bool = True,
    allow_solo_owner_admin_for_protected: bool = False,
) -> dict[str, Any]:
    """Deterministically verify exact-head solo-maintainer evidence; fail closed."""
    if repository != REPO or not isinstance(expected_sha, str) or not SHA_RE.fullmatch(expected_sha):
        return decision("SOLO_TARGET_INVALID", sha=expected_sha, findings=["SOLO_TARGET_INVALID"])
    if not isinstance(pr, dict) or not isinstance(check_runs, list) or not isinstance(files, list):
        return decision("SOLO_EVIDENCE_INVALID", sha=expected_sha, findings=["SOLO_EVIDENCE_INVALID"])

    scope = classify_change_scope(
        files,
        file_statuses=file_statuses,
        governance_files=governance_files,
    )
    change_class = scope["change_class"]
    bad: list[str] = []

    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    user = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    author_id = _int_id(user.get("id"))
    valid_owner_id = _int_id(owner_id)
    valid_head_author_id = _int_id(head_commit_author_id)

    if (
        head.get("sha") != expected_sha
        or pr.get("state") != "open"
        or pr.get("draft") is not False
        or pr.get("merged_at") is not None
        or base.get("ref") != "main"
        or head_repo.get("full_name") != repository
        or author_id is None
    ):
        bad.append("SOLO_PR_IDENTITY_OR_HEAD_INVALID")

    if valid_owner_id is None:
        bad.append("SOLO_OWNER_IDENTITY_INVALID")

    qa_id = _int_id(qa_app_id)
    rel_id = _int_id(release_app_id)
    distinct_apps = (
        qa_id is not None
        and rel_id is not None
        and qa_id != rel_id
        and qa_id != ACTIONS_APP
        and rel_id != ACTIONS_APP
    )
    if not distinct_apps:
        bad.append("SOLO_QA_RELEASE_APP_IDENTITY_INVALID")

    required_checks = R_REQUIRED_CHECKS if change_class == "R" else PROTECTED_REQUIRED_CHECKS
    for name in required_checks:
        matches = [
            c
            for c in check_runs
            if isinstance(c, dict)
            and c.get("name") == name
            and c.get("head_sha") == expected_sha
            and isinstance(c.get("app"), dict)
            and _int_id(c["app"].get("id")) == ACTIONS_APP
        ]
        if not matches:
            bad.append("SOLO_REQUIRED_CHECK_MISSING:" + name)
            continue
        matches.sort(key=lambda c: _int_id(c.get("id")) or -1)
        if matches[-1].get("status") != "completed" or matches[-1].get("conclusion") != "success":
            bad.append("SOLO_REQUIRED_CHECK_NOT_GREEN:" + name)

    independent_admin_proven = False
    if change_class != "R":
        bad.extend(_validate_adversarial_receipt(adversarial_receipt, expected_sha))
        if change_class in {"C", "P+C"}:
            bad.extend(_validate_calibration_replay_receipt(calibration_replay_receipt, expected_sha))
        ext_ok, ext_bad = _validate_external_admin_attestation(
            external_admin_attestation,
            expected_sha=expected_sha,
            owner_id=valid_owner_id,
            author_id=author_id,
            head_commit_author_id=valid_head_author_id,
        )
        bad.extend(ext_bad)
        independent_admin_proven = ext_ok

    audit = {
        "execution_isolated_from_pr_code": True,
        "distinct_qa_release_apps": distinct_apps,
        "qa_app_id": qa_id,
        "release_app_id": rel_id,
        "independent_administration_proven": independent_admin_proven,
        "independent_human_review_performed": False,
        "owner_administered_apps": not independent_admin_proven,
    }

    if bad:
        return decision(
            "SOLO_VERIFICATION_HOLD",
            sha=expected_sha,
            change_class=change_class,
            findings=bad,
            checks=REQUIRED_NATIVE_CHECKS,
            administration_audit=audit,
        )

    if change_class == "R":
        return decision(
            "SOLO_R_EVIDENCE_READY",
            sha=expected_sha,
            change_class="R",
            checks=REQUIRED_NATIVE_CHECKS,
            assurance_level="SOLO_AUTOMATED_DETERMINISTIC_VERIFICATION",
            administration_audit=audit,
        )

    if independent_admin_proven:
        return decision(
            "SOLO_EXTERNALLY_ATTESTED_EVIDENCE_READY",
            sha=expected_sha,
            change_class=change_class,
            checks=REQUIRED_NATIVE_CHECKS,
            assurance_level="AUTOMATED_PROTECTED_VERIFICATION_EXTERNAL_ADMIN",
            administration_audit=audit,
        )

    if require_independent_admin or not allow_solo_owner_admin_for_protected:
        return decision(
            "SOLO_PROTECTED_INDEPENDENT_ADMIN_UNPROVEN",
            sha=expected_sha,
            change_class=change_class,
            findings=["SOLO_INDEPENDENT_ADMIN_NOT_PROVEN"],
            checks=REQUIRED_NATIVE_CHECKS,
            administration_audit=audit,
        )

    return decision(
        "SOLO_PROTECTED_EVIDENCE_READY",
        sha=expected_sha,
        change_class=change_class,
        checks=REQUIRED_NATIVE_CHECKS,
        assurance_level="SOLO_AUTOMATED_PROTECTED_VERIFICATION_OWNER_ADMIN",
        administration_audit=audit,
    )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_json(path: str, token: str) -> Any:
    url = "https://api.github.com/repos/" + REPO + ("/" + path if path else "")
    req = Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with build_opener(NoRedirect()).open(req, timeout=15) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("SOLO_GITHUB_RESPONSE_LIMIT")
    return json.loads(payload)


def _pr_identity(record: Any) -> tuple[Any, ...] | None:
    if not isinstance(record, dict):
        return None
    head = record.get("head")
    base = record.get("base")
    user = record.get("user")
    if (
        not isinstance(head, dict)
        or not isinstance(base, dict)
        or not isinstance(user, dict)
        or not isinstance(head.get("repo"), dict)
    ):
        return None
    return (
        head.get("sha"),
        head.get("ref"),
        head["repo"].get("full_name"),
        base.get("ref"),
        user.get("id"),
        record.get("state"),
        record.get("draft"),
        record.get("merged_at"),
    )


def collect_and_evaluate(
    pr_number: int,
    expected_sha: str,
    *,
    token: str,
    qa_app_id: int,
    release_app_id: int,
    adversarial_receipt: Mapping[str, Any] | None = None,
    calibration_replay_receipt: Mapping[str, Any] | None = None,
    external_admin_attestation: Mapping[str, Any] | None = None,
    require_independent_admin: bool = True,
    allow_solo_owner_admin_for_protected: bool = False,
) -> dict[str, Any]:
    """Read authenticated PR, file inventory, and check runs directly from GitHub."""
    if (
        type(pr_number) is not int
        or pr_number <= 0
        or not isinstance(expected_sha, str)
        or not SHA_RE.fullmatch(expected_sha)
    ):
        return decision("SOLO_TARGET_INVALID", sha=expected_sha, findings=["SOLO_TARGET_INVALID"])
    if not token:
        return decision(
            "SOLO_GITHUB_EVIDENCE_UNAVAILABLE",
            sha=expected_sha,
            findings=["SOLO_GITHUB_EVIDENCE_UNAVAILABLE"],
        )
    try:
        pr = github_json(f"pulls/{pr_number}", token)
        repo = github_json("", token)
        commit = github_json(f"commits/{expected_sha}", token)
        pr_files = github_json(f"pulls/{pr_number}/files?per_page=100", token)
        checks = github_json(f"commits/{expected_sha}/check-runs?per_page=100", token)
        # Re-read PR after collection to detect concurrent force-push / state change.
        final_pr = github_json(f"pulls/{pr_number}", token)
    except (HTTPError, URLError, ValueError, TypeError, TimeoutError, json.JSONDecodeError):
        return decision(
            "SOLO_GITHUB_EVIDENCE_UNAVAILABLE",
            sha=expected_sha,
            findings=["SOLO_GITHUB_EVIDENCE_UNAVAILABLE"],
        )

    before = _pr_identity(pr)
    after = _pr_identity(final_pr)
    if before is None or after is None:
        return decision("SOLO_EVIDENCE_INVALID", sha=expected_sha, findings=["SOLO_EVIDENCE_INVALID"])
    if before != after:
        return decision(
            "SOLO_PR_CHANGED_DURING_VERIFICATION",
            sha=expected_sha,
            findings=["SOLO_PR_CHANGED_DURING_VERIFICATION"],
        )
    if (
        not isinstance(repo, dict)
        or not isinstance(commit, dict)
        or not isinstance(pr_files, list)
        or not isinstance(checks, dict)
        or not isinstance(checks.get("check_runs"), list)
        or not isinstance(repo.get("owner"), dict)
        or (commit.get("author") is not None and not isinstance(commit.get("author"), dict))
        or type(checks.get("total_count")) is not int
        or checks["total_count"] < 0
    ):
        return decision("SOLO_EVIDENCE_INVALID", sha=expected_sha, findings=["SOLO_EVIDENCE_INVALID"])
    if (
        checks["total_count"] > 100
        or checks["total_count"] != len(checks["check_runs"])
        or len(pr_files) >= 100
    ):
        return decision(
            "SOLO_EVIDENCE_PAGINATION_INCOMPLETE",
            sha=expected_sha,
            findings=["SOLO_EVIDENCE_PAGINATION_INCOMPLETE"],
        )

    file_names: list[str] = []
    file_statuses: dict[str, str] = {}
    for row in pr_files:
        if not isinstance(row, dict) or not isinstance(row.get("filename"), str):
            return decision("SOLO_EVIDENCE_INVALID", sha=expected_sha, findings=["SOLO_EVIDENCE_INVALID"])
        fname = _norm_path(row["filename"])
        file_names.append(fname)
        file_statuses[fname] = str(row.get("status") or "")
        prev = row.get("previous_filename")
        if isinstance(prev, str) and prev.strip():
            prev_norm = _norm_path(prev)
            file_names.append(prev_norm)
            file_statuses[prev_norm] = "renamed"

    head_author = commit.get("author")
    return evaluate(
        repository=REPO,
        expected_sha=expected_sha,
        pr=final_pr,
        files=file_names,
        file_statuses=file_statuses,
        check_runs=checks["check_runs"],
        owner_id=repo["owner"].get("id"),
        qa_app_id=qa_app_id,
        release_app_id=release_app_id,
        head_commit_author_id=head_author.get("id") if head_author else None,
        adversarial_receipt=adversarial_receipt,
        calibration_replay_receipt=calibration_replay_receipt,
        external_admin_attestation=external_admin_attestation,
        require_independent_admin=require_independent_admin,
        allow_solo_owner_admin_for_protected=allow_solo_owner_admin_for_protected,
    )


def _load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("RECEIPT_NOT_JSON_OBJECT")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--qa-app-id", type=int, default=0)
    parser.add_argument("--release-app-id", type=int, default=0)
    parser.add_argument("--adversarial-receipt", type=Path)
    parser.add_argument("--calibration-replay-receipt", type=Path)
    parser.add_argument("--external-admin-attestation", type=Path)
    parser.add_argument("--allow-solo-owner-admin-for-protected", action="store_true")
    args = parser.parse_args(argv)

    qa_app_id = args.qa_app_id or int(os.environ.get("WOW_QA_APP_ID") or "0")
    release_app_id = args.release_app_id or int(os.environ.get("WOW_RELEASE_APP_ID") or "0")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    allow_solo_owner = args.allow_solo_owner_admin_for_protected or (
        os.environ.get("WOW_QA_SOLO_OWNER_ADMIN_ACCEPTED", "").lower() == "true"
    )
    try:
        adv = _load_optional_json(args.adversarial_receipt)
        cal = _load_optional_json(args.calibration_replay_receipt)
        ext = _load_optional_json(args.external_admin_attestation)
    except (OSError, ValueError, json.JSONDecodeError):
        receipt = decision(
            "SOLO_EVIDENCE_INVALID",
            sha=args.head_sha,
            findings=["SOLO_EVIDENCE_INVALID"],
        )
        print(json.dumps(receipt, sort_keys=True))
        return 1

    receipt = collect_and_evaluate(
        args.pr,
        args.head_sha,
        token=token,
        qa_app_id=qa_app_id,
        release_app_id=release_app_id,
        adversarial_receipt=adv,
        calibration_replay_receipt=cal,
        external_admin_attestation=ext,
        require_independent_admin=not allow_solo_owner,
        allow_solo_owner_admin_for_protected=allow_solo_owner,
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["verification_status"] == "EVIDENCE_READY" else 1


if __name__ == "__main__":
    sys.exit(main())
