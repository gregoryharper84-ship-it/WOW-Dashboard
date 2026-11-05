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
import hashlib
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

# receipt_kind selects the typed route adapter in the RECEIPT stage. The prop
# canary ledger shape (migrations/20260917_prop_action_canary_receipts.sql) is
# NOT imposed on ML/spread routes, which persist to wow_predictions instead
# (schema.sql) and have no hash-ledger, action_operation_id, or
# certification_id columns at all.
ROUTES: dict[str, dict[str, Any]] = {
    "NFL_MONEYLINE": {
        "sport": "NFL", "market": "MONEYLINE", "specialist_kind": "TEAM_EVENT_WIN",
        "receipt_kind": "GOVERNED_PREDICTION",
    },
    "NFL_PROP": {
        "sport": "NFL", "market": "PROP", "specialist_kind": "PLAYER_PROP", "stats": NFL_PROP_STATS,
        "receipt_kind": "PROP_ACTION_CANARY",
    },
    "NBA_PROP": {
        "sport": "NBA", "market": "PROP", "specialist_kind": "PLAYER_PROP", "stats": NBA_PROP_STATS,
        "receipt_kind": "PROP_ACTION_CANARY",
    },
    "NFL_SPREAD": {
        "sport": "NFL", "market": "SPREAD", "specialist_kind": "SCORE_MARGIN_COVER",
        "receipt_kind": "GOVERNED_PREDICTION",
    },
    "NCAAF_SPREAD": {
        "sport": "NCAAF", "market": "SPREAD", "specialist_kind": "SCORE_MARGIN_COVER",
        "receipt_kind": "GOVERNED_PREDICTION",
    },
}

_VALID_IDENTITY_BINDING_STATUS = frozenset({"PASS", "PROVIDER_IDENTITY_ONLY"})

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
    # (immutable_receipt_hash, prediction_id) for PROP_ACTION_CANARY rows only;
    # used by run_bundle's cross-case replay check, never exported via as_dict.
    receipt_hash_fingerprint: tuple[str, str] | None = None

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


def prop_canary_identity_hash(
    *, sport: Any, stat_type: Any, model_artifact_version: Any, artifact_checksum: Any,
    certification_id: Any, prediction_id: Any, calibrated_probability: float, calibrated_lower_bound: float,
) -> str:
    """Recompute the prop-canary receipt's identity hash from its own fields.

    Mirrors the identity columns of ``wow_prop_action_canary_receipts``
    (migrations/20260917_prop_action_canary_receipts.sql): sport, stat_type,
    model_artifact_version, artifact_checksum, certification_id, prediction_id,
    plus the calibrated probability/lower-bound pair.

    This is schema-shape verification only. Recomputing a hash from
    caller-supplied fields can show a receipt is internally self-consistent
    (not tampered) or tie two cases together (replayed), but it can never
    prove the database's own `immutable_receipt_hash` unique constraint --
    that guarantee is enforced by Postgres at write time, not by a fixture.
    """
    payload = "|".join([
        str(sport), str(stat_type), str(model_artifact_version), str(artifact_checksum),
        str(certification_id), str(prediction_id),
        format(float(calibrated_probability), ".6f"),
        format(float(calibrated_lower_bound), ".6f"),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evaluate_prop_identity_binding(spec: Mapping[str, Any], req: Mapping[str, Any],
                                     event: Mapping[str, Any], add) -> None:
    """Mirror pick_request_runtime.py::_validate_evidence's real role_status contract.

    The real check reads ``row.evidence.role_status`` -- a nested dict with
    ``canonical_event_id``, ``provider_event_ids`` (keyed by provider, e.g.
    "ESPN"), ``espn_athlete_id``, ``status``, ``verified_canonical_event_id``
    and ``identity_binding_status`` -- not a single pre-verified boolean. A
    fixture carrying a flat `espn_alias_verified: true` can never exercise the
    actual typed PROP_EVENT_IDENTITY_CONFLICT:* branches below.
    """
    role = (req.get("evidence") or {}).get("role_status") or {}
    request_event_id = str(event.get("event_id") or "").strip()

    bound_event_id = str(role.get("canonical_event_id") or "").strip()
    if bound_event_id and bound_event_id != request_event_id:
        add("IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:CANONICAL_EVENT_ID_MISMATCH")

    if spec.get("sport") == "NFL":
        provider_event_ids = role.get("provider_event_ids")
        espn_alias = (
            str(provider_event_ids.get("ESPN") or "").strip()
            if isinstance(provider_event_ids, Mapping)
            else ""
        )
        provider_backed = bool(
            espn_alias
            or str(role.get("espn_athlete_id") or "").strip()
            or str(role.get("status") or "").strip().upper() == "ACTIVE_CURRENT_ESPN_ROSTER"
        )
        verified_event_id = str(role.get("verified_canonical_event_id") or "").strip()
        if provider_backed and not verified_event_id:
            add("IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_UNVERIFIED")
        if verified_event_id and verified_event_id != request_event_id:
            add("IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_MISMATCH")

    identity_status = str(role.get("identity_binding_status") or "").strip().upper()
    if identity_status and identity_status not in _VALID_IDENTITY_BINDING_STATUS:
        add("IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:IDENTITY_BINDING_NOT_PASS")


def _evaluate_prop_canary_receipt(
    receipt: Mapping[str, Any], prediction: Mapping[str, Any], p: float | None, add,
) -> tuple[str | None, tuple[str, str] | None]:
    """Validate an NFL/NBA prop receipt against the real
    ``wow_prop_action_canary_receipts`` column contract, not a self-invented
    shape. Returns (natural receipt id, hash fingerprint for replay detection).

    Schema-shape checked only: a passing result proves the captured receipt is
    well-formed, internally self-consistent, and linked to the right
    prediction/calibration identity. It cannot prove the database's own
    unique-constraint / RLS-enforced immutability guarantee -- that is a
    runtime property only Postgres can certify, never a fixture.
    """
    required = (
        "receipt_id", "created_at", "action_operation_id", "action_request_fingerprint",
        "prediction_id", "sport", "stat_type", "feature_schema_version", "model_family",
        "model_artifact_version", "artifact_checksum", "calibrator_version",
        "certification_id", "calibration_evidence_hash", "raw_model_probability",
        "calibrated_probability", "calibrated_lower_bound", "reconciliation_status",
        "immutable_receipt_hash",
    )
    if not receipt or any(receipt.get(f) in (None, "") for f in required):
        add("RECEIPT", "RECEIPT_FIELD_MISSING")
        return receipt.get("receipt_id"), None

    receipt_id = receipt.get("receipt_id")
    if receipt.get("action_operation_id") not in ("scoreWowPickRequest", "scoreWowV17PickRequest"):
        add("RECEIPT", "RECEIPT_ACTION_OPERATION_INVALID")
    if str(receipt.get("reconciliation_status")).upper() != "PASS":
        add("RECEIPT", "RECEIPT_RECONCILIATION_NOT_PASS")
    if receipt.get("can_execute") is not False:
        add("RECEIPT", "RECEIPT_CAN_EXECUTE_INVARIANT_UNVERIFIED")

    raw_p = _num(receipt.get("raw_model_probability"))
    cal_p = _num(receipt.get("calibrated_probability"))
    cal_lb = _num(receipt.get("calibrated_lower_bound"))
    bounds_ok = (
        raw_p is not None and 0.0 < raw_p < 1.0
        and cal_p is not None and cal_lb is not None and 0.0 < cal_lb <= cal_p < 1.0
    )
    if not bounds_ok:
        add("RECEIPT", "RECEIPT_CALIBRATED_BOUNDS_INVALID")
    if cal_p is not None and p is not None and abs(cal_p - p) > 1e-9:
        add("RECEIPT", "RECEIPT_PERSISTENCE_MISMATCH")

    if prediction.get("prediction_id") and receipt.get("prediction_id") != prediction.get("prediction_id"):
        add("RECEIPT", "RECEIPT_PREDICTION_LINKAGE_MISMATCH")

    identity_fields = ("certification_id", "calibrator_version", "calibration_evidence_hash")
    if any(
        prediction.get(f) is not None and receipt.get(f) != prediction.get(f)
        for f in identity_fields
    ):
        add("RECEIPT", "RECEIPT_CALIBRATION_IDENTITY_INCOMPATIBLE")

    hash_fingerprint = None
    if bounds_ok:
        expected_hash = prop_canary_identity_hash(
            sport=receipt.get("sport"), stat_type=receipt.get("stat_type"),
            model_artifact_version=receipt.get("model_artifact_version"),
            artifact_checksum=receipt.get("artifact_checksum"),
            certification_id=receipt.get("certification_id"),
            prediction_id=receipt.get("prediction_id"),
            calibrated_probability=cal_p, calibrated_lower_bound=cal_lb,
        )
        if receipt.get("immutable_receipt_hash") != expected_hash:
            add("RECEIPT", "RECEIPT_HASH_INVALID")
        else:
            hash_fingerprint = (receipt.get("immutable_receipt_hash"), str(receipt.get("prediction_id")))
    return receipt_id, hash_fingerprint


def _evaluate_governed_prediction_receipt(
    receipt: Mapping[str, Any], prediction: Mapping[str, Any], event: Mapping[str, Any],
    p: float | None, now: datetime, add,
) -> str | None:
    """Validate an ML/spread receipt against the real ``wow_predictions`` row
    contract (schema.sql) -- the calibrated-bounds triple and the
    ``locked_at`` immutability trigger -- instead of the prop-canary
    hash-ledger shape, which does not apply to this route family.
    """
    # locked_at is excluded here: NULL is a legitimate column value (not yet
    # locked) on a real wow_predictions row, not a structurally missing field.
    required = (
        "prediction_id", "event_id", "sport", "market_type", "calibrated_probability",
        "calibrated_probability_lower_bound", "calibrated_probability_upper_bound",
        "probability_publishable", "created_at",
    )
    if not receipt or "locked_at" not in receipt or any(receipt.get(f) is None for f in required):
        add("RECEIPT", "RECEIPT_FIELD_MISSING")
        return receipt.get("prediction_id")

    receipt_id = receipt.get("prediction_id")
    if not receipt.get("locked_at"):
        add("RECEIPT", "RECEIPT_NOT_LOCKED")

    cal_p = _num(receipt.get("calibrated_probability"))
    lb = _num(receipt.get("calibrated_probability_lower_bound"))
    ub = _num(receipt.get("calibrated_probability_upper_bound"))
    # Mirrors schema.sql's chk_calibrated_bounds exactly (strict > 0, strict < 1).
    bounds_ok = (
        cal_p is not None and lb is not None and ub is not None
        and lb > 0.0 and lb <= cal_p <= ub and ub < 1.0
    )
    if not bounds_ok:
        add("RECEIPT", "RECEIPT_CALIBRATED_BOUNDS_INVALID")
    if cal_p is not None and p is not None and abs(cal_p - p) > 1e-9:
        add("RECEIPT", "RECEIPT_PERSISTENCE_MISMATCH")

    if prediction.get("prediction_id") and receipt.get("prediction_id") != prediction.get("prediction_id"):
        add("RECEIPT", "RECEIPT_PREDICTION_LINKAGE_MISMATCH")
    if receipt.get("event_id") != event.get("event_id"):
        add("RECEIPT", "RECEIPT_FOREIGN")

    created = _parse_time(receipt.get("created_at"))
    if created is None or now - created > RECEIPT_MAX_AGE or created > now:
        add("RECEIPT", "RECEIPT_STALE")
    return receipt_id


def _environment(env: Mapping[str, Any], case_run_sha: Any, add) -> dict[str, Any]:
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
    # run_sha is captured Action/CI-run provenance for this case (which GitHub
    # Actions run produced this evidence), not a column on the persisted
    # receipt -- neither wow_prop_action_canary_receipts nor wow_predictions
    # has a run_sha field.
    if case_run_sha is not None and case_run_sha != github_sha:
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

    environment = _environment(env, case.get("run_sha"), add)

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
        _evaluate_prop_identity_binding(spec, req, event, add)
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

    # RECEIPT -----------------------------------------------------------
    # Dispatches to a typed route adapter instead of one shape for every
    # route: the prop-canary hash ledger does not apply to ML/spread, and the
    # wow_predictions governed-row contract does not apply to props.
    receipt_kind = (spec or {}).get("receipt_kind")
    prediction = case.get("prediction") or {}
    hash_fingerprint = None
    if not held:
        if receipt_kind == "PROP_ACTION_CANARY":
            receipt_id, hash_fingerprint = _evaluate_prop_canary_receipt(receipt, prediction, p, add)
        elif receipt_kind == "GOVERNED_PREDICTION":
            receipt_id = _evaluate_governed_prediction_receipt(receipt, prediction, event, p, now, add)
        else:
            receipt_id = receipt.get("receipt_id") or receipt.get("prediction_id")
            if not receipt_id:
                add("RECEIPT", "RECEIPT_FIELD_MISSING")
        start_t = start
        pred_t = _parse_time(resp.get("prediction_created_at"))
        if phase == "PREGAME" and start_t and pred_t and pred_t >= start_t:
            add("RECEIPT", "CHRONOLOGY_VIOLATION")
    else:
        receipt_id = None

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
        receipt_hash_fingerprint=hash_fingerprint,
    )


def reconcile_rows(rows: Mapping[str, Any]) -> dict[str, Any]:
    """rows_in == completed + held + rejected and each row terminates once.

    Field names (``row_key`` / ``terminal_status``) match the real per-row
    outcome shape in pick_request_runtime_core.py and the aggregate
    rows_in/rows_completed/rows_held/rows_rejected counters persisted by
    agent_runtime_api.py, not a harness-invented ``row_id`` / ``state`` pair.
    """
    states = rows.get("terminal_states") or []
    ids = [r.get("row_key") for r in states]
    counts = {s: sum(1 for r in states if r.get("terminal_status") == s) for s in TERMINAL_STATES}
    rows_in = rows.get("rows_in")
    blockers = []
    if not isinstance(rows_in, int) or rows_in != len(states):
        blockers.append("RECONCILIATION_COUNT_MISMATCH")
    if len(set(ids)) != len(ids):
        blockers.append("ROW_TERMINATED_MORE_THAN_ONCE")
    if any(r.get("terminal_status") not in TERMINAL_STATES for r in states):
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
    # A PROP_ACTION_CANARY receipt's immutable_receipt_hash can be internally
    # self-consistent on each case alone and still be a replayed/copy-pasted
    # receipt -- the same hash (and therefore the same persisted prediction
    # row, since prediction_id is itself a hash input) backing more than one
    # case. Only a cross-case comparison catches that, mirroring the
    # existing duplicate-receipt_id check below.
    hash_values = [r.receipt_hash_fingerprint[0] for r in results if r.receipt_hash_fingerprint]
    receipts = {
        "receipts": len(receipt_ids),
        "unique": len(set(receipt_ids)),
        "duplicates": sorted({x for x in receipt_ids if receipt_ids.count(x) > 1}),
        "hash_duplicates": sorted({h for h in hash_values if hash_values.count(h) > 1}),
    }
    harness_ok = (
        cov["complete"] and totals["reconciled"] and not expectation_mismatches
        and not receipts["duplicates"] and not receipts["hash_duplicates"]
    )
    return {
        "harness_version": HARNESS_VERSION,
        "mode": env.get("mode"),
        "evaluated_at": bundle.get("evaluated_at"),
        "dry_run_only": DRY_RUN_ONLY,
        "can_execute": CAN_EXECUTE,
        "self_certified": False,
        "production_acceptance": "NOT_PERFORMED_REQUIRES_INDEPENDENT_SIRT_QA",
        "harness_status": "HARNESS_FIXTURES_CONSISTENT" if harness_ok else "HARNESS_FAILED",
        "receipt_evidence_class": "FIXTURE_SCHEMA_SHAPE_CHECKED_NOT_DATABASE_CONSTRAINT_PROVEN",
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
        f"- Receipt evidence class: **{report['receipt_evidence_class']}** "
        "(schema-shape checked; never a substitute for the database's own constraint proof)",
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
