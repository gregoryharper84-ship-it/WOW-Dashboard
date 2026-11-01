"""Exact NFL Daily package readback without invented dynamic-calibration proof.

This does not fit or calibrate a model, alter probabilities, or override LLP.
A missing, stale, conflicting, or unreadable receipt leaves the original result
HELD at the existing full-board publication gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from math import exp, isfinite
from typing import Any, Mapping

STAGE_SOURCE = "NFL_IMMUTABLE_PREGAME_PREDICTION_READBACK"
CAN_EXECUTE = False


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _same_instant(a: Any, b: Any) -> bool:
    left, right = _instant(a), _instant(b)
    return left is not None and right is not None and left == right


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if isfinite(x) else None


def _same_number(a: Any, b: Any) -> bool:
    left, right = _number(a), _number(b)
    return left is not None and right is not None and abs(left - right) <= 1e-10



def _actual_producer_receipt(row: Mapping[str, Any]) -> str | None:
    """Reconcile the scorer-produced Platt operation, not its health flag."""
    saved = row.get("model_output_snapshot")
    receipt = saved.get("calibration_operation_receipt") if isinstance(saved, Mapping) else None
    if receipt is None:
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_MISSING"
    if not isinstance(receipt, Mapping):
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_INVALID"
    if (receipt.get("schema_version") != "NFL_CALIBRATION_OPERATION_V1"
            or receipt.get("producer") != "nfl_event_model_v17.score_feature_row"
            or receipt.get("status") != "COMPLETED"
            or receipt.get("can_execute") is not False):
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_INVALID"

    # The checksum catches post-inference mutation. This is an integrity digest,
    # not a signature or a substitute for independent model certification.
    try:
        original = {key: val for key, val in receipt.items() if key != "evidence_sha256"}
        digest = hashlib.sha256(json.dumps(
            original, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_INVALID"
    if digest != receipt.get("evidence_sha256"):
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_INVALID"

    for name, other in (
        ("research_run_id", row.get("research_run_id")),
        ("prediction_identity_key", row.get("prediction_identity_key")),
        ("official_event_id", row.get("official_event_id")),
        ("event_key", row.get("event_key")),
        ("requested_slate_date", row.get("requested_slate_date")),
        ("model_artifact_id", row.get("model_artifact_id")),
        ("model_version", row.get("model_version")),
        ("calibration_method", row.get("calibration_method")),
        ("calibration_version", row.get("calibration_version")),
        ("uncertainty_method", row.get("uncertainty_method")),
        ("feature_row_hash", row.get("feature_row_hash")),
        ("source_snapshot_id", row.get("source_snapshot_id")),
        ("artifact_checksum", saved.get("artifact_checksum")),
    ):
        if not other or str(receipt.get(name) or "") != str(other):
            return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_IDENTITY_MISMATCH"
    for name in ("model_timestamp", "source_snapshot_timestamp", "event_start_time_utc"):
        if not _same_instant(receipt.get(name), row.get(name)):
            return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_TIMESTAMP_MISMATCH"
    if receipt.get("calibration_training_n") != row.get("calibration_training_n"):
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_INVALID"

    for name in (
        "raw_home_probability",
        "calibrated_home_probability", "calibrated_away_probability",
        "calibrated_home_lower_bound", "calibrated_home_upper_bound",
        "calibrated_away_lower_bound", "calibrated_away_upper_bound",
    ):
        if not _same_number(receipt.get(name), row.get(name)):
            return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_NUMERIC_MISMATCH"

    # Independently replay the *same* scorer Platt transform and bound formula,
    # without recalibrating coefficients or substituting sportsbook probabilities.
    logit = _number(receipt.get("platt_input_logit"))
    a = _number(receipt.get("platt_a"))
    b = _number(receipt.get("platt_b"))
    margin = _number(receipt.get("uncertainty_margin"))
    if None in (logit, a, b, margin) or not 0 < margin <= 0.20:
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_INVALID"
    sigmoid = lambda x: 1.0 / (1.0 + exp(-max(-40.0, min(40.0, x))))
    home_raw = sigmoid(logit)
    home = sigmoid(a * logit + b)
    lower = max(0.001, home - margin)
    upper = min(0.999, home + margin)
    expected = {
        "raw_home_probability": home_raw,
        "calibrated_home_probability": home,
        "calibrated_away_probability": 1.0 - home,
        "calibrated_home_lower_bound": lower,
        "calibrated_home_upper_bound": upper,
        "calibrated_away_lower_bound": max(0.001, 1.0 - upper),
        "calibrated_away_upper_bound": min(0.999, 1.0 - lower),
    }
    if any(not _same_number(row.get(name), value) for name, value in expected.items()):
        return "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_NUMERIC_MISMATCH"
    return None


def _final_refresh_proven(row: Mapping[str, Any], governed: Mapping[str, Any]) -> bool:
    governance = governed.get("llp_governance")
    refresh = governance.get("final_refresh") if isinstance(governance, Mapping) else None
    if not isinstance(refresh, Mapping):
        return False
    if (refresh.get("source") != "NFL_CANONICAL_SCHEDULE_REQUERY"
            or refresh.get("final_refresh_status") != "PASS"
            or refresh.get("reasons") != []
            or refresh.get("probability_invalidated") is not False
            or refresh.get("rerun_required") is not False
            or refresh.get("can_execute") is not False
            or not refresh.get("refresh_source_snapshot_id")
            or not _same_instant(refresh.get("model_timestamp"), row.get("model_timestamp"))
            or str(refresh.get("scored_source_snapshot_id") or "") != str(row.get("source_snapshot_id") or "")):
        return False
    checked, modeled, kickoff = (
        _instant(refresh.get("checked_at")),
        _instant(row.get("model_timestamp")),
        _instant(row.get("event_start_time_utc")),
    )
    return all((checked, modeled, kickoff)) and modeled <= checked < kickoff


# Bounded pagination for the exact-version forward cohort. A single bounded
# read is not sufficient: a 250-game cohort behind a 100-game minimum used to
# read back as 200 and be presented as an exact total.
_GRADE_PAGE_SIZE = 1000
_GRADE_MAX_PAGES = 20


def _observed_version_cohort(db: Any, version: str) -> tuple[set[str], bool]:
    """Distinct officially graded events for one exact model version.

    Pages until a short page proves the end of the version-filtered set. If the
    page cap is reached the caller is told the result is a lower bound, so a
    truncated read is never presented as an exact cohort size.
    """
    seen: set[str] = set()
    for page in range(_GRADE_MAX_PAGES):
        start = page * _GRADE_PAGE_SIZE
        result = (
            db.table("wow_nfl_forward_shadow_grades")
            .select("official_event_id")
            .eq("model_version", version)
            .range(start, start + _GRADE_PAGE_SIZE - 1)
            .execute()
        )
        grades = result.data
        if not isinstance(grades, list):
            raise ValueError("malformed forward grade page")
        for grade in grades:
            if isinstance(grade, Mapping) and grade.get("official_event_id"):
                seen.add(str(grade["official_event_id"]))
        if len(grades) < _GRADE_PAGE_SIZE:
            return seen, False
    return seen, True


def _forward_certification_diagnosis(
    db: Any, row: Mapping[str, Any]
) -> tuple[bool, list[str], dict[str, Any]]:
    """Independent cohort and champion certification, never automatic promotion.

    Returns the same boolean decision this module has always made, plus the
    typed evidence behind a HOLD. A bare False collapse made every distinct
    cause of an unproven forward certification indistinguishable, which is what
    let earlier reviews diagnose this gate as a missing-boolean problem.
    """
    blockers: list[str] = []
    version = str(row.get("model_version") or "")
    evidence: dict[str, Any] = {
        "model_version": version,
        "model_artifact_id": str(row.get("model_artifact_id") or ""),
        "graded_events": None,
        "graded_events_exact": None,
        "mismatch_evidence": [],
        "minimum_forward_required": None,
        "health_graded_n": None,
        "health_status": None,
    }
    try:
        models = db.table("wow_nfl_event_fitted_model_artifacts").select(
            "artifact_id,active,promoted,probability_publishable"
        ).eq("artifact_id", row["model_artifact_id"]).limit(2).execute().data
        health = db.table("wow_nfl_forward_calibration_health").select(
            "graded_n,minimum_forward_required,status,certification_recommendation,blockers"
        ).order("generated_at", desc=True).limit(1).execute().data
    except Exception:
        return False, ["NFL_FORWARD_CERTIFICATION_QUERY_FAILED"], evidence
    if not isinstance(models, list) or len(models) != 1 or not isinstance(health, list) or len(health) != 1:
        return False, ["NFL_FORWARD_CERTIFICATION_REGISTRY_SHAPE_INVALID"], evidence
    model, assessment = models[0], health[0]
    minimum = assessment.get("minimum_forward_required")
    evidence["health_graded_n"] = assessment.get("graded_n")
    evidence["health_status"] = assessment.get("status")
    evidence["minimum_forward_required"] = minimum
    # Health is a global snapshot. Verify that its cohort actually contains
    # enough unique prospective grades for this prediction's exact model version.
    # A different champion's 100 games can never authorize this model.
    if not isinstance(minimum, int) or minimum < 100:
        return False, ["NFL_FORWARD_MINIMUM_COHORT_INVALID"], evidence
    try:
        unique_games, truncated = _observed_version_cohort(db, version)
    except Exception:
        return False, ["NFL_FORWARD_GRADE_COHORT_QUERY_FAILED"], evidence
    evidence["graded_events"] = len(unique_games)
    evidence["graded_events_exact"] = not truncated

    # Each conjunct is reported separately so an operator can tell genuine
    # missing forward evidence from a champion-registry or policy defect.
    #
    # An empty exact-version cohort is NOT by itself a version mismatch: a newly
    # registered champion may have no settled events, or no captured pregame
    # forecasts, yet. Mismatch is claimed only when a separately observed cohort
    # for some other model_version actually exists.
    if not unique_games:
        other_versions: list[str] = []
        try:
            observed = (
                db.table("wow_nfl_forward_shadow_grades")
                .select("model_version")
                .neq("model_version", version)
                .limit(5)
                .execute()
                .data
            )
            if isinstance(observed, list):
                other_versions = sorted({
                    str(g["model_version"]) for g in observed
                    if isinstance(g, Mapping) and g.get("model_version")
                })
        except Exception:
            # Inability to establish a mismatch is not a mismatch claim, and it
            # never upgrades the row: the cohort is still below minimum.
            other_versions = []
        evidence["mismatch_evidence"] = other_versions
        if other_versions:
            blockers.append("NFL_FORWARD_COHORT_MODEL_VERSION_MISMATCH")
    if not len(unique_games) >= minimum:
        blockers.append("NFL_FORWARD_GRADED_EVENTS_BELOW_MINIMUM")
    if model.get("active") is not True:
        blockers.append("NFL_ARTIFACT_NOT_ACTIVE")
    if model.get("promoted") is not True:
        blockers.append("NFL_ARTIFACT_NOT_PROMOTED")
    if model.get("probability_publishable") is not True:
        # wow_nfl_event_fitted_model_artifacts is constrained by
        # wow_nfl_event_fitted_registry_not_probability_publication
        # (CHECK (probability_publishable = false)), so this conjunct can never
        # hold. It is reported as a policy/schema conflict, never repaired here
        # and never treated as a licence to publish. This diagnostic is a
        # classification label; the governing terminal code stays unchanged.
        blockers.append("NFL_ARTIFACT_PUBLICATION_FLAG_NOT_GRANTED")
        blockers.append("NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT")
        evidence["artifact_publication_activation"] = "SCHEMA_CONSTRAINED_FALSE"
        evidence["diagnostic_classification"] = "NFL_REGISTRY_PUBLICATION_AUTHORITY_CONFLICT"
    if not isinstance(assessment.get("graded_n"), int):
        blockers.append("NFL_FORWARD_HEALTH_COHORT_MALFORMED")
    if not isinstance(assessment.get("minimum_forward_required"), int):
        blockers.append("NFL_FORWARD_HEALTH_COHORT_MALFORMED")
    elif assessment["minimum_forward_required"] < 100:
        blockers.append("NFL_FORWARD_MINIMUM_COHORT_INVALID")
    if isinstance(assessment.get("graded_n"), int) and isinstance(minimum, int) and not assessment["graded_n"] >= minimum:
        blockers.append("NFL_FORWARD_HEALTH_GRADED_N_BELOW_MINIMUM")
    if assessment.get("status") != "PASS":
        blockers.append("NFL_FORWARD_HEALTH_STATUS_NOT_PASS")
    if assessment.get("certification_recommendation") != "FORWARD_EVIDENCE_PASS":
        blockers.append("NFL_FORWARD_RECOMMENDATION_NOT_PASS")
    if assessment.get("blockers") != []:
        blockers.append("NFL_FORWARD_HEALTH_BLOCKERS_PRESENT")
    return not blockers, sorted(set(blockers)), evidence


def _forward_certification_proven(db: Any, row: Mapping[str, Any]) -> bool:
    """Boolean-only view of the forward-certification decision."""
    proven, _blockers, _evidence = _forward_certification_diagnosis(db, row)
    return proven


def attach_nfl_daily_stage_receipt(*, db: Any, req: Any, governed: Mapping[str, Any]) -> dict[str, Any]:
    """Attach canonical fitted package only after exact prediction readback.

    Caller must still enforce SQL governance, final refresh and terminal reducer.
    Those are independent gates, never replaced by this evidence.
    """
    out = dict(governed)
    reasons: list[str] = []
    prediction_id = str(out.get("event_prediction_id") or "")
    snapshot_id = str(out.get("score_snapshot_id") or "")
    if not prediction_id or not snapshot_id:
        reasons.append("NFL_STAGE_PROOF_PREDICTION_ID_MISSING")
        rows: list[dict[str, Any]] = []
    else:
        try:
            query = (
                db.table("wow_nfl_event_predictions")
                .select("*")
                .eq("event_prediction_id", prediction_id)
                .limit(2)
                .execute()
            )
            rows = [dict(row) for row in query.data] if isinstance(query.data, list) else []
        except Exception:
            rows = []
            reasons.append("NFL_STAGE_PROOF_READBACK_FAILED")

    if len(rows) != 1:
        reasons.append("NFL_STAGE_PROOF_EXACT_ROW_UNAVAILABLE")
    if not reasons:
        row = rows[0]
        identity_checks = (
            ("official_event_id", getattr(req, "official_event_id", None)),
            ("event_key", getattr(req, "event_key", None)),
            ("requested_slate_date", getattr(req, "requested_slate_date", None)),
            ("selected_participant", out.get("selected_participant")),
            ("source_snapshot_id", getattr(req, "source_snapshot_id", None)),
            ("score_snapshot_id", snapshot_id),
            ("model_artifact_id", out.get("model_artifact_id")),
            ("model_version", out.get("model_version")),
            ("calibration_method", out.get("calibration_method")),
            ("calibration_version", out.get("calibration_version")),
        )
        for field, expected in identity_checks:
            if expected in (None, "") or str(row.get(field) or "") != str(expected):
                reasons.append("NFL_STAGE_PROOF_" + field.upper() + "_MISMATCH")

        for field, expected in (
            ("event_start_time_utc", getattr(req, "event_start_time_utc", None)),
            ("source_snapshot_timestamp", getattr(req, "latest_material_update_timestamp", None)),
            ("model_timestamp", out.get("model_timestamp")),
        ):
            if not _same_instant(row.get(field), expected):
                reasons.append("NFL_STAGE_PROOF_" + field.upper() + "_MISMATCH")

        model_at = _instant(row.get("model_timestamp"))
        material_at = _instant(row.get("latest_material_update_timestamp"))
        if model_at is None or material_at is None or model_at < material_at:
            reasons.append("NFL_STAGE_PROOF_STALE_MODEL")

        if any(row.get(flag) is not True for flag in (
            "model_package_valid", "provenance_complete", "model_probability_publishable"
        )) or row.get("can_execute") is not False:
            reasons.append("NFL_STAGE_PROOF_MODEL_PACKAGE_NOT_CERTIFIED")

        p, lower, upper = (_number(row.get(name)) for name in (
            "calibrated_selection_probability",
            "calibrated_selection_lower_bound",
            "calibrated_selection_upper_bound",
        ))
        if (p is None or lower is None or upper is None
                or not 0 <= lower <= p <= upper <= 1):
            reasons.append("NFL_STAGE_PROOF_CALIBRATED_BOUNDS_INVALID")

        # NFL currently exposes home/away upper bounds, not a generic
        # selected-side upper-bound field. Resolve only by the exact selected
        # team and never borrow the opposite side's bound.
        selected = str(out.get("selected_participant") or "")
        if selected == str(getattr(req, "home_team", "") or ""):
            selected_upper = out.get("calibrated_home_upper_bound")
        elif selected == str(getattr(req, "away_team", "") or ""):
            selected_upper = out.get("calibrated_away_upper_bound")
        else:
            selected_upper = None
        explicit_upper = out.get("calibrated_selection_upper_bound")
        if explicit_upper is not None:
            selected_upper = explicit_upper
        for field, outer in (
            ("calibrated_selection_probability", out.get("calibrated_selection_probability")),
            ("calibrated_selection_lower_bound", out.get("rank_calibrated_lower_bound")),
            ("calibrated_selection_upper_bound", selected_upper),
        ):
            if not _same_number(row.get(field), outer):
                reasons.append("NFL_STAGE_PROOF_" + field.upper() + "_MISMATCH")

        training_n = row.get("calibration_training_n")
        if (str(row.get("calibration_health_status") or "").upper() != "PASS"
                or not row.get("calibration_method")
                or not row.get("calibration_version")
                or not row.get("uncertainty_method")
                or not isinstance(training_n, int) or training_n <= 0):
            reasons.append("NFL_STAGE_PROOF_DYNAMIC_CALIBRATION_UNPROVEN")

    # Numerical-package proof and subsequent operation proof are separate.
    # Missing calibration must not erase a valid immutable fitted probability.
    stage_reasons: list[str] = []
    producer_blocker = _actual_producer_receipt(rows[0]) if not reasons else None
    if producer_blocker:
        stage_reasons.append(producer_blocker)
    calibration_proven = not reasons and not producer_blocker
    refresh_proven = calibration_proven and _final_refresh_proven(rows[0], out)
    if calibration_proven and not refresh_proven:
        stage_reasons.append("NFL_FINAL_REFRESH_SOURCE_PROOF_UNPROVEN")
    stage_proven = calibration_proven and refresh_proven
    forward_blockers: list[str] = []
    forward_evidence: dict[str, Any] = {}
    if stage_proven:
        certified_forward, forward_blockers, forward_evidence = _forward_certification_diagnosis(
            db, rows[0]
        )
    else:
        certified_forward = False
    upstream_approved = (
        out.get("rank_eligible") is True
        and out.get("probability_publishable") is True
        and out.get("terminal_label") == "FINAL_APPROVED"
        and out.get("global_terminal_authority") == "V17_TERMINAL_REDUCER"
        and out.get("can_execute") is False
    )

    # A readable persisted score is NOT a reducer decision. Preserve upstream
    # eligibility solely as a diagnostic; never let this adapter publish a row
    # with an absent controlling dynamic-calibration producer receipt.
    if out.get("rank_eligible") is True:
        out["rank_eligible_pre_audit"] = True
    if out.get("terminal_label") not in (None, "MODEL_QUALIFIED_HOLD"):
        out["upstream_terminal_label"] = out["terminal_label"]
    out["rank_eligible"] = False
    out["probability_publishable"] = False
    out["terminal_label"] = "MODEL_QUALIFIED_HOLD"
    out["terminal_ceiling"] = "MODEL_QUALIFIED_HOLD"
    out["can_execute"] = False

    if reasons:
        # Never adopt a success boolean from an earlier source or a prior event.
        out.pop("probability_package_valid", None)
        out["dynamic_calibration_complete"] = False
        out["calibration_status"] = "UNPROVEN"
        out["nfl_stage_receipt"] = {
            "status": "HOLD",
            "source": STAGE_SOURCE,
            "prediction_id": prediction_id or None,
            "blockers": sorted(set(reasons)),
            "can_execute": False,
        }
        out["can_execute"] = False
        return out

    # This branch requires real fitted-producer operation proof and a separate,
    # identity-bound final schedule re-query. Forward certification is a
    # different accuracy requirement: 53 actual graded games cannot become 100
    # through a stage-proof patch.
    allowed = bool(upstream_approved and stage_proven and certified_forward)
    out.update({
        "prediction_id": prediction_id,
        "immutable_model_timestamp": _instant(row["model_timestamp"]).isoformat(),
        "calibrated_probability": p,
        "calibrated_lower_bound": lower,
        "calibrated_upper_bound": upper,
        "source_snapshot_id": str(row["source_snapshot_id"]),
        "source_snapshot_timestamp": _instant(row["source_snapshot_timestamp"]).isoformat(),
        "outcome_space": "NFL_FULL_GAME_HOME_AWAY",
        "probability_package_valid": True,
        "dynamic_calibration_complete": calibration_proven,
        "calibration_status": "PASS" if calibration_proven else "UNPROVEN",
        "rank_eligible": allowed,
        "probability_publishable": allowed,
        "terminal_label": "FINAL_APPROVED" if allowed else "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "FINAL_APPROVED" if allowed else "MODEL_QUALIFIED_HOLD",
        "nfl_stage_receipt": {
            "status": "PASS" if stage_proven else "HOLD",
            "package_readback_status": "PASS",
            "dynamic_calibration_stage": (
                "PASS" if calibration_proven else "EVIDENCE_NOT_EMITTED"
                if producer_blocker == "NFL_DYNAMIC_CALIBRATION_PRODUCER_RECEIPT_MISSING"
                else "EVIDENCE_INVALID"
            ),
            "final_refresh_stage": "PASS" if refresh_proven else "HOLD",
            "forward_certification_status": (
                "NOT_CHECKED" if not stage_proven else "PASS" if certified_forward else "HOLD"
            ),
            "forward_certification_blockers": list(forward_blockers),
            "forward_certification_evidence": dict(forward_evidence),
            "blockers": stage_reasons,
            "source": STAGE_SOURCE,
            "prediction_id": prediction_id,
            "score_snapshot_id": snapshot_id,
            "official_event_id": str(row["official_event_id"]),
            "model_timestamp": _instant(row["model_timestamp"]).isoformat(),
            "source_snapshot_id": str(row["source_snapshot_id"]),
            "calibration_version": str(row["calibration_version"]),
            "can_execute": False,
        },
        "can_execute": False,
    })
    if not allowed:
        out["blockers"] = sorted(set([
            *(str(value) for value in out.get("blockers", [])),
            *stage_reasons,
            *forward_blockers,
            "NFL_FORWARD_CERTIFICATION_UNPROVEN" if stage_proven and not certified_forward
            else "NFL_UPSTREAM_TERMINAL_UNPROVEN" if stage_proven else "NFL_STAGE_PROOF_UNPROVEN",
        ]))
    return out
