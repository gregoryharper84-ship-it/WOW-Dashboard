"""Evidence-gated WOW V17 Track 1 -> 2 -> 3 engineering handoff queue.

Pure, read-only queue compilation under the existing Conductor. This module
does not dispatch workers, acquire writer leases, approve releases, modify
models, or certify model/production results. External receipts are input
evidence to be authenticated by their existing owners, not new authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

TERMINAL = "V17_TERMINAL_REDUCER"
TRACKS = ("TRACK_1_RECOVERY", "TRACK_2_MODEL_SCIENCE", "TRACK_3_PREVENTION")
EDGES = ((TRACKS[0], TRACKS[1]), (TRACKS[1], TRACKS[2]))
SHA = re.compile(r"^[0-9a-f]{40}$")
EXPERIMENT_PREFIX = "artifacts/wow-engine/v17/experiments/"


class HandoffContractError(ValueError):
    """A typed refusal to accept an unsupported cross-team handoff."""


def _governed(payload: dict[str, Any], name: str) -> None:
    if not isinstance(payload, dict):
        raise HandoffContractError(f"{name}_NOT_AN_OBJECT")
    if payload.get("can_execute") is not False:
        raise HandoffContractError(f"{name}_EXECUTION_MUST_BE_DISABLED")
    if payload.get("terminal_authority") != TERMINAL:
        raise HandoffContractError(f"{name}_TERMINAL_AUTHORITY_MISMATCH")


def _required(row: dict[str, Any], keys: tuple[str, ...], label: str) -> None:
    for key in keys:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise HandoffContractError(f"{label}_MISSING_{key.upper()}")


def _validate_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    _governed(config, "CONFIG")
    if config.get("schema_version") != "1.0":
        raise HandoffContractError("CONFIG_SCHEMA_UNSUPPORTED")
    if config.get("control_plane") != "WOW_ECOSYSTEM_CONDUCTOR":
        raise HandoffContractError("COMPETING_CONTROL_PLANE_FORBIDDEN")
    if config.get("no_new_dispatcher") is not True:
        raise HandoffContractError("NEW_DISPATCHER_FORBIDDEN")
    tracks = config.get("tracks")
    if (
        not isinstance(tracks, list)
        or len(tracks) != len(TRACKS)
        or any(not isinstance(x, dict) for x in tracks)
        or tuple(x.get("id") for x in tracks) != TRACKS
    ):
        raise HandoffContractError("TRACK_ORDER_OR_COVERAGE_INVALID")
    issues = set()
    for track in tracks:
        _required(track, ("owner", "scope", "acceptance"), "TRACK")
        issue = track.get("tracking_issue")
        if type(issue) is not int or issue <= 0 or issue in issues:
            raise HandoffContractError("TRACK_ISSUE_INVALID_OR_DUPLICATE")
        issues.add(issue)
        if track.get("can_execute") is not False:
            raise HandoffContractError("TRACK_EXECUTION_MUST_BE_DISABLED")
        if track.get("may_approve_own_work") is not False:
            raise HandoffContractError("TRACK_SELF_APPROVAL_FORBIDDEN")
    return tracks


def _track1_proof(row: dict[str, Any]) -> None:
    _governed(row, "RECOVERY_RECEIPT")
    _required(
        row,
        ("incident_id", "root_cause", "typed_failure", "source_sha",
         "reproduction_ref", "regression_ref", "owner_issue_ref"),
        "RECOVERY_RECEIPT",
    )
    if not SHA.fullmatch(row["source_sha"]):
        raise HandoffContractError("RECOVERY_SOURCE_SHA_INVALID")
    if row.get("change_class") not in ("A", "B", "C"):
        raise HandoffContractError("RECOVERY_CHANGE_CLASS_MISSING")
    if row.get("finding_status") not in ("ROOT_CAUSE_CONFIRMED", "PATCH_CANDIDATE", "PRODUCTION_EVIDENCE_RECORDED"):
        raise HandoffContractError("RECOVERY_FINDING_NOT_CONFIRMED")
    if row.get("self_approved") is not False:
        raise HandoffContractError("RECOVERY_SELF_APPROVAL_FORBIDDEN")
    if row.get("production_verified") is True:
        # The existing release + QA + independent SIRT proof chain owns closure.
        _required(row, ("integrated_sha", "deployed_sha", "action_receipt_id",
                        "persistence_receipt_id", "qa_receipt_id",
                        "release_receipt_id", "independent_verification_id"),
                  "RECOVERY_PRODUCTION")
        if not SHA.fullmatch(row["integrated_sha"]) or row["integrated_sha"] != row["deployed_sha"]:
            raise HandoffContractError("PRODUCTION_EXACT_SHA_UNPROVEN")
        if row["finding_status"] != "PRODUCTION_EVIDENCE_RECORDED":
            raise HandoffContractError("PRODUCTION_STATUS_CONTRADICTION")
    elif row.get("production_verified") not in (False, None):
        raise HandoffContractError("PRODUCTION_PROOF_UNTRUSTED")


def _track2_proof(row: dict[str, Any], recovery: dict[str, Any]) -> None:
    _governed(row, "SCIENCE_RECEIPT")
    _required(row, ("incident_id", "upstream_source_sha", "experiment_id",
                    "experiment_path", "hypothesis", "replay_ref",
                    "counterexample_ref", "holdout_ref", "regression_ref"),
              "SCIENCE_RECEIPT")
    if row["incident_id"] != recovery["incident_id"] or row["upstream_source_sha"] != recovery["source_sha"]:
        raise HandoffContractError("SCIENCE_UPSTREAM_IDENTITY_MISMATCH")
    if not row["experiment_path"].startswith(EXPERIMENT_PREFIX) or ".." in row["experiment_path"]:
        raise HandoffContractError("SCIENCE_EXPERIMENT_PATH_FORBIDDEN")
    if row.get("validation_status") != "RESEARCH_VALIDATED":
        raise HandoffContractError("SCIENCE_VALIDATION_NOT_PROVEN")
    if row.get("production_behavior_change") is not False:
        raise HandoffContractError("SCIENCE_SERVING_CHANGE_FORBIDDEN")
    if row.get("self_approved") is not False:
        raise HandoffContractError("SCIENCE_SELF_APPROVAL_FORBIDDEN")


def _fingerprint(incident: str, source: str, target: str, evidence: dict[str, Any]) -> str:
    stable = {"incident": incident, "source": source, "target": target, "evidence": evidence}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_queue(config: dict[str, Any], evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compile three ordered lanes without inventing source, model or QA success.

    Accepted handoffs remain *candidate evidence*; a connected worker must
    authenticate provenance, own the existing incident/lease, and record the
    real handoff before doing work. Compilation is not worker admission.
    """
    tracks = _validate_config(config)
    evidence = evidence if evidence is not None else {}
    if not isinstance(evidence, dict) or set(evidence) - {"recovery", "science"}:
        raise HandoffContractError("EVIDENCE_ENVELOPE_INVALID")
    recovery = evidence.get("recovery")
    science = evidence.get("science")
    if science is not None and recovery is None:
        raise HandoffContractError("SCIENCE_WITHOUT_RECOVERY_HANDOFF")
    if recovery is not None:
        _track1_proof(recovery)
    if science is not None:
        _track2_proof(science, recovery)

    results: list[dict[str, Any]] = []
    statuses = (
        "QUEUED_IN_EXISTING_RECOVERY",
        "EVIDENCE_READY_RESEARCH_ONLY" if recovery else "WAITING_FOR_TRACK_1_EVIDENCE",
        "EVIDENCE_READY_INDEPENDENT_ASSURANCE" if science else "WAITING_FOR_TRACK_2_EVIDENCE",
    )
    for track, state in zip(tracks, statuses):
        results.append({
            "track": track["id"], "issue_number": track["tracking_issue"],
            "owner": track["owner"], "state": state,
            "dispatch_authority": "EXISTING_CONDUCTOR_LEASE_ONLY",
            "can_execute": False,
        })

    handoffs = []
    for source, target in EDGES:
        source_evidence = recovery if source == TRACKS[0] else science
        item: dict[str, Any] = {
            "from": source, "to": target,
            "state": "EVIDENCE_READY_UNVERIFIED" if source_evidence else "WAITING_FOR_EVIDENCE",
            "next_authority": "EXISTING_CONDUCTOR" if target == TRACKS[1] else "INDEPENDENT_SIRT",
            "approval_or_release_granted": False,
            "can_execute": False,
        }
        if source_evidence is not None:
            item["incident_id"] = source_evidence["incident_id"]
            item["fingerprint"] = _fingerprint(source_evidence["incident_id"], source, target, source_evidence)
            item["evidence"] = source_evidence
        handoffs.append(item)

    return {
        "schema_version": "1.0", "status": "ADVISORY_QUEUE_NOT_DISPATCHED",
        "source": "EXISTING_WOW_ENGINEERING_QUEUE",
        "tracks": results, "handoffs": handoffs,
        "next_action": "Existing Conductor validates source, leases and independent receipts before any dispatch.",
        "model_probability_authority": "NONE",
        "production_promotion_allowed": False,
        "self_approval_allowed": False,
        "can_execute": False, "terminal_authority": TERMINAL,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile read-only WOW three-track handoff queue")
    parser.add_argument("--config", required=True)
    parser.add_argument("--evidence", help="Optional authentic receipt envelope in JSON; absence keeps downstream queued")
    parser.add_argument("--output", help="Optional generated queue JSON file")
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8")) if args.evidence else {}
        queue = build_queue(config, evidence)
    except (HandoffContractError, ValueError, OSError) as error:
        print(json.dumps({"status": "BLOCKED_WITH_EXACT_REASON", "reason": str(error),
                          "can_execute": False, "terminal_authority": TERMINAL}, sort_keys=True))
        return 2
    rendered = json.dumps(queue, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
