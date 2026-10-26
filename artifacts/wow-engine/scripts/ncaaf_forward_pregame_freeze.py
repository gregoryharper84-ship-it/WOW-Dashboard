"""Immutable pregame *forward* feature witness; does not certify training provenance.

Read-only proof that a specific 44-row result/form research feature snapshot
and cross-checked ESPN identities were archived at a genuine GitHub Actions
protected-main capture time *before each target game*. It does not create
historical archived-pregame evidence, score events, or grant certification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from ncaaf_frozen_result_form_audit import audit, _time, FrozenInputError

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SCHEMA = "WOW_NCAAF_RESULT_FORM_PROSPECTIVE_FREEZE_RECEIPT_V1"
REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
CAPTURE_POLICY = "TARGET_FORWARD_INPUT_ARCHIVED_BEFORE_KICKOFF_NO_HISTORICAL_ATTESTATION"


class ForwardCaptureError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ForwardCaptureError(code)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_receipt(
    *,
    feature_bytes: bytes,
    identity_bytes: bytes,
    captured_at: datetime,
    github_sha: str,
    github_run_id: str,
    github_run_attempt: str,
    github_repository: str = REPOSITORY,
    github_ref: str = "refs/heads/main",
    github_event_name: str = "push",
) -> dict[str, Any]:
    _require(captured_at.tzinfo is not None,
             "NCAAF_FREEZE_CAPTURE_CLOCK_NAIVE")
    clock = captured_at.astimezone(timezone.utc)
    _require(re.fullmatch(r"[0-9a-f]{40}", github_sha) is not None,
             "NCAAF_FREEZE_HEAD_SHA_INVALID")
    _require(github_repository == REPOSITORY and
             github_ref == "refs/heads/main" and
             github_event_name == "push",
             "NCAAF_FREEZE_GITHUB_CONTEXT_UNTRUSTED")
    _require(github_run_id.isdecimal() and int(github_run_id) > 0 and
             github_run_attempt.isdecimal() and int(github_run_attempt) > 0,
             "NCAAF_FREEZE_ACTION_IDENTITY_INVALID")
    try:
        features = json.loads(feature_bytes)
        identities = json.loads(identity_bytes)
        result = audit(features, identities)
    except (ValueError, UnicodeDecodeError, FrozenInputError, TypeError) as exc:
        raise ForwardCaptureError(
            getattr(exc, "code", "NCAAF_FREEZE_FROZEN_INPUT_INVALID")
        ) from exc
    _require(result.get("events_verified") == 44,
             "NCAAF_FREEZE_COHORT_INCOMPLETE")
    _require(features.get("schema_version") == "WOW_NCAAF_RESULT_FORM_FORWARD_EVIDENCE_V1" and
             identities.get("schema_version") == "WOW_FORWARD_MODEL_IDENTITY_FIXTURE_V1",
             "NCAAF_FREEZE_SOURCE_SCHEMA_INVALID")
    _require(features.get("model_artifact_certified") is False and
             features.get("archived_pregame_score_claimed") is False and
             features.get("can_execute") is False and
             features.get("probability_publishable") is False,
             "NCAAF_FREEZE_UNSUPPORTED_AUTHORITY")
    frozen_espn = (identities.get("sports") or {}).get("NCAAF") or {}
    _require(identities.get("github_run_id") == 39852468868 and
             features.get("espn_source", {}).get("github_run_id") == 39852468868,
             "NCAAF_FREEZE_ESPN_CAPTURE_PROVENANCE_MISMATCH")
    _require(re.fullmatch(r"[a-f0-9]{64}", features.get("espn_source", {}).get("raw_sha256", "")) is not None and
             re.fullmatch(r"[a-f0-9]{64}", features.get("source_snapshot", {}).get("payload_sha256", "")) is not None,
             "NCAAF_FREEZE_ORIGINAL_SOURCE_HASH_INVALID")
    original_espn_clock = _time(frozen_espn.get("capture_host_clock_utc"))
    cfbd_clock = _time(features.get("source_snapshot", {}).get("retrieved_at"))
    _require(original_espn_clock <= clock and cfbd_clock <= clock,
             "NCAAF_FREEZE_SOURCE_CAPTURE_AFTER_WITNESS")
    rows = features["events"]
    starts = [_time(r["event_start_time"]) for r in rows]
    first_kickoff = min(starts)
    # Strict: this GitHub Actions witness must precede every event by >=30m.
    _require((first_kickoff - clock).total_seconds() >= 1800,
             "NCAAF_FREEZE_NOT_30_MIN_PREKICKOFF")
    _require(all(r.get("status") == "RESEARCH_FORWARD_FEATURES_ONLY" and
                 r.get("calibrated_lower_bound") is None and
                 r.get("calibrated_probability") is None and
                 r.get("model_probability") is None and
                 r.get("probability_publishable") is False and
                 r.get("can_execute") is False for r in rows),
             "NCAAF_FREEZE_SCORE_FIELD_FORBIDDEN")
    return {
        "schema_version": SCHEMA,
        "status": "NCAAF_44_PROSPECTIVE_FORWARD_INPUTS_FROZEN_NOT_CERTIFIED",
        "capture_policy": CAPTURE_POLICY,
        "witness_kind": "GITHUB_ACTIONS_PROTECTED_MAIN_CAPTURE_BEFORE_TARGET_EVENTS",
        "github": {
            "repository": github_repository, "ref": github_ref,
            "event": github_event_name, "sha": github_sha,
            "run_id": int(github_run_id),
            "run_attempt": int(github_run_attempt),
        },
        "captured_at_utc": _stamp(clock),
        "first_target_kickoff_utc": _stamp(first_kickoff),
        "last_target_kickoff_utc": _stamp(max(starts)),
        "minimum_minutes_before_kickoff": int(
            (first_kickoff - clock).total_seconds() // 60
        ),
        "event_count": len(rows),
        "model_family": features["candidate_model_family"],
        "feature_schema_version": features["feature_schema_version"],
        "candidate_input_sha256": hashlib.sha256(feature_bytes).hexdigest(),
        "espn_identity_fixture_sha256": hashlib.sha256(identity_bytes).hexdigest(),
        "cfbd_snapshot": features["source_snapshot"],
        "espn_source": features["espn_source"],
        "event_ids": sorted(str(r["official_event_id"]) for r in rows),
        "identity_replay_status": result["status"],
        "historical_training_rows_retroactively_attested": False,
        "historical_training_source_attestation": "HOLD",
        "provider_raw_payload_bytes_in_this_archive": False,
        "model_probability": None,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "candidate_certified": False,
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True, type=Path)
    ap.add_argument("--identities", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    try:
        receipt = build_receipt(
            feature_bytes=args.features.read_bytes(),
            identity_bytes=args.identities.read_bytes(),
            captured_at=datetime.now(timezone.utc),
            github_sha=os.environ.get("GITHUB_SHA", ""),
            github_run_id=os.environ.get("GITHUB_RUN_ID", ""),
            github_run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            github_repository=os.environ.get("GITHUB_REPOSITORY", ""),
            github_ref=os.environ.get("GITHUB_REF", ""),
            github_event_name=os.environ.get("GITHUB_EVENT_NAME", ""),
        )
    except (ForwardCaptureError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "code": getattr(exc, "code", "NCAAF_FREEZE_INPUT_READ_ERROR"),
                          "probability_publishable": False, "can_execute": False}))
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": receipt["status"], "event_count": receipt["event_count"],
        "captured_at_utc": receipt["captured_at_utc"],
        "first_target_kickoff_utc": receipt["first_target_kickoff_utc"],
        "minimum_minutes_before_kickoff": receipt["minimum_minutes_before_kickoff"],
        "candidate_input_sha256": receipt["candidate_input_sha256"],
        "candidate_certified": False,
        "probability_publishable": False, "can_execute": False
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
