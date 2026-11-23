"""SIRT_MODEL_DELIVERY_AUDIT: independent, read-only, fail-closed evidence reducer.

SIRT never generates, relabels, substitutes or publishes a probability, never
certifies a model and never approves a merge/deploy. This module only reduces
already-collected evidence into a dated audit:

* a sport x market x phase capability census across the governed stages;
* out-of-time forecast quality on immutable, settled, identity-matched rows;
* an exact-SHA independent semantic review verdict gate.

Missing evidence is ``UNKNOWN_NOT_VERIFIED`` / ``NOT_YET_ESTIMABLE`` /
``INDEPENDENT_SEMANTIC_UNVERIFIED`` -- never PASS.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

AUDIT_TYPE = "SIRT_MODEL_DELIVERY_AUDIT"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False

CANONICAL_SPORTS = (
    "MLB", "NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "NHL",
    "SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET",
)
MARKETS = ("ML", "SPREAD", "PROPS")
PHASES = ("PREGAME", "LIVE")
STAGES = (
    "DISCOVERY", "CANONICAL_IDENTITY", "CURRENT_HYDRATION", "FITTED_SPECIALIST",
    "FAILURE_REGIMES", "CALIBRATION_BOUND", "CERTIFICATION", "ACTION",
    "EXACT_ONCE", "TERMINAL_REDUCER", "USER_PATH",
)
CELL_STATUSES = ("VERIFIED", "SHADOW", "BLOCKED_TYPED", "NO_EVENTS", "UNKNOWN_NOT_VERIFIED")
# SHADOW is only meaningful once a fitted specialist actually ran.
SHADOW_ALLOWED_FROM = STAGES.index("CALIBRATION_BOUND")
GENERIC_BLOCKERS = {"", "ERROR", "FAILED", "FAILURE", "UNKNOWN", "EXCEPTION", "INTERNAL_ERROR", "GENERIC_FAILURE"}
TYPED_CODE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_EVIDENCE_MAX_AGE = timedelta(hours=36)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("SIRT_CLOCK_MUST_BE_AWARE")
    return value.astimezone(timezone.utc)


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _cell(sport: str, market: str, phase: str, status: str, blocker: str,
          stage: str | None, source: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    source = source or {}
    row = {
        "sport": sport, "market": market, "phase": phase, "status": status,
        "first_failing_stage": stage, "canonical_blocker": blocker or None,
        "candidate_count": _int(source.get("candidate_count")),
        "event_count": _int(source.get("event_count")),
        "model_artifact": source.get("model_artifact"),
        "model_version": source.get("model_version"),
        "artifact_sha256": source.get("artifact_sha256"),
        "certification_id": source.get("certification_id"),
        "action_receipt_id": source.get("action_receipt_id"),
        "prediction_id": source.get("prediction_id"),
        "source_timestamp": source.get("source_timestamp"),
        "verified_environment": source.get("verified_environment"),
        "deployed_sha": source.get("deployed_sha"),
        "owning_issue": source.get("owning_issue"),
        "next_diagnostic": source.get("next_diagnostic"),
    }
    row.update(extra)
    return row


def assess_capability_cell(cell: dict[str, Any] | None, *, sport: str, market: str, phase: str,
                           now: datetime | None = None,
                           evidence_max_age: timedelta = DEFAULT_EVIDENCE_MAX_AGE) -> dict[str, Any]:
    """Classify one sport/market/phase cell; never upgrades on absent evidence."""
    observed = _now(now)
    if not cell:
        return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "EVIDENCE_UNAVAILABLE", STAGES[0])
    if cell.get("can_execute") is not False or cell.get("terminal_authority") != TERMINAL_AUTHORITY:
        return _cell(sport, market, phase, "BLOCKED_TYPED", "GOVERNANCE_INVARIANT_UNVERIFIED", None, cell)
    stages = cell.get("stages") if isinstance(cell.get("stages"), dict) else {}
    for index, stage in enumerate(STAGES):
        entry = stages.get(stage) if isinstance(stages.get(stage), dict) else {}
        status = _norm(entry.get("status"))
        ref = str(entry.get("evidence_ref") or "").strip()
        if status == "PASS" and ref:
            continue
        if not ref or status in {"", "EVIDENCE_UNAVAILABLE", "NOT_RUN", "UNKNOWN"}:
            return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "EVIDENCE_UNAVAILABLE", stage, cell)
        if status == "NO_EVENTS":
            # NO_EVENTS needs an authoritative schedule and a real zero denominator.
            if (stage == "DISCOVERY" and cell.get("authoritative_schedule_ref")
                    and _int(cell.get("event_count")) == 0):
                return _cell(sport, market, phase, "NO_EVENTS", "NO_EVENTS", stage, cell)
            return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "NO_EVENTS_SCHEDULE_UNVERIFIED", stage, cell)
        if status == "SHADOW":
            if index >= SHADOW_ALLOWED_FROM and SHA256.fullmatch(str(cell.get("artifact_sha256") or "")):
                return _cell(sport, market, phase, "SHADOW", "NOT_CERTIFIED_FOR_PUBLICATION", stage, cell)
            return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "SHADOW_CLAIM_UNSUPPORTED", stage, cell)
        if status in {"FAIL", "BLOCKED"}:
            code = str(entry.get("blocker_code") or "").strip()
            if code in GENERIC_BLOCKERS or not TYPED_CODE.fullmatch(code):
                return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "UNTYPED_FAILURE", stage, cell)
            return _cell(sport, market, phase, "BLOCKED_TYPED", code, stage, cell)
        return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "STAGE_STATUS_INVALID", stage, cell)
    # Every stage passed: VERIFIED still requires a full production receipt chain.
    missing = []
    if not SHA256.fullmatch(str(cell.get("artifact_sha256") or "")):
        missing.append("ARTIFACT_SHA256")
    for key in ("model_artifact", "model_version", "certification_id", "action_receipt_id", "prediction_id"):
        if not str(cell.get(key) or "").strip():
            missing.append(key.upper())
    if not SHA40.fullmatch(str(cell.get("deployed_sha") or "")):
        missing.append("DEPLOYED_SHA")
    if _norm(cell.get("verified_environment")) != "PRODUCTION":
        missing.append("PRODUCTION_ENVIRONMENT")
    if (_int(cell.get("candidate_count")) or 0) <= 0 or (_int(cell.get("event_count")) or 0) <= 0:
        missing.append("POSITIVE_DENOMINATOR")
    if missing:
        return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "RECEIPT_INCOMPLETE", None, cell,
                     missing_receipts=missing)
    stamp = _time(cell.get("source_timestamp"))
    if stamp is None or stamp > observed + timedelta(minutes=2):
        return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "SOURCE_TIMESTAMP_UNVERIFIABLE", None, cell)
    if observed - stamp > evidence_max_age:
        return _cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED", "EVIDENCE_STALE", None, cell)
    return _cell(sport, market, phase, "VERIFIED", "", None, cell)


def build_model_delivery_audit(cells: list[dict[str, Any]], *, registered_sports: list[str] | tuple[str, ...] = (),
                               now: datetime | None = None,
                               evidence_max_age: timedelta = DEFAULT_EVIDENCE_MAX_AGE) -> dict[str, Any]:
    """Full matrix; every declared/observed sport appears, absent cells are UNKNOWN."""
    observed = _now(now)
    sports = list(CANONICAL_SPORTS)
    extras = [_norm(s) for s in registered_sports] + [_norm(c.get("sport")) for c in cells if isinstance(c, dict)]
    for sport in extras:
        if sport and sport not in sports:
            sports.append(sport)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    invalid = 0
    for cell in cells:
        key = (_norm(cell.get("sport")), _norm(cell.get("market")), _norm(cell.get("phase"))) if isinstance(cell, dict) else None
        if not key or key[1] not in MARKETS or key[2] not in PHASES or not key[0]:
            invalid += 1
            continue
        grouped[key].append(cell)
    matrix = []
    for sport in sports:
        for market in MARKETS:
            for phase in PHASES:
                found = grouped.get((sport, market, phase), [])
                if len(found) > 1:
                    matrix.append(_cell(sport, market, phase, "UNKNOWN_NOT_VERIFIED",
                                        "DUPLICATE_CELL_EVIDENCE", None))
                    continue
                matrix.append(assess_capability_cell(found[0] if found else None, sport=sport, market=market,
                                                     phase=phase, now=observed, evidence_max_age=evidence_max_age))
    counts = {status: sum(1 for row in matrix if row["status"] == status) for status in CELL_STATUSES}
    blockers: dict[str, int] = defaultdict(int)
    for row in matrix:
        if row["status"] in {"BLOCKED_TYPED", "UNKNOWN_NOT_VERIFIED"}:
            blockers[str(row["canonical_blocker"])] += 1
    return {
        "audit_type": AUDIT_TYPE,
        "generated_at": observed.isoformat(),
        "scope": "INDEPENDENT_EVIDENCE_ONLY_NOT_RELEASE_OR_MODEL_APPROVAL",
        "sports": sports,
        "matrix": matrix,
        "status_counts": counts,
        "blocker_counts": dict(sorted(blockers.items(), key=lambda kv: (-kv[1], kv[0]))),
        "invalid_cell_records": invalid,
        "self_approval": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


# --------------------------------------------------------------------------- quality

def _prob(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0.0 < value < 1.0 else None


def _exclusion(row: dict[str, Any]) -> str | None:
    if row.get("immutable") is not True:
        return "FORECAST_NOT_IMMUTABLE"
    predicted, start = _time(row.get("prediction_ts")), _time(row.get("event_start_ts"))
    if predicted is None or start is None:
        return "TIMESTAMP_UNVERIFIABLE"
    if predicted >= start:
        return "POST_START_LEAKAGE"
    for left, right in (("event_id", "outcome_event_id"), ("market_key", "outcome_market_key"),
                        ("period", "outcome_period"), ("side", "outcome_side")):
        if not str(row.get(left) or "").strip() or _norm(row.get(left)) != _norm(row.get(right)):
            return "IDENTITY_MISMATCH"
    line, settled_line = row.get("line"), row.get("outcome_line")
    if line is not None or settled_line is not None:
        try:
            if float(line) != float(settled_line):
                return "LINE_MISMATCH"
        except (TypeError, ValueError):
            return "LINE_MISMATCH"
    if _norm(row.get("market")) == "SPREAD" and (line is None or row.get("line_convention") != "SIGNED_SIDE_LINE"):
        return "SPREAD_LINE_UNSIGNED"
    if _prob(row.get("probability")) is None:
        return "MALFORMED_PROBABILITY"
    outcome = _norm(row.get("outcome"))
    if _norm(row.get("sport")) == "SOCCER" and _norm(row.get("market")) == "ML":
        if _norm(row.get("side")) not in {"HOME", "DRAW", "AWAY"}:
            return "SOCCER_THREE_WAY_SIDE_INVALID"
        if outcome == "PUSH":
            return "SOCCER_DRAW_NOT_A_PUSH"
    if outcome in {"PUSH", "VOID"}:
        return outcome
    if outcome not in {"WIN", "LOSS"}:
        return "UNSETTLED"
    settled = _time(row.get("settled_ts"))
    if settled is None or settled < start:
        return "SETTLEMENT_UNVERIFIABLE"
    return None


def _metrics(probs: list[float], hits: list[int], bins: int) -> dict[str, Any]:
    n = len(probs)
    errors = [(p - y) ** 2 for p, y in zip(probs, hits)]
    brier = sum(errors) / n
    var = sum((e - brier) ** 2 for e in errors) / (n - 1) if n > 1 else 0.0
    log_loss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(probs, hits)) / n
    diagram, ece = [], 0.0
    for b in range(bins):
        members = [(p, y) for p, y in zip(probs, hits) if min(int(p * bins), bins - 1) == b]
        if not members:
            continue
        mean_p = sum(p for p, _ in members) / len(members)
        rate = sum(y for _, y in members) / len(members)
        ece += len(members) / n * abs(mean_p - rate)
        diagram.append({"bin": b, "count": len(members), "mean_probability": mean_p, "hit_rate": rate})
    mean_p = sum(probs) / n
    return {
        "brier": brier, "brier_standard_error": math.sqrt(var / n),
        "log_loss": log_loss, "ece": ece, "reliability_diagram": diagram,
        "base_rate": sum(hits) / n, "mean_probability": mean_p,
        "sharpness_variance": sum((p - mean_p) ** 2 for p in probs) / n,
    }


def assess_forecast_quality(rows: list[dict[str, Any]], *, min_cohort: int = 100, bins: int = 10) -> dict[str, Any]:
    """Out-of-time quality by sport x market x cohort; coverage reported separately."""
    if min_cohort < 2 or bins < 1:
        raise ValueError("SIRT_QUALITY_PARAMETERS_INVALID")
    excluded: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    eligible: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        forecast_id = str(row.get("forecast_id") or "").strip()
        if not forecast_id:
            excluded["FORECAST_ID_MISSING"] += 1
            continue
        if forecast_id in seen:
            excluded["DUPLICATE_FORECAST"] += 1
            continue
        seen.add(forecast_id)
        reason = _exclusion(row)
        if reason:
            excluded[reason] += 1
            continue
        key = (_norm(row.get("sport")), _norm(row.get("market")), str(row.get("cohort") or "UNSPECIFIED"))
        eligible[key].append(row)
    cohorts = []
    for (sport, market, cohort), members in sorted(eligible.items()):
        entry: dict[str, Any] = {"sport": sport, "market": market, "cohort": cohort, "n": len(members)}
        if len(members) < min_cohort:
            entry["status"] = "NOT_YET_ESTIMABLE"
            cohorts.append(entry)
            continue
        probs = [float(r["probability"]) for r in members]
        hits = [1 if _norm(r["outcome"]) == "WIN" else 0 for r in members]
        entry.update(status="ESTIMATED", **_metrics(probs, hits, bins))
        bounds = [_prob(r.get("lower_bound")) for r in members]
        if all(b is not None and b <= p for b, p in zip(bounds, probs)):
            mean_bound = sum(bounds) / len(bounds)  # type: ignore[arg-type]
            entry["lower_bound"] = {"mean_lower_bound": mean_bound,
                                    "respected": entry["base_rate"] >= mean_bound}
        else:
            entry["lower_bound"] = "NOT_YET_ESTIMABLE"
        baseline = [_prob(r.get("baseline_probability")) for r in members]
        if all(b is not None for b in baseline):
            entry["baseline_identical_cohort"] = {
                "brier": sum((b - y) ** 2 for b, y in zip(baseline, hits)) / len(hits),  # type: ignore[operator]
                "n": len(hits),
            }
        else:
            entry["baseline_identical_cohort"] = "BASELINE_NOT_ON_IDENTICAL_COHORT"
        cohorts.append(entry)
    return {
        "coverage": {
            "rows_in": len(rows),
            "eligible_scored": sum(len(v) for v in eligible.values()),
            "excluded": dict(sorted(excluded.items())),
        },
        "cohorts": cohorts,
        "probability_source": "IMMUTABLE_FITTED_FORECASTS_ONLY_NO_SUBSTITUTION",
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


# --------------------------------------------------------------------------- review

REVIEW_VERDICTS = ("REVIEW_PASS", "REVIEW_REJECT_ENGINEERING", "REVIEW_BLOCKED_HARD_BOUNDARY")
UNVERIFIED = "INDEPENDENT_SEMANTIC_UNVERIFIED"
ESCALATION_ISSUE = "gregoryharper84-ship-it/WOW-Dashboard#2136"
BOT_LOGINS = {"coderabbitai", "github-actions", "copilot", "copilot-swe-agent", "dependabot", "codecov"}
NON_SEMANTIC_SOURCES = {"CHECK_RUN", "CI", "STATUS", "INTAKE_ISSUE", "AUTHOR_COMMENT", "APP_CHECK"}


def _login(value: Any) -> str:
    login = str(value or "").strip().lower()
    return login[:-5] if login.endswith("[bot]") else login


def _independent(review: Any, author: str, head: str, *, require_evidence: bool) -> str | None:
    if not isinstance(review, dict):
        return "NO_INDEPENDENT_REVIEW"
    reviewer = _login(review.get("reviewer"))
    if not reviewer or reviewer == author:
        return "SELF_OR_ANONYMOUS_REVIEW"
    if _norm(review.get("reviewer_type")) != "USER" or reviewer in BOT_LOGINS:
        return "BOT_REVIEW_NOT_SEMANTIC"
    if _norm(review.get("source")) in NON_SEMANTIC_SOURCES or not review.get("source"):
        return "NON_SEMANTIC_SOURCE"
    if str(review.get("commit_sha") or "").lower() != head:
        return "STALE_OR_MISMATCHED_SHA"
    if require_evidence and (review.get("full_diff_reviewed") is not True
                             or not review.get("evidence_refs") or not review.get("counterexample_tests")):
        return "PASS_EVIDENCE_INCOMPLETE"
    return None


def assess_semantic_review(*, pr_author: str, current_head_sha: str, change_class: str,
                           review: dict[str, Any] | None,
                           architect_review: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attributable exact-SHA verdict; checks, bots, intake or author text never count."""
    head = str(current_head_sha or "").lower()
    author = _login(pr_author)
    klass = _norm(change_class)
    reason = None
    verdict = _norm((review or {}).get("verdict"))
    if not SHA40.fullmatch(head) or not author:
        reason = "PR_IDENTITY_UNVERIFIED"
    elif verdict not in REVIEW_VERDICTS:
        reason = "VERDICT_INVALID" if review else "NO_INDEPENDENT_REVIEW"
    else:
        reason = _independent(review, author, head, require_evidence=verdict == "REVIEW_PASS")
    if reason is None and verdict == "REVIEW_PASS" and klass == "P":
        if _norm((architect_review or {}).get("verdict")) != "REVIEW_PASS":
            reason = "INDEPENDENT_ARCHITECT_REVIEW_MISSING"
        else:
            reason = _independent(architect_review, author, head, require_evidence=True)
            if reason is None and _login(architect_review.get("reviewer")) == _login(review.get("reviewer")):  # type: ignore[union-attr]
                reason = "ARCHITECT_NOT_DISTINCT"
            elif reason is not None:
                reason = "ARCHITECT_" + reason
    result = verdict if reason is None else UNVERIFIED
    return {
        "verdict": result,
        "reason": reason,
        "hold": result != "REVIEW_PASS",
        "exact_head_sha": head,
        "change_class": klass,
        "escalation": ESCALATION_ISSUE if result == UNVERIFIED else None,
        "authorizes_merge_or_deploy": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WOW SIRT model delivery audit (evidence only)")
    parser.add_argument("mode", choices=("census", "quality"))
    parser.add_argument("evidence", help="JSON file: list of cell or forecast rows")
    parser.add_argument("--registered-sport", action="append", default=[])
    parser.add_argument("--min-cohort", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        with open(args.evidence, encoding="utf-8") as handle:
            rows = json.load(handle)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError("SIRT_EVIDENCE_NOT_A_LIST_OF_OBJECTS")
        if args.mode == "census":
            result = build_model_delivery_audit(rows, registered_sports=args.registered_sport)
        else:
            result = assess_forecast_quality(rows, min_cohort=args.min_cohort)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "EVIDENCE_UNAVAILABLE", "reason": type(exc).__name__,
                          "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
