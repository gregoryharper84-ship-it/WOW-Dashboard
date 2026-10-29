"""Read-only cross-sport production acceptance matrix (#1935 / #1896 / #1950 / #1951).

Consumes a *captured* evidence bundle (``V17_PRODUCTION_ACCEPTANCE_EVIDENCE_V1``, produced by
the read-only SQL in ``acceptance/V17_PRODUCTION_ACCEPTANCE_QUERIES.sql`` and Render/GitHub
reads) and reports, per route, the first failing production stage using the same ordered stages
as ``v17.model_acceptance_harness``.

Auditor only: never scores, never recomputes or substitutes a probability, never writes, never
opens a network connection, never promotes an artifact or flips a flag. ``can_execute`` is False.
A row that is correctly HELD is reported as HELD with its exact blocker -- not as a defect -- and a
route is ``ready`` only when every stage is clean, a row is genuinely COMPLETED, and the
environment (GitHub/Render SHA parity, production DB binding) is proven.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from v17.model_acceptance_harness import CAN_EXECUTE, MODEL_FAILURES, STAGES, redact

MATRIX_VERSION = "V17_PRODUCTION_ACCEPTANCE_MATRIX_V1"
EVIDENCE_SCHEMA = "V17_PRODUCTION_ACCEPTANCE_EVIDENCE_V1"
SOURCE_MAX_AGE = timedelta(hours=24)  # mirrors nfl_team_event_specialist._NFL_SCHEDULE_MAX_AGE
LOWER_GAP_FLOOR = -0.03  # mirrors nfl_forward_shadow.py (lower_gap < -0.03 blocks certification)
PUBLISHED_TERMINALS = frozenset({"COMPLETED", "FINAL_APPROVED", "OFFICIAL_QUALIFIED"})
_SKIP_STAGES = ("ENVIRONMENT",)  # environment is reported separately, never as "first failing model stage"
MODEL_STAGE_ORDER = tuple(s for s in STAGES if s not in _SKIP_STAGES)


def _t(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _f(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def environment_blockers(ev: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    main = (ev.get("github") or {}).get("main_sha")
    render = ev.get("render") or {}
    engine = (render.get("engine") or {}).get("live_sha")
    worker = (render.get("worker") or {}).get("live_sha")
    if not (main and engine and worker):
        out.append("ENVIRONMENT_SHA_EVIDENCE_MISSING")
    else:
        if engine != main:
            out.append("RENDER_ENGINE_SHA_NOT_GITHUB_MAIN")
        if worker != engine:
            out.append("RENDER_WEB_WORKER_SHA_MISMATCH")
    if (ev.get("database") or {}).get("production_binding") != "PROVEN":
        out.append("PRODUCTION_DB_BINDING_UNPROVEN")
    return out


def _row_blockers(route: Mapping[str, Any], row: Mapping[str, Any], champion: Mapping[str, Any] | None):
    b: list[tuple[str, str]] = []
    if not row.get("official_event_id") or row.get("code") == "EVENT_IDENTITY_UNRESOLVED":
        b.append(("IDENTITY", "EVENT_IDENTITY_UNRESOLVED"))
    model_ts, src_ts, start = _t(row.get("model_timestamp")), _t(row.get("source_snapshot_timestamp")), _t(row.get("event_start_time_utc"))
    if model_ts and start and model_ts >= start:
        b.append(("LIFECYCLE", "MODEL_AFTER_EVENT_START"))
    if model_ts and src_ts and model_ts - src_ts > SOURCE_MAX_AGE:
        b.append(("LIFECYCLE", "SOURCE_SNAPSHOT_STALE"))
    if row.get("model_invoked") is False or row.get("bucket") == "MODEL_UNAVAILABLE":
        b.append(("MODEL", "MODEL_UNAVAILABLE"))
        return b
    code = row.get("code")
    if code in MODEL_FAILURES:
        b.append(("MODEL", code))
        return b
    if champion is not None:
        if row.get("model_artifact_id") != champion.get("artifact_id"):
            b.append(("MODEL", "ROW_ARTIFACT_NOT_ACTIVE_CHAMPION"))
        elif row.get("artifact_checksum") and row.get("artifact_checksum") != champion.get("checksum"):
            b.append(("MODEL", "ROW_ARTIFACT_CHECKSUM_MISMATCH"))
    p, lo = _f(row.get("calibrated_probability")), _f(row.get("calibrated_lower_bound"))
    if p is None or lo is None or not (0.0 < lo <= p < 1.0):
        b.append(("CALIBRATION", "CALIBRATED_BOUND_INVALID"))
    if not row.get("prediction_id") or row.get("prediction_receipt_found") is not True:
        b.append(("RECEIPT", "IMMUTABLE_PREDICTION_RECEIPT_MISSING"))
    return b


def evaluate_route(route: Mapping[str, Any], env_blockers: Sequence[str], ev: Mapping[str, Any]) -> dict[str, Any]:
    reg = route.get("registry") or {}
    champions = list(reg.get("active_promoted") or [])
    champion = champions[0] if len(champions) == 1 else None
    rows = list((route.get("latest_run") or {}).get("rows") or [])
    blockers: list[tuple[str, str]] = []
    findings: list[str] = []
    notes: list[str] = []

    # MODEL registry state (route-level, before any row)
    if int(reg.get("artifact_count") or 0) == 0:
        blockers.append(("MODEL", "MODEL_UNAVAILABLE"))
        findings.append("FITTED_ARTIFACT_ABSENT")
    elif not champions:
        blockers.append(("MODEL", "MODEL_UNAVAILABLE"))
        findings.append("ARTIFACTS_PRESENT_NONE_ACTIVE_PROMOTED")
    elif len(champions) > 1:
        blockers.append(("MODEL", "MODEL_UNAVAILABLE"))
        findings.append("CHAMPION_AMBIGUOUS")
    if route.get("calibration", {}).get("active_calibrator_count") == 0:
        blockers.append(("CALIBRATION", "ACTIVE_CALIBRATOR_ABSENT"))
    if route.get("features", {}).get("hydration_available") is False:
        blockers.append(("MODEL", "MODEL_INPUTS_INSUFFICIENT"))
        findings.append(route["features"].get("reason") or "FEATURE_HYDRATION_UNAVAILABLE")

    if not rows:
        (blockers if champion else notes).append(("ROUTE", "NO_ROWS_IN_LATEST_RUN") if champion else "NO_ROWS_IN_LATEST_RUN_SLATE_WINDOW")
    for row in rows:
        blockers.extend(_row_blockers(route, row, champion))

    invoked = [r for r in rows if r.get("model_invoked") is True]
    # forward calibration (only meaningful when a fitted model has actually produced rows)
    health = (route.get("calibration") or {}).get("forward_health")
    if health and invoked:
        n, need = health.get("graded_n"), health.get("minimum_forward_required")
        if isinstance(n, int) and isinstance(need, int) and n < need:
            blockers.append(("CALIBRATION", "FORWARD_CALIBRATION_INSUFFICIENT_N"))
        elif health.get("status") != "PASS":
            blockers.append(("CALIBRATION", "FORWARD_CALIBRATION_NOT_PASS"))
        gap = _f(health.get("lower_bound_reliability_gap"))
        if gap is not None and gap < LOWER_GAP_FLOOR:
            findings.append("FORWARD_LOWER_BOUND_OVERCONFIDENT_WOULD_STILL_BLOCK_AT_MIN_N")

    # PUBLICATION: artifact certification vs row flag vs controlling terminal vs consumers
    cert = champion.get("probability_publishable") if champion else None
    if champion is not None and cert is not True:
        blockers.append(("PUBLICATION", "ARTIFACT_NOT_CERTIFIED_FOR_PUBLICATION"))
        if any(r.get("row_probability_publishable") is True for r in invoked):
            findings.append("ARTIFACT_ROW_PUBLISHABILITY_MISMATCH")
        for r in invoked:
            if r.get("rank_eligible") is True or r.get("final_terminal") in PUBLISHED_TERMINALS:
                blockers.append(("PUBLICATION", "PUBLISHED_FROM_UNCERTIFIED_ARTIFACT"))
                break
        for c in route.get("consumers") or []:
            if c.get("status") == "PUBLISHED":
                (blockers if c.get("observed") is True else findings).append(
                    ("PUBLICATION", "CONSUMER_PUBLISHED_FROM_UNCERTIFIED_ARTIFACT") if c.get("observed") is True
                    else f"CONSUMER_TRUSTS_ROW_FLAG_ONLY:{c.get('name')}")
    for r in invoked:
        if r.get("forward_certification_status") not in (None, "PASS"):
            blockers.append(("PUBLICATION", "FORWARD_CERTIFICATION_UNPROVEN"))
            break

    by_status: dict[str, int] = {}
    for r in rows:
        by_status[str(r.get("row_status"))] = by_status.get(str(r.get("row_status")), 0) + 1
    declared = (route.get("latest_run") or {}).get("rows_total")
    if isinstance(declared, int) and declared != len(rows):
        blockers.append(("RECONCILIATION", "ROW_COUNT_MISMATCH"))

    blockers = list(dict.fromkeys(blockers))
    first = next((s for s in MODEL_STAGE_ORDER if any(st == s for st, _ in blockers)), None)
    exact = next((c for st, c in blockers if st == first), None)
    codes = {c for _, c in blockers}
    if not blockers:
        status = "PROBABILITY_ACCEPTED"
    elif codes <= {"UNSUPPORTED_ROUTE_HOLD", *MODEL_FAILURES, "ACTIVE_CALIBRATOR_ABSENT", "FORWARD_CALIBRATION_INSUFFICIENT_N",
                   "FORWARD_CERTIFICATION_UNPROVEN", "ARTIFACT_NOT_CERTIFIED_FOR_PUBLICATION", "NO_ROWS_IN_LATEST_RUN"}:
        status = "HELD_TYPED"
    else:
        status = "REJECTED"
    completed = any(r.get("row_status") == "COMPLETED" for r in rows)
    ready = not blockers and not env_blockers and completed
    first_row = rows[0] if rows else {}
    return {
        "route": route["route"], "sport": route["sport"], "market": route.get("market"),
        "github_main_sha": (ev.get("github") or {}).get("main_sha"),
        "render_engine_live_sha": ((ev.get("render") or {}).get("engine") or {}).get("live_sha"),
        "render_worker_live_sha": ((ev.get("render") or {}).get("worker") or {}).get("live_sha"),
        "database": {k: (ev.get("database") or {}).get(k) for k in ("project_ref", "project_name", "production_binding")},
        "event": {k: first_row.get(k) for k in ("event_key", "official_event_id", "event_start_time_utc")},
        "model_artifact": champion or {"artifact_count": reg.get("artifact_count"), "active_promoted": 0},
        "feature_freshness": route.get("features"),
        "calibration": route.get("calibration"),
        "scorer_invocation": {"rows_total": len(rows), "rows_invoked": len(invoked),
                              "status": "INVOKED" if invoked else ("NOT_INVOKED" if rows else "NO_ROWS")},
        "calibrated_probability": first_row.get("calibrated_probability") if invoked else None,
        "calibrated_lower_bound": first_row.get("calibrated_lower_bound") if invoked else None,
        "prediction_receipt": {"prediction_id": first_row.get("prediction_id"), "found": first_row.get("prediction_receipt_found")} if invoked else None,
        "terminal": {"row_status_counts": by_status, "final_terminal": first_row.get("final_terminal")},
        "history_7d": route.get("history_7d"),
        "probability_status": status,
        "first_failing_stage": first, "exact_blocker": exact,
        "blockers": [f"{s}:{c}" for s, c in blockers],
        "provenance_findings": sorted({f if isinstance(f, str) else f"{f[0]}:{f[1]}" for f in findings}),
        "notes": notes, "environment_blockers": list(env_blockers),
        "readiness": "ready" if ready else "hold",
        "hold_cause": None if ready else (exact or (env_blockers[0] if env_blockers else "NO_COMPLETED_ROW")),
        "can_execute": CAN_EXECUTE,
    }


def build_matrix(ev: Mapping[str, Any]) -> dict[str, Any]:
    if ev.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError("EVIDENCE_SCHEMA_INVALID")
    env = environment_blockers(ev)
    matrix = [evaluate_route(r, env, ev) for r in ev.get("routes") or []]
    return redact({
        "matrix_version": MATRIX_VERSION, "dry_run_only": True, "can_execute": CAN_EXECUTE,
        "captured_at": ev.get("captured_at"), "evidence_source": ev.get("source"),
        "environment_blockers": env, "routes": matrix,
        "ready_routes": [m["route"] for m in matrix if m["readiness"] == "ready"],
        "self_certification": False,
        "note": "Evidence-derived and non-authorizing. Not production acceptance until SIRT independently refreshes it on a proven production binding.",
    })


def render_markdown(m: Mapping[str, Any]) -> str:
    lines = [f"# V17 production acceptance matrix ({m['captured_at']})", "",
             f"Environment blockers: {', '.join(m['environment_blockers']) or 'none'}", "",
             "| Route | Scorer | Artifact certified | First failing stage | Exact blocker | Readiness |", "|---|---|---|---|---|---|"]
    for r in m["routes"]:
        art = r["model_artifact"]
        lines.append(f"| {r['route']} | {r['scorer_invocation']['status']} | {art.get('probability_publishable', 'n/a')} | "
                     f"{r['first_failing_stage']} | {r['exact_blocker']} | {r['readiness']} |")
    lines += ["", "can_execute=false; dry-run only; non-authorizing."]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--json-out")
    ap.add_argument("--md-out")
    a = ap.parse_args(argv)
    matrix = build_matrix(json.loads(Path(a.evidence).read_text(encoding="utf-8")))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if a.md_out:
        Path(a.md_out).write_text(render_markdown(matrix), encoding="utf-8")
    print(json.dumps({k: matrix[k] for k in ("matrix_version", "ready_routes", "environment_blockers")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
