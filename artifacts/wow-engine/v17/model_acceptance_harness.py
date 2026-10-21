"""Dry-run-only V17 end-to-end model acceptance harness (#1935).

Evaluates captured route evidence (fixtures, or a separately authorized
post-deploy dry-run canary capture) for the four acceptance lanes:

* NFL moneyline (LLP fitted specialist)
* NFL props (passing/rushing/receiving yards)
* NBA props (POINTS, POINTS_REBOUNDS) -- typed MODEL_UNAVAILABLE expected
* NFL/NCAAF spreads (independent score-margin/cover specialist)

plus the pregame/live boundary. It is an auditor only: it never scores, never
recomputes or replaces a probability, never writes, never calls the network and
never places a wager. ``can_execute`` is always False.

The sporting-probability pass and the market/value/money audit are separate
stages; a money-audit failure never revokes a completed probability pass.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

from v17.team_event_official_publication_guard import WINNER_WATCH_MIN_LOWER_BOUND

HARNESS_VERSION = "V17_MODEL_ACCEPTANCE_HARNESS_V1"
CAN_EXECUTE = False
DRY_RUN_ONLY = True

# Stage order; first_failing_stage is the earliest stage with a blocker.
STAGES = (
    "ENVIRONMENT",
    "ROUTE",
    "IDENTITY",
    "LIFECYCLE",
    "MODEL",
    "CALIBRATION",
    "PUBLICATION",
    "RECEIPT",
    "RECONCILIATION",
)
PROBABILITY_STAGES = STAGES
MONEY_STAGE = "MONEY_AUDIT"

# Narrow V17 model-failure semantics (CLAUDE.md section 2). Never collapsed.
MODEL_FAILURES = (
    "MODEL_UNAVAILABLE",
    "MODEL_SCORER_FAILED",
    "MODEL_INPUTS_INSUFFICIENT",
    "MODEL_OUTPUT_INVALID",
)

# Acceptance floor for a publishable calibrated lower bound. Pinned to the
# official publication guard; a route may declare a stricter threshold but the
# harness rejects any declared threshold below this floor.
MIN_PUBLICATION_LOWER_BOUND = WINNER_WATCH_MIN_LOWER_BOUND

NFL_PROP_STATS = frozenset({"PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS"})
NBA_PROP_STATS = frozenset({"POINTS", "POINTS_REBOUNDS"})

ROUTES: dict[str, dict[str, Any]] = {
    "NFL_MONEYLINE": {"sport": "NFL", "market": "MONEYLINE", "specialist_kind": "TEAM_EVENT_WIN"},
    "NFL_PROP": {"sport": "NFL", "market": "PROP", "specialist_kind": "PLAYER_PROP", "stats": NFL_PROP_STATS},
    "NBA_PROP": {"sport": "NBA", "market": "PROP", "specialist_kind": "PLAYER_PROP", "stats": NBA_PROP_STATS},
    "NFL_SPREAD": {"sport": "NFL", "market": "SPREAD", "specialist_kind": "SCORE_MARGIN_COVER"},
    "NCAAF_SPREAD": {"sport": "NCAAF", "market": "SPREAD", "specialist_kind": "SCORE_MARGIN_COVER"},
}

# Coverage the fixture suite (or a canary capture) must exercise.
REQUIRED_COVERAGE = (
    ("NFL_MONEYLINE", None),
    ("NFL_PROP", "PASSING_YARDS"),
    ("NFL_PROP", "RUSHING_YARDS"),
    ("NFL_PROP", "RECEIVING_YARDS"),
    ("NBA_PROP", "POINTS"),
    ("NBA_PROP", "POINTS_REBOUNDS"),
    ("NFL_SPREAD", None),
    ("NCAAF_SPREAD", None),
)

TERMINAL_STATES = frozenset({"COMPLETED", "HELD", "REJECTED"})
RECEIPT_MAX_AGE = timedelta(hours=6)
_SECRET_KEY = re.compile(r"(token|secret|password|api[_-]?key|authorization|service[_-]?role)", re.I)
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class RouteResult:
    case_id: str
    route: str
    environment: dict[str, Any]
    action: dict[str, Any]
    canonical: dict[str, Any]
    model: dict[str, Any]
    calibration: dict[str, Any]
    probability_publishable: bool
    rank_eligible: bool
    receipt_id: str | None
    reconciliation: dict[str, Any]
    blockers: list[tuple[str, str]] = field(default_factory=list)
    money_blockers: list[str] = field(default_factory=list)
    money_applicable: bool = True

    @property
    def first_failing_stage(self) -> str | None:
        for stage in STAGES:
            if any(s == stage for s, _ in self.blockers):
                return stage
        return MONEY_STAGE if self.money_blockers else None

    @property
    def exact_blocker(self) -> str | None:
        stage = self.first_failing_stage
        if stage == MONEY_STAGE:
            return self.money_blockers[0]
        return next((code for s, code in self.blockers if s == stage), None)

    @property
    def probability_status(self) -> str:
        if not self.blockers:
            return "PROBABILITY_ACCEPTED"
        codes = {c for _, c in self.blockers}
        if codes <= {"UNSUPPORTED_ROUTE_HOLD", *MODEL_FAILURES}:
            return "HELD_TYPED"
        return "REJECTED"

    @property
    def money_status(self) -> str:
        if not self.money_applicable:
            return "MONEY_AUDIT_NOT_RUN"
        return "MONEY_AUDIT_FAILED" if self.money_blockers else "MONEY_AUDIT_PASSED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "route": self.route,
            "environment": self.environment,
            "action": self.action,
            "canonical": self.canonical,
            "model": self.model,
            "calibration": self.calibration,
            "probability_publishable": self.probability_publishable,
            "rank_eligible": self.rank_eligible,
            "receipt_id": self.receipt_id,
            "reconciliation": self.reconciliation,
            "probability_status": self.probability_status,
            "money_status": self.money_status,
            "first_failing_stage": self.first_failing_stage,
            "exact_blocker": self.exact_blocker,
            "blockers": [f"{s}:{c}" for s, c in self.blockers],
            "money_blockers": list(self.money_blockers),
            "can_execute": CAN_EXECUTE,
        }


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) else None


def redact(value: Any) -> Any:
    """Strip anything secret-shaped before it reaches a report or log."""
    if isinstance(value, Mapping):
        return {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def classify_model_outcome(http_status: Any, response: Mapping[str, Any]) -> str | None:
    """Return the typed model failure, or None when the scorer completed validly.

    HTTP 200 is transport evidence only; a 200 carrying MODEL_UNAVAILABLE is
    still MODEL_UNAVAILABLE. Each failure keeps its own type.
    """
    declared = response.get("model_status")
    if declared in MODEL_FAILURES:
        return declared
    if declared not in (None, "OK"):
        return "MODEL_OUTPUT_INVALID"
    if http_status != 200:
        return "MODEL_SCORER_FAILED"
    p = _num(response.get("probability"))
    if p is None or not 0.0 < p < 1.0:
        return "MODEL_OUTPUT_INVALID"
    return None


def _environment(env: Mapping[str, Any], receipt: Mapping[str, Any], add) -> dict[str, Any]:
    mode = env.get("mode")
    github_sha = env.get("github_sha")
    out = {
        "mode": mode,
        "environment": env.get("environment"),
        "github_sha": github_sha,
        "render_live_sha": env.get("render_live_sha"),
        "render_deploy_id": env.get("render_deploy_id"),
        "supabase_project_ref": env.get("supabase_project_ref"),
        "production_binding_evidence": env.get("production_binding_evidence"),
    }
    if env.get("dry_run") is not True or env.get("can_execute") is not False:
        add("ENVIRONMENT", "DRY_RUN_CONTRACT_VIOLATED")
    if not isinstance(github_sha, str) or not _SHA.match(github_sha):
        add("ENVIRONMENT", "GITHUB_SHA_MISSING")
    if mode == "CANARY":
        if env.get("render_live_sha") != github_sha:
            add("ENVIRONMENT", "RUN_SHA_MISMATCH")
        if not env.get("render_deploy_id") or not env.get("supabase_project_ref"):
            add("ENVIRONMENT", "PRODUCTION_BINDING_EVIDENCE_MISSING")
        if not env.get("production_binding_evidence"):
            add("ENVIRONMENT", "PRODUCTION_BINDING_EVIDENCE_MISSING")
    elif mode != "FIXTURE":
        add("ENVIRONMENT", "UNKNOWN_HARNESS_MODE")
    run_sha = receipt.get("run_sha")
    if receipt and run_sha is not None and run_sha != github_sha:
        add("ENVIRONMENT", "RUN_SHA_MISMATCH")
    return out


def evaluate_case(case: Mapping[str, Any], bundle_env: Mapping[str, Any], now: datetime) -> RouteResult:
    blockers: list[tuple[str, str]] = []

    def add(stage: str, code: str) -> None:
        if (stage, code) not in blockers:
            blockers.append((stage, code))

    env = {**bundle_env, **(case.get("environment_override") or {})}
    route = case.get("route")
    spec = ROUTES.get(route)
    req = case.get("request") or {}
    resp = case.get("response") or {}
    event = case.get("event") or {}
    receipt = case.get("receipt") or {}
    rows = case.get("rows") or {}

    environment = _environment(env, receipt, add)

    # ROUTE -----------------------------------------------------------------
    stat = req.get("stat")
    if spec is None:
        add("ROUTE", "ROUTE_NOT_IN_ACCEPTANCE_MATRIX")
    elif "stats" in spec and stat not in spec["stats"]:
        add("ROUTE", "PROP_STAT_UNSUPPORTED")
    if resp.get("route_status") == "UNSUPPORTED":
        add("ROUTE", "UNSUPPORTED_ROUTE_HOLD")
    action = {
        "action_route": case.get("action_route"),
        "http_status": case.get("http_status"),
        "latency_ms": case.get("latency_ms"),
    }

    # IDENTITY --------------------------------------------------------------
    canonical = {
        "event_id": event.get("event_id"),
        "line": req.get("line"),
        "side": req.get("side"),
        "direction": req.get("direction"),
        "stat": stat,
        "player": req.get("player"),
    }
    if not event.get("event_id") or resp.get("event_id") != event.get("event_id"):
        add("IDENTITY", "IDENTITY_CONFLICT")
    if resp.get("line") is not None and _num(resp.get("line")) != _num(req.get("line")):
        add("IDENTITY", "EXACT_LINE_MISMATCH")
    for key in ("side", "direction"):
        if req.get(key) is not None and resp.get(key) != req.get(key):
            add("IDENTITY", "IDENTITY_CONFLICT")
    if spec and spec["market"] == "PROP":
        if req.get("direction") not in ("MORE", "LESS"):
            add("IDENTITY", "PROP_DIRECTION_MISSING")
        if route == "NFL_PROP" and resp.get("espn_alias_verified") is not True:
            add("IDENTITY", "ESPN_ALIAS_UNVERIFIED")
    if spec and spec["market"] == "SPREAD":
        text = str(req.get("line_text") or "")
        line = _num(req.get("line"))
        try:
            signed = text[:1] in ("+", "-") and float(text) == line
        except ValueError:
            signed = False
        if line is None or not signed:
            add("IDENTITY", "SPREAD_LINE_UNSIGNED")

    # LIFECYCLE -------------------------------------------------------------
    phase = case.get("phase")
    start = _parse_time(event.get("start_time"))
    if phase == "PREGAME":
        if event.get("status") not in ("SCHEDULED", None) or (start and start <= now):
            add("LIFECYCLE", "EVENT_STARTED_INVALIDATED")
    elif phase == "LIVE":
        if not event.get("live_event_recognized"):
            add("LIFECYCLE", "LIVE_EVENT_UNRECOGNIZED")
        if resp.get("prediction_phase") != "LIVE" or not resp.get("live_specialist_certified"):
            add("LIFECYCLE", "PREGAME_REUSED_IN_LIVE")
    else:
        add("LIFECYCLE", "PHASE_MISSING")

    # MODEL -----------------------------------------------------------------
    unsupported = resp.get("route_status") == "UNSUPPORTED"
    model_failure = None if unsupported else classify_model_outcome(case.get("http_status"), resp)
    if model_failure:
        add("MODEL", model_failure)
    if unsupported and resp.get("probability") is not None:
        add("MODEL", "PROBABILITY_BORROWED")
    held = bool(model_failure) or unsupported
    if spec and resp.get("specialist_kind") not in (None, spec["specialist_kind"]) and not held:
        add("MODEL", "CONTROLLING_SPECIALIST_MISMATCH")
    if spec and spec["market"] == "SPREAD" and resp.get("probability_source") not in (None, "SCORE_MARGIN_COVER"):
        add("MODEL", "PROBABILITY_BORROWED")
    if not held and not resp.get("model_artifact"):
        add("MODEL", "MODEL_ARTIFACT_MISSING")
    model = {
        "model_status": model_failure or ("ROUTE_UNSUPPORTED" if unsupported else "OK"),
        "model_artifact": resp.get("model_artifact"),
        "model_version": resp.get("model_version"),
        "specialist_kind": resp.get("specialist_kind"),
        "probability": resp.get("probability"),
    }

    # CALIBRATION -----------------------------------------------------------
    p = _num(resp.get("probability"))
    lb = _num(resp.get("lower_bound"))
    threshold = _num(resp.get("publication_threshold"))
    if not held:
        if lb is None:
            add("CALIBRATION", "CALIBRATED_LOWER_BOUND_MISSING")
        elif p is not None and not 0.0 < lb <= p < 1.0:
            add("CALIBRATION", "CALIBRATED_BOUND_INVALID")
        if not resp.get("calibration_id"):
            add("CALIBRATION", "CALIBRATION_EVIDENCE_MISSING")
        if threshold is None or threshold < MIN_PUBLICATION_LOWER_BOUND:
            add("CALIBRATION", "PUBLICATION_THRESHOLD_WEAKENED")
        if spec and spec["market"] == "SPREAD":
            line = _num(req.get("line"))
            if line is not None and line.is_integer() and not resp.get("push_rule"):
                add("CALIBRATION", "PUSH_RULE_MISSING")
            if not resp.get("settlement_rule"):
                add("CALIBRATION", "SETTLEMENT_RULE_MISSING")
    calibration = {
        "calibration_id": resp.get("calibration_id"),
        "lower_bound": resp.get("lower_bound"),
        "publication_threshold": resp.get("publication_threshold"),
        "push_rule": resp.get("push_rule"),
    }

    # PUBLICATION (#1896 contradiction) -------------------------------------
    publishable = resp.get("probability_publishable") is True
    rank_eligible = resp.get("rank_eligible") is True
    artifact_publishable = resp.get("artifact_publishable")
    if artifact_publishable is not None and bool(artifact_publishable) != publishable:
        add("PUBLICATION", "PUBLISHABILITY_CONTRADICTION")
    if rank_eligible and not publishable:
        add("PUBLICATION", "RANK_WITHOUT_PUBLICATION")
    if publishable and (held or lb is None or threshold is None or lb < threshold):
        add("PUBLICATION", "PUBLISHED_WITHOUT_BOUND_SUPPORT")
    if not held and not publishable and not any(s == "PUBLICATION" for s, _ in blockers):
        add("PUBLICATION", "PROBABILITY_NOT_PUBLISHABLE")

    # RECEIPT ---------------------------------------------------------------
    receipt_id = receipt.get("receipt_id")
    if not held:
        if not receipt_id or receipt.get("immutable") is not True:
            add("RECEIPT", "IMMUTABLE_RECEIPT_MISSING")
        else:
            if receipt.get("case_id") != case.get("case_id") or receipt.get("event_id") != event.get("event_id"):
                add("RECEIPT", "RECEIPT_FOREIGN")
            created = _parse_time(receipt.get("created_at"))
            if created is None or now - created > RECEIPT_MAX_AGE or created > now:
                add("RECEIPT", "RECEIPT_STALE")
            if _num(receipt.get("probability")) != p or _num(receipt.get("line")) != _num(req.get("line")):
                add("RECEIPT", "RECEIPT_PERSISTENCE_MISMATCH")
        start_t = start
        pred_t = _parse_time(resp.get("prediction_created_at"))
        if phase == "PREGAME" and start_t and pred_t and pred_t >= start_t:
            add("RECEIPT", "CHRONOLOGY_VIOLATION")

    # RECONCILIATION --------------------------------------------------------
    rec = reconcile_rows(rows)
    for code in rec["blockers"]:
        add("RECONCILIATION", code)

    # MONEY AUDIT (separate; never revokes probability) ---------------------
    money_blockers: list[str] = []
    money = case.get("money")
    if not held:
        if not money or not money.get("price_snapshot_id") or _num(money.get("price")) is None:
            money_blockers.append("MONEY_EVIDENCE_ABSENT")
        elif money.get("wager_placed") or money.get("order_placed"):
            money_blockers.append("LIVE_EXECUTION_EVIDENCE_FORBIDDEN")

    return RouteResult(
        case_id=str(case.get("case_id")),
        route=str(route),
        environment=redact(environment),
        action=action,
        canonical=canonical,
        model=model,
        calibration=calibration,
        probability_publishable=publishable,
        rank_eligible=rank_eligible,
        receipt_id=receipt_id,
        reconciliation={k: v for k, v in rec.items() if k != "blockers"},
        blockers=blockers,
        money_blockers=money_blockers,
        money_applicable=not held,
    )


def reconcile_rows(rows: Mapping[str, Any]) -> dict[str, Any]:
    """rows_in == completed + held + rejected and each row terminates once."""
    states = rows.get("terminal_states") or []
    ids = [r.get("row_id") for r in states]
    counts = {s: sum(1 for r in states if r.get("state") == s) for s in TERMINAL_STATES}
    rows_in = rows.get("rows_in")
    blockers = []
    if not isinstance(rows_in, int) or rows_in != len(states):
        blockers.append("RECONCILIATION_COUNT_MISMATCH")
    if len(set(ids)) != len(ids):
        blockers.append("ROW_TERMINATED_MORE_THAN_ONCE")
    if any(r.get("state") not in TERMINAL_STATES for r in states):
        blockers.append("ROW_NOT_TERMINAL")
    return {
        "rows_in": rows_in,
        "rows_completed": counts["COMPLETED"],
        "rows_held": counts["HELD"],
        "rows_rejected": counts["REJECTED"],
        "balanced": not blockers,
        "blockers": blockers,
    }


def coverage(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    seen = {(c.get("route"), (c.get("request") or {}).get("stat")) for c in cases}
    seen |= {(r, None) for r, _ in seen}
    missing = [f"{r}:{s}" if s else r for r, s in REQUIRED_COVERAGE if (r, s) not in seen]
    phases = {c.get("phase") for c in cases}
    if "LIVE" not in phases:
        missing.append("PHASE:LIVE")
    return {"required": len(REQUIRED_COVERAGE) + 1, "missing": missing, "complete": not missing}


def run_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    if bundle.get("can_execute") is not False:
        raise ValueError("acceptance bundle must declare can_execute=false")
    now = _parse_time(bundle.get("evaluated_at"))
    if now is None:
        raise ValueError("acceptance bundle requires a timezone-aware evaluated_at")
    env = bundle.get("environment") or {}
    cases = bundle.get("cases") or []
    results = [evaluate_case(c, env, now) for c in cases]
    rows = [r.as_dict() for r in results]

    expectation_mismatches = []
    for case, row in zip(cases, rows):
        expect = case.get("expect")
        if not expect:
            continue
        for key in ("probability_status", "money_status", "first_failing_stage", "exact_blocker"):
            if key in expect and expect[key] != row[key]:
                expectation_mismatches.append(
                    {"case_id": row["case_id"], "field": key, "expected": expect[key], "actual": row[key]}
                )

    cov = coverage(cases)
    totals = {
        "cases": len(rows),
        "probability_accepted": sum(r["probability_status"] == "PROBABILITY_ACCEPTED" for r in rows),
        "held_typed": sum(r["probability_status"] == "HELD_TYPED" for r in rows),
        "rejected": sum(r["probability_status"] == "REJECTED" for r in rows),
        "money_audit_failed": sum(r["money_status"] == "MONEY_AUDIT_FAILED" for r in rows),
    }
    totals["reconciled"] = (
        totals["cases"] == totals["probability_accepted"] + totals["held_typed"] + totals["rejected"]
    )
    receipt_ids = [r["receipt_id"] for r in rows if r["receipt_id"]]
    receipts = {
        "receipts": len(receipt_ids),
        "unique": len(set(receipt_ids)),
        "duplicates": sorted({x for x in receipt_ids if receipt_ids.count(x) > 1}),
    }
    harness_ok = cov["complete"] and totals["reconciled"] and not expectation_mismatches and not receipts["duplicates"]
    return {
        "harness_version": HARNESS_VERSION,
        "mode": env.get("mode"),
        "evaluated_at": bundle.get("evaluated_at"),
        "dry_run_only": DRY_RUN_ONLY,
        "can_execute": CAN_EXECUTE,
        "self_certified": False,
        "production_acceptance": "NOT_PERFORMED_REQUIRES_INDEPENDENT_SIRT_QA",
        "harness_status": "HARNESS_FIXTURES_CONSISTENT" if harness_ok else "HARNESS_FAILED",
        "coverage": cov,
        "totals": totals,
        "receipt_reconciliation": receipts,
        "expectation_mismatches": expectation_mismatches,
        "routes": rows,
    }


def plan_post_deploy_canary(authorized_launch_id: str | None = None) -> dict[str, Any]:
    """Describe the opt-in authenticated dry-run canary. Never runs it.

    Execution requires a separately authorized launch and an independent
    operator; this module has no network or write path by design.
    """
    return {
        "status": "PLANNED_NOT_RUN",
        "authorized_launch_id": authorized_launch_id,
        "requires": [
            "separately authorized launch after an authorized Render release",
            "independent SIRT/QA operator (not the PR author)",
            "OIDC-authenticated read-only/dry-run Action calls; no service-role secrets in logs",
            "captured evidence written to a bundle with environment.mode=CANARY",
        ],
        "forbidden": ["production writes", "deploy triggers", "wagers or market orders"],
        "can_execute": CAN_EXECUTE,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    t = report["totals"]
    lines = [
        "# V17 model acceptance harness report",
        "",
        f"- Harness: `{report['harness_version']}` · mode `{report['mode']}` · evaluated `{report['evaluated_at']}`",
        f"- Harness status: **{report['harness_status']}**",
        f"- Production acceptance: **{report['production_acceptance']}** (self_certified={report['self_certified']})",
        f"- dry_run_only={report['dry_run_only']} · can_execute={report['can_execute']}",
        "",
        "## Totals",
        "",
        f"{t['cases']} cases = {t['probability_accepted']} accepted + {t['held_typed']} held (typed) "
        f"+ {t['rejected']} rejected · reconciled={t['reconciled']} · money-audit failures={t['money_audit_failed']}",
        "",
        f"Coverage complete: {report['coverage']['complete']}"
        + (f" (missing: {', '.join(report['coverage']['missing'])})" if report["coverage"]["missing"] else ""),
        f"Receipts: {report['receipt_reconciliation']['receipts']} "
        f"({report['receipt_reconciliation']['unique']} unique)",
        "",
        "## Routes",
        "",
        "| Case | Route | Probability | Money | First failing stage | Exact blocker | p / lower bound | Publishable | Receipt |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["routes"]:
        lines.append(
            f"| {r['case_id']} | {r['route']} | {r['probability_status']} | {r['money_status']} | "
            f"{r['first_failing_stage'] or '—'} | {r['exact_blocker'] or '—'} | "
            f"{r['model']['probability']} / {r['calibration']['lower_bound']} | "
            f"{r['probability_publishable']} | {r['receipt_id'] or '—'} |"
        )
    if report["expectation_mismatches"]:
        lines += ["", "## Expectation mismatches", ""]
        lines += [f"- {m['case_id']} `{m['field']}`: expected {m['expected']}, got {m['actual']}"
                  for m in report["expectation_mismatches"]]
    lines += [
        "",
        "Fixture evidence only. Independent SIRT/QA must perform production acceptance after an "
        "authorized Render release; this report does not certify production.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args(argv)
    report = run_bundle(json.loads(args.bundle.read_text()))
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.write_text(text)
    if args.md_out:
        args.md_out.write_text(render_markdown(report))
    if not (args.json_out or args.md_out):
        print(text, end="")
    return 0 if report["harness_status"] == "HARNESS_FIXTURES_CONSISTENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
