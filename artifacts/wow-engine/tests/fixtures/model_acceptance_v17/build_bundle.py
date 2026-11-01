import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from v17.model_acceptance_harness import prop_canary_identity_hash  # noqa: E402

SHA = "1" * 40
NOW = "2026-10-21T15:00:00Z"
START = "2026-10-25T17:00:00Z"
PRED = "2026-10-21T14:30:00Z"

GOVERNED_MARKET = {"NFL_MONEYLINE": "MONEYLINE", "NFL_SPREAD": "SPREAD", "NCAAF_SPREAD": "SPREAD"}


def _role_status(case_id, event_id):
    """The real nested evidence.role_status shape (pick_request_runtime.py),
    not a flat espn_alias_verified boolean."""
    return {
        "canonical_event_id": event_id,
        "provider_event_ids": {"ESPN": f"espn-{case_id}"},
        "espn_athlete_id": f"espn-athlete-{case_id}",
        "status": "ACTIVE_CURRENT_ESPN_ROSTER",
        "verified_canonical_event_id": event_id,
        "identity_binding_status": "PASS",
    }


def _prop_canary_receipt(case_id, route, req, prediction, calibrated_probability, calibrated_lower_bound):
    """Shape mirrors wow_prop_action_canary_receipts exactly (migrations/
    20260917_prop_action_canary_receipts.sql): a hash-ledger row keyed by
    prediction_id, not a self-invented receipt_id/immutable/case_id/line/
    run_sha bag of fields."""
    receipt_hash = prop_canary_identity_hash(
        sport=prediction["sport"], stat_type=prediction["stat_type"],
        model_artifact_version=prediction["model_artifact_version"],
        artifact_checksum=prediction["artifact_checksum"],
        certification_id=prediction["certification_id"],
        prediction_id=prediction["prediction_id"],
        calibrated_probability=calibrated_probability, calibrated_lower_bound=calibrated_lower_bound,
    )
    return {
        "receipt_id": f"RCPT-{case_id}",
        "created_at": PRED,
        "action_operation_id": "scoreWowPickRequest",
        "action_request_fingerprint": f"FP-{case_id}",
        "prediction_id": prediction["prediction_id"],
        "sport": prediction["sport"],
        "stat_type": prediction["stat_type"],
        "feature_schema_version": prediction["feature_schema_version"],
        "model_family": prediction["model_family"],
        "model_artifact_version": prediction["model_artifact_version"],
        "artifact_checksum": prediction["artifact_checksum"],
        "calibrator_version": prediction["calibrator_version"],
        "certification_id": prediction["certification_id"],
        "calibration_evidence_hash": prediction["calibration_evidence_hash"],
        "raw_model_probability": calibrated_probability - 0.02,
        "calibrated_probability": calibrated_probability,
        "calibrated_lower_bound": calibrated_lower_bound,
        "reconciliation_status": "PASS",
        "immutable_receipt_hash": receipt_hash,
        "can_execute": False,
    }


def _governed_receipt(case_id, event_id, prediction, calibrated_probability, calibrated_lower_bound):
    """Shape mirrors wow_predictions (schema.sql): locked_at is the real
    immutability signal (a before-update trigger blocks edits once set),
    not a self-attested boolean `immutable` flag."""
    return {
        "prediction_id": prediction["prediction_id"],
        "event_id": event_id,
        "sport": prediction["sport"],
        "market_type": prediction["market_type"],
        "calibrated_probability": calibrated_probability,
        "calibrated_probability_lower_bound": calibrated_lower_bound,
        "calibrated_probability_upper_bound": min(calibrated_probability + 0.2, 0.95),
        "probability_publishable": True,
        "locked_at": PRED,
        "created_at": PRED,
    }


def base(case_id, route, req, resp_extra=None, phase="PREGAME", stat=None):
    ev = f"EVT-{case_id}"
    r = dict(req)
    probability, lower_bound = 0.64, 0.58
    resp = {"model_status": "OK", "event_id": ev, "line": r.get("line"), "side": r.get("side"),
            "direction": r.get("direction"), "probability": probability, "lower_bound": lower_bound,
            "publication_threshold": 0.55, "calibration_id": f"CAL-{route}-v3",
            "model_artifact": f"{route.lower()}_fitted.joblib", "model_version": "v17.3.0",
            "specialist_kind": {"NFL_MONEYLINE": "TEAM_EVENT_WIN", "NFL_SPREAD": "SCORE_MARGIN_COVER",
                                "NCAAF_SPREAD": "SCORE_MARGIN_COVER"}.get(route, "PLAYER_PROP"),
            "probability_publishable": True, "rank_eligible": True, "artifact_publishable": True,
            "prediction_phase": "PREGAME", "prediction_created_at": PRED}
    if route in ("NFL_PROP", "NBA_PROP"):
        r = {**r, "evidence": {"role_status": _role_status(case_id, ev)}}
    if route.endswith("SPREAD"):
        resp.update(probability_source="SCORE_MARGIN_COVER", settlement_rule="FULL_GAME_INCL_OT",
                    push_rule="PUSH_VOID_REFUND")
    resp.update(resp_extra or {})
    probability = resp.get("probability", probability)
    lower_bound = resp.get("lower_bound", lower_bound)

    sport = {"NFL_MONEYLINE": "NFL", "NFL_PROP": "NFL", "NBA_PROP": "NBA",
             "NFL_SPREAD": "NFL", "NCAAF_SPREAD": "NCAAF"}[route]
    if route in ("NFL_PROP", "NBA_PROP"):
        prediction = {
            "prediction_id": f"PRED-{case_id}", "sport": sport, "stat_type": stat or r.get("stat"),
            "feature_schema_version": "FSV-1", "model_family": "PROP_YARDS_V3",
            "model_artifact_version": "v17.3.0", "artifact_checksum": f"sha256:{case_id}-artifact",
            "calibrator_version": "ISOTONIC_V1", "certification_id": f"CERT-{case_id}",
            "calibration_evidence_hash": f"sha256:{case_id}-calibration",
        }
        receipt = (
            _prop_canary_receipt(case_id, route, r, prediction, probability, lower_bound)
            if isinstance(probability, (int, float)) and isinstance(lower_bound, (int, float))
            else {}
        )
    else:
        prediction = {"prediction_id": f"PRED-{case_id}", "sport": sport, "market_type": GOVERNED_MARKET[route]}
        receipt = (
            _governed_receipt(case_id, ev, prediction, probability, lower_bound)
            if isinstance(probability, (int, float)) and isinstance(lower_bound, (int, float))
            else {}
        )

    return {
        "case_id": case_id, "route": route, "phase": phase,
        "action_route": "scoreWowTeamEventRequest" if not route.endswith("PROP") else "scoreWowPickRequest",
        "http_status": 200, "latency_ms": 840,
        # Action/CI-run provenance for this case -- which GitHub Actions run
        # produced this captured evidence. Not a column on the persisted
        # receipt (neither real table has a run_sha field).
        "run_sha": SHA,
        "event": {"event_id": ev, "start_time": START, "status": "SCHEDULED"},
        "request": r, "response": resp,
        "prediction": prediction,
        "receipt": receipt,
        "rows": {"rows_in": 1, "terminal_states": [{"row_key": f"ROW-{case_id}", "terminal_status": "COMPLETED"}]},
        "money": {"price_snapshot_id": f"PX-{case_id}", "price": -120, "wager_placed": False},
    }


def expect(c, ps="PROBABILITY_ACCEPTED", stage=None, code=None, money="MONEY_AUDIT_PASSED"):
    c["expect"] = {"probability_status": ps, "first_failing_stage": stage, "exact_blocker": code}
    money = c.pop("_money", money)
    if money: c["expect"]["money_status"] = money
    return c


def unavailable(c, status, http=200):
    c["http_status"] = http
    c["response"].update(model_status=status, probability=None, lower_bound=None,
                         probability_publishable=False, rank_eligible=False, artifact_publishable=False,
                         model_artifact=None)
    c["receipt"] = {}; c["money"] = None
    c["rows"]["terminal_states"][0]["terminal_status"] = "HELD"
    c["_money"] = "MONEY_AUDIT_NOT_RUN"
    return c


ML = {"side": "HOME"}
PROP = lambda stat, line, d="MORE": {"stat": stat, "line": line, "direction": d, "player": "FIXTURE PLAYER"}
SP = lambda line, text: {"side": "HOME", "line": line, "line_text": text}
cases = []
cases.append(expect(base("nfl-ml-accept", "NFL_MONEYLINE", ML)))
c = base("nfl-ml-1896-contradiction", "NFL_MONEYLINE", ML, {"artifact_publishable": False})
cases.append(expect(c, "REJECTED", "PUBLICATION", "PUBLISHABILITY_CONTRADICTION"))
cases.append(expect(base("nfl-prop-passing-accept", "NFL_PROP", PROP("PASSING_YARDS", 245.5))))
cases.append(expect(base("nfl-prop-rushing-accept", "NFL_PROP", PROP("RUSHING_YARDS", 62.5, "LESS"))))
c = base("nfl-prop-receiving-money-absent", "NFL_PROP", PROP("RECEIVING_YARDS", 54.5)); c["money"] = None
cases.append(expect(c, "PROBABILITY_ACCEPTED", "MONEY_AUDIT", "MONEY_EVIDENCE_ABSENT", "MONEY_AUDIT_FAILED"))

# #1894: the real role_status contract, not a flat boolean -- one adversarial
# case per typed PROP_EVENT_IDENTITY_CONFLICT:* branch.
c = base("nfl-prop-1894-alias-unverified", "NFL_PROP", PROP("RECEIVING_YARDS", 70.5))
c["request"]["evidence"]["role_status"].update(verified_canonical_event_id="")
cases.append(expect(c, "REJECTED", "IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_UNVERIFIED"))
c = base("nfl-prop-1894-alias-mismatch", "NFL_PROP", PROP("RECEIVING_YARDS", 71.5))
c["request"]["evidence"]["role_status"].update(verified_canonical_event_id="EVT-SOME-OTHER-EVENT")
cases.append(expect(c, "REJECTED", "IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:NFL_CANONICAL_EVENT_ID_MISMATCH"))
c = base("nfl-prop-1894-canonical-conflict", "NFL_PROP", PROP("RECEIVING_YARDS", 72.5))
c["request"]["evidence"]["role_status"].update(canonical_event_id="EVT-BOUND-ELSEWHERE")
cases.append(expect(c, "REJECTED", "IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:CANONICAL_EVENT_ID_MISMATCH"))
c = base("nfl-prop-1894-binding-not-pass", "NFL_PROP", PROP("RECEIVING_YARDS", 73.5))
c["request"]["evidence"]["role_status"].update(identity_binding_status="PENDING")
cases.append(expect(c, "REJECTED", "IDENTITY", "PROP_EVENT_IDENTITY_CONFLICT:IDENTITY_BINDING_NOT_PASS"))

c = base("nfl-prop-threshold-weakened", "NFL_PROP", PROP("PASSING_YARDS", 230.5),
         {"publication_threshold": 0.45, "lower_bound": 0.50, "probability": 0.57})
cases.append(expect(c, "REJECTED", "CALIBRATION", "PUBLICATION_THRESHOLD_WEAKENED"))
c = base("nfl-prop-malformed-output", "NFL_PROP", PROP("RUSHING_YARDS", 48.5), {"probability": "0.61x"})
c["receipt"] = {}; c["money"] = None
cases.append(expect(c, "REJECTED", "MODEL", "MODEL_OUTPUT_INVALID", "MONEY_AUDIT_NOT_RUN"))
cases.append(expect(unavailable(base("nba-points-200-model-unavailable", "NBA_PROP", PROP("POINTS", 24.5)), "MODEL_UNAVAILABLE"),
                    "HELD_TYPED", "MODEL", "MODEL_UNAVAILABLE"))
cases.append(expect(unavailable(base("nba-pra-200-model-unavailable", "NBA_PROP", PROP("POINTS_REBOUNDS", 33.5)), "MODEL_UNAVAILABLE"),
                    "HELD_TYPED", "MODEL", "MODEL_UNAVAILABLE"))
c = unavailable(base("nba-points-scorer-failed", "NBA_PROP", PROP("POINTS", 19.5)), None, http=503)
c["response"].pop("model_status")
cases.append(expect(c, "HELD_TYPED", "MODEL", "MODEL_SCORER_FAILED"))
cases.append(expect(unavailable(base("nba-points-inputs-insufficient", "NBA_PROP", PROP("POINTS", 21.5)), "MODEL_INPUTS_INSUFFICIENT"),
                    "HELD_TYPED", "MODEL", "MODEL_INPUTS_INSUFFICIENT"))
cases.append(expect(base("nfl-spread-accept-integer-push", "NFL_SPREAD", SP(-3.0, "-3"))))
cases.append(expect(base("ncaaf-spread-accept", "NCAAF_SPREAD", SP(-6.5, "-6.5"))))
c = base("ncaaf-1h-spread-unsupported-hold", "NCAAF_SPREAD", SP(-3.5, "-3.5"))
c["response"].update(route_status="UNSUPPORTED", model_status=None, probability=None, lower_bound=None,
                     probability_publishable=False, rank_eligible=False, artifact_publishable=False)
c["receipt"] = {}; c["money"] = None; c["rows"]["terminal_states"][0]["terminal_status"] = "HELD"; c["_money"] = "MONEY_AUDIT_NOT_RUN"
cases.append(expect(c, "HELD_TYPED", "ROUTE", "UNSUPPORTED_ROUTE_HOLD"))
c = base("nfl-spread-borrowed-ml", "NFL_SPREAD", SP(-2.5, "-2.5"), {"probability_source": "MONEYLINE"})
cases.append(expect(c, "REJECTED", "MODEL", "PROBABILITY_BORROWED"))
c = base("nfl-spread-unsigned-line", "NFL_SPREAD", SP(3.5, "3.5"))
cases.append(expect(c, "REJECTED", "IDENTITY", "SPREAD_LINE_UNSIGNED"))
c = base("nfl-spread-push-rule-missing", "NFL_SPREAD", SP(-7.0, "-7"), {"push_rule": None})
cases.append(expect(c, "REJECTED", "CALIBRATION", "PUSH_RULE_MISSING"))
c = base("nfl-ml-stale-receipt", "NFL_MONEYLINE", ML); c["receipt"]["created_at"] = "2026-10-20T01:00:00Z"
c["response"]["prediction_created_at"] = "2026-10-20T01:00:00Z"
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_STALE"))
c = base("nfl-ml-foreign-receipt", "NFL_MONEYLINE", ML); c["receipt"]["event_id"] = "EVT-SOME-OTHER-EVENT"
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_FOREIGN"))
c = base("nfl-ml-run-sha-mismatch", "NFL_MONEYLINE", ML); c["run_sha"] = "2" * 40
cases.append(expect(c, "REJECTED", "ENVIRONMENT", "RUN_SHA_MISMATCH"))
c = base("nfl-prop-identity-conflict", "NFL_PROP", PROP("PASSING_YARDS", 260.5)); c["response"]["event_id"] = "EVT-OTHER"
cases.append(expect(c, "REJECTED", "IDENTITY", "IDENTITY_CONFLICT"))
c = base("nfl-ml-live-unrecognized-event", "NFL_MONEYLINE", ML, phase="LIVE")
c["event"].update(status="IN_PROGRESS", live_event_recognized=False)
cases.append(expect(c, "REJECTED", "LIFECYCLE", "LIVE_EVENT_UNRECOGNIZED", None))
c = base("nfl-ml-live-pregame-reuse", "NFL_MONEYLINE", ML, phase="LIVE")
c["event"].update(status="IN_PROGRESS", live_event_recognized=True)
cases.append(expect(c, "REJECTED", "LIFECYCLE", "PREGAME_REUSED_IN_LIVE", None))
c = base("nfl-prop-pregame-event-started", "NFL_PROP", PROP("RUSHING_YARDS", 70.5))
c["event"].update(status="IN_PROGRESS", start_time="2026-10-21T13:00:00Z")
cases.append(expect(c, "REJECTED", "LIFECYCLE", "EVENT_STARTED_INVALIDATED", None))
c = base("nfl-ml-row-terminated-twice", "NFL_MONEYLINE", ML)
c["rows"] = {"rows_in": 1, "terminal_states": [{"row_key": "R1", "terminal_status": "COMPLETED"},
                                               {"row_key": "R1", "terminal_status": "HELD"}]}
cases.append(expect(c, "REJECTED", "RECONCILIATION", "RECONCILIATION_COUNT_MISMATCH"))

# --- Adversarial prop-canary-receipt cases (review follow-up on #1935) -----
c = base("nfl-prop-receipt-hash-tampered", "NFL_PROP", PROP("PASSING_YARDS", 241.5))
c["receipt"]["immutable_receipt_hash"] = "0" * 64
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_HASH_INVALID"))

c = base("nfl-prop-receipt-wrong-prediction", "NFL_PROP", PROP("PASSING_YARDS", 242.5))
c["receipt"]["prediction_id"] = "PRED-some-other-case"
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_PREDICTION_LINKAGE_MISMATCH"))

c = base("nfl-prop-receipt-incompatible-calibration", "NFL_PROP", PROP("PASSING_YARDS", 243.5))
_tampered_cert = "CERT-forged-lineage"
c["receipt"]["certification_id"] = _tampered_cert
# Hash is recomputed from the receipt's own (tampered) fields, so it is
# internally self-consistent -- the only thing that catches this is the
# cross-check against the canonical prediction identity, not the hash.
c["receipt"]["immutable_receipt_hash"] = prop_canary_identity_hash(
    sport=c["receipt"]["sport"], stat_type=c["receipt"]["stat_type"],
    model_artifact_version=c["receipt"]["model_artifact_version"],
    artifact_checksum=c["receipt"]["artifact_checksum"],
    certification_id=_tampered_cert, prediction_id=c["receipt"]["prediction_id"],
    calibrated_probability=c["receipt"]["calibrated_probability"],
    calibrated_lower_bound=c["receipt"]["calibrated_lower_bound"],
)
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_CALIBRATION_IDENTITY_INCOMPATIBLE"))

# Note: the duplicate/replayed-receipt scenario (one immutable_receipt_hash
# backing two distinct cases, each individually well-formed) is exercised in
# test_v17_model_acceptance_harness.py against a throwaway bundle copy, not
# baked into this committed fixture -- it deliberately makes the bundle
# HARNESS_FAILED, which this file's own consistent sample report must not be.

# --- Adversarial governed-prediction-receipt cases (ML/spread) -------------
c = base("nfl-ml-receipt-not-locked", "NFL_MONEYLINE", ML)
c["receipt"]["locked_at"] = None
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_NOT_LOCKED"))

c = base("nfl-spread-receipt-bounds-invalid", "NFL_SPREAD", SP(-4.0, "-4"))
c["receipt"]["calibrated_probability_upper_bound"] = 1.0  # must be strictly < 1 (schema.sql chk_calibrated_bounds)
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_CALIBRATED_BOUNDS_INVALID"))

c = base("nfl-ml-receipt-wrong-prediction", "NFL_MONEYLINE", ML)
c["receipt"]["prediction_id"] = "PRED-some-other-case"
cases.append(expect(c, "REJECTED", "RECEIPT", "RECEIPT_PREDICTION_LINKAGE_MISMATCH"))

bundle = {"harness_version": "V17_MODEL_ACCEPTANCE_HARNESS_V1", "evaluated_at": NOW, "can_execute": False,
          "environment": {"mode": "FIXTURE", "environment": "ci-fixture", "dry_run": True, "can_execute": False,
                          "github_sha": SHA, "render_live_sha": None, "render_deploy_id": None,
                          "supabase_project_ref": None,
                          "production_binding_evidence": "NOT_PRODUCTION_EVIDENCE_FIXTURE_ONLY"},
          "cases": cases}

json.dump(bundle, open(sys.argv[1], "w"), indent=2, sort_keys=True); open(sys.argv[1], "a").write("\n")
