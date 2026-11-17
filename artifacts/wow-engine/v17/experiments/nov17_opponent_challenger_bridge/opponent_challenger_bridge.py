"""Offline, non-serving WOW V17 opponent-aware model / prop bridge.

Reads *already fitted* candidate artifacts, complete pregame feature vectors,
LLP score-state scenarios and WOW prop conditional models. Nothing here fits a
model, self-certifies calibration, registers a probability or places an order.

Preserve the incumbent for all picks. Research-only outputs never mint a lower
bound or pretend a historical post-hoc replay was truly available pregame.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MODEL_FAMILY = "NFL_DYNAMIC_TEAM_STATE_LOGIT_V2"
FEATURE_SCHEMA = "NFL_DYNAMIC_TEAM_STATE_FEATURES_V2"


def hash_payload(value: Any) -> str:
    """Match the existing WOW candidate JSON SHA-256 recipe."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str, allow_nan=False).encode()).hexdigest()


def _time(x: Any) -> datetime | None:
    if not isinstance(x, str) or not x:
        return None
    try:
        d = datetime.fromisoformat(x.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo is not None else None


def _str(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _number(v: Any, lo: float | None = None, hi: float | None = None) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if not math.isfinite(f) or lo is not None and f < lo or hi is not None and f > hi:
        return None
    return f


def _out(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, "research_only": True, "can_execute": CAN_EXECUTE,
            "automatic_promotion": AUTOMATIC_PROMOTION,
            "terminal_authority": TERMINAL_AUTHORITY,
            "publishable": False, "publication_allowed": False, **kwargs}


def _match(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (_str(a.get("sport")) and _str(b.get("sport"))
            and a["sport"].strip().upper() == b["sport"].strip().upper()
            and _str(a.get("official_event_id"))
            and a["official_event_id"] == b.get("official_event_id"))


def score_existing_team_state_candidate(
    feature_receipt: Mapping[str, Any], candidate_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Run exactly the existing standardized logistic + fitted calibrator.

    This function cannot turn a research candidate into a calibrated Full Model.
    Caller-supplied source labels and hashes do not independently authenticate
    the upstream evidence. A *post-event* candidate cannot become pregame.
    """
    bad = _out("INVALID_PREGAME_PROVENANCE", candidate_probability=None,
               raw_probability=None, calibrated_lower_bound=None,
               provenance_authenticated=False)
    if not isinstance(feature_receipt, Mapping) or not isinstance(candidate_artifact, Mapping):
        return bad
    if (feature_receipt.get("sport") != "NFL" or candidate_artifact.get("sport") != "NFL"
            or not all(_str(feature_receipt.get(x)) for x in (
                "official_event_id", "home_team", "away_team", "feature_source_id"))
            or feature_receipt.get("home_team") == feature_receipt.get("away_team")
            or feature_receipt.get("feature_schema_version") != FEATURE_SCHEMA):
        return bad
    feature_time = _time(feature_receipt.get("feature_as_of_utc"))
    snap = _time(feature_receipt.get("snapshot_at_utc"))
    start = _time(feature_receipt.get("event_start_utc"))
    if not (feature_time and snap and start and feature_time <= snap < start):
        return bad
    artifact_time = _time(candidate_artifact.get("model_created_at_utc"))
    fit_time = _time(candidate_artifact.get("fit_cutoff_utc"))
    cal_time = _time(candidate_artifact.get("calibration_cutoff_utc"))
    if not (artifact_time and fit_time and cal_time and fit_time <= cal_time <= artifact_time <= snap):
        return {**bad, "status": "MODEL_NOT_AVAILABLE_AT_SNAPSHOT"}
    if (candidate_artifact.get("lifecycle_state") != "CANDIDATE"
            or candidate_artifact.get("promoted") is not False
            or candidate_artifact.get("active") is not False
            or not _str(candidate_artifact.get("model_artifact_version"))):
        return {**bad, "status": "MODEL_LIFECYCLE_NOT_RESEARCH_CANDIDATE"}
    payload = candidate_artifact.get("artifact_payload")
    vector = feature_receipt.get("features")
    cal = candidate_artifact.get("calibrator_payload")
    if not all(isinstance(v, Mapping) for v in (payload, vector, cal)):
        return {**bad, "status": "ARTIFACT_OR_FEATURE_MISSING"}
    if not (_str(candidate_artifact.get("artifact_checksum"))
            and _str(candidate_artifact.get("calibrator_content_hash"))
            and _str(feature_receipt.get("feature_content_hash"))):
        return {**bad, "status": "PROVENANCE_HASH_MISSING"}
    try:
        artifact_valid = hash_payload(payload) == candidate_artifact["artifact_checksum"]
        vector_valid = hash_payload(vector) == feature_receipt["feature_content_hash"]
        calibrator_valid = hash_payload(cal) == candidate_artifact["calibrator_content_hash"]
    except (TypeError, ValueError):
        return {**bad, "status": "PROVENANCE_CONTENT_INVALID"}
    if not artifact_valid or not vector_valid:
        return {**bad, "status": "CONTENT_HASH_MISMATCH"}
    if not calibrator_valid:
        return {**bad, "status": "CALIBRATOR_HASH_MISMATCH"}
    if (payload.get("artifact_format") != "STANDARDIZED_LOGISTIC_JSON_V1"
            or payload.get("model_family") != MODEL_FAMILY):
        return {**bad, "status": "ARTIFACT_IDENTITY_INVALID"}
    names = payload.get("feature_names")
    means, scales, weights = (payload.get(k) for k in ("scaler_mean", "scaler_scale", "coefficients"))
    if (not isinstance(names, list) or not names or not all(_str(n) for n in names)
            or len(set(names)) != len(names) or set(names) != set(vector)
            or not all(isinstance(v, list) and len(v)==len(names) for v in (means, scales, weights))):
        return {**bad, "status": "FEATURE_SCHEMA_MISMATCH"}
    intercept = _number(payload.get("intercept"))
    fields = [[_number(v) for v in values] for values in (means, scales, weights)]
    xs = [_number(vector[name]) for name in names]
    if (intercept is None or any(v is None for group in fields for v in group)
            or any(v is None for v in xs) or any(x <= 0 for x in fields[1])):
        return {**bad, "status": "NONFINITE_MODEL_OR_FEATURE"}
    logit = intercept + sum((v - mean) / sd * coef for v, mean, sd, coef in
                            zip(xs, fields[0], fields[1], fields[2]))
    if not math.isfinite(logit):
        return {**bad, "status": "NUMERICAL_OVERFLOW"}
    raw = 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit)/(1+math.exp(logit))
    method = cal.get("method")
    if method == "IDENTITY_RAW_PROBABILITY_V1":
        predicted = min(1-1e-6, max(1e-6, raw))
    elif method == "EMPIRICAL_WILSON_BINS_V1":
        bins = cal.get("bins")
        if not isinstance(bins,list) or not bins:
            return {**bad,"status":"CALIBRATOR_INVALID"}
        options=[]
        for row in bins:
            if not isinstance(row,Mapping):
                return {**bad,"status":"CALIBRATOR_INVALID"}
            center = _number(row.get("raw_mean"),0,1)
            outcome = _number(row.get("calibrated_probability"),0,1)
            if center is None or outcome is None:
                return {**bad,"status":"CALIBRATOR_INVALID"}
            options.append((center,outcome))
        predicted = min(1-1e-6,max(1e-6,min(options,key=lambda a:abs(a[0]-raw))[1]))
    else:
        return {**bad,"status":"CALIBRATOR_INVALID"}
    return _out("SHADOW_CANDIDATE_ONLY" if candidate_artifact.get("research_screen_pass") is True
                else "RESEARCH_SCREEN_FAILED",
                candidate_probability=predicted, raw_probability=raw,
                calibrated_lower_bound=None,
                model_artifact_version=candidate_artifact["model_artifact_version"],
                official_event_id=feature_receipt["official_event_id"],
                sport="NFL",home_team=feature_receipt["home_team"],
                away_team=feature_receipt["away_team"],
                event_start_utc=feature_receipt["event_start_utc"],
                snapshot_at_utc=feature_receipt["snapshot_at_utc"],
                model_created_at_utc=candidate_artifact["model_created_at_utc"],
                artifact_checksum=candidate_artifact["artifact_checksum"],
                calibrator_content_hash=candidate_artifact["calibrator_content_hash"],
                feature_content_hash=feature_receipt["feature_content_hash"],
                feature_source_id=feature_receipt["feature_source_id"],
                source_review_status=candidate_artifact.get("source_review_status"),
                candidate_probability_is_certified=False,
                provenance_authenticated=False)


def bridge_team_script_to_prop(team_scenario: Mapping[str, Any],
                               prop_scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Cross-lane score-state mix; never infer leading weights from a ML price."""
    bad=_out("SCENARIO_EVIDENCE_INSUFFICIENT", expected_count=None,
             variance=None,p_over=None)
    if not isinstance(team_scenario, Mapping) or not isinstance(prop_scenario, Mapping):
        return bad
    if (not _match(team_scenario,prop_scenario) or team_scenario.get("source_type") != "LLP_TEAM_BETTING_ENGINE"
            or prop_scenario.get("source_type") != "WOW_PROP_LANE"
            or team_scenario.get("probability_basis") != "FITTED_TEAM_STATE"
            or not _str(team_scenario.get("team"))
            or team_scenario.get("team") != prop_scenario.get("team")
            or not all(_str(team_scenario.get(k)) for k in ("team_event_model_artifact_hash","scenario_receipt_id"))
            or not all(_str(prop_scenario.get(k)) for k in ("player_id","stat","prop_fit_artifact_hash",
                                                              "prop_source_snapshot_hash"))
            or _number(prop_scenario.get("exact_line"),0) is None):
        return bad
    start=_time(team_scenario.get("event_start_utc"))
    if not start or _time(prop_scenario.get("event_start_utc")) != start:
        return bad
    states_time=_time(team_scenario.get("scenario_frozen_at_utc"))
    team_fit=_time(team_scenario.get("scenario_fit_cutoff_utc"))
    prop_fit=_time(prop_scenario.get("prop_fit_cutoff_utc"))
    team_snap=_time(team_scenario.get("snapshot_at_utc"))
    prop_snap=_time(prop_scenario.get("snapshot_at_utc"))
    if not all(v is not None for v in (states_time,team_fit,prop_fit,team_snap,prop_snap)):
        return bad
    if not (team_fit <= states_time <= team_snap < start and prop_fit <= prop_snap < start
            and states_time <= prop_snap):
        return bad
    tw,ps=team_scenario.get("states"),prop_scenario.get("states")
    if not isinstance(tw,list) or not isinstance(ps,list) or len(tw)!=3 or len(ps)!=3:
        return bad
    state_names={"LEADING","TIED","TRAILING"}
    tmap={};pmap={}
    for r in tw:
        if not isinstance(r,Mapping):return bad
        name=r.get("state")
        if name not in state_names or name in tmap:return bad
        weight=_number(r.get("weight"),0,1)
        if weight is None:return bad
        tmap[name]=weight
    for r in ps:
        if not isinstance(r,Mapping):return bad
        name=r.get("state")
        if name not in state_names or name in pmap:return bad
        m=_number(r.get("expected_count"),0)
        v=_number(r.get("count_variance"),0)
        o=_number(r.get("p_over_exact_line"),0,1)
        if None in (m,v,o):return bad
        pmap[name]=(m,v,o)
    if set(tmap)!=state_names or set(pmap)!=state_names or abs(sum(tmap.values())-1)>1e-6:
        return bad
    mean=sum(tmap[k]*pmap[k][0] for k in state_names)
    var=sum(tmap[k]*(pmap[k][1]+(pmap[k][0]-mean)**2) for k in state_names)
    over=sum(tmap[k]*pmap[k][2] for k in state_names)
    return _out("CONDITIONAL_USAGE_RESEARCH_ONLY",expected_count=mean,
                variance=var,p_over=over,team_scenario_receipt_id=team_scenario["scenario_receipt_id"],
                player_id=prop_scenario["player_id"], exact_line=prop_scenario["exact_line"],
                source_dependency="LLP_TEAM_BETTING_ENGINE_PLUS_WOW_PROP_LANE",
                probability_is_calibrated=False)


def build_paired_forward_row(incumbent: Mapping[str,Any],
                             challenger: Mapping[str,Any],
                             settlement: Mapping[str,Any]) -> dict[str,Any]:
    """Pair forward-frozen research probabilities, never certify raw estimates."""
    bad=_out("INVALID_PAIR",baseline_probability=None,
             challenger_probability=None,challenger_lower_bound=None,
             certification_eligible=False)
    if not all(isinstance(x,Mapping) for x in (incumbent,challenger,settlement)):
        return bad
    if not _match(incumbent,challenger) or not _match(incumbent,settlement):
        return bad
    chosen = incumbent.get("selected_participant")
    if (chosen not in (challenger.get("home_team"), challenger.get("away_team"))
            or chosen != settlement.get("selected_participant")
            or any(x.get("sport") != "NFL" for x in (incumbent,challenger,settlement))
            or incumbent.get("market_family") != "MONEYLINE"
            or incumbent.get("market_period") != settlement.get("market_period")
            or incumbent.get("settlement_basis") != settlement.get("settlement_basis")
            or incumbent.get("market_family") != settlement.get("market_family")
            or challenger.get("status") not in ("RESEARCH_SCREEN_FAILED","SHADOW_CANDIDATE_ONLY")
            or not all(_str(incumbent.get(k)) for k in ("receipt_id","model_artifact_hash","source_snapshot_hash"))
            or not _str(settlement.get("source_id"))
            or settlement.get("source_type") != "INDEPENDENT_OFFICIAL"
            or type(settlement.get("outcome")) is not bool):
        return bad
    start=_time(incumbent.get("event_start_utc"))
    if not start or _time(challenger.get("event_start_utc")) != start:
        return bad
    incumbent_at=_time(incumbent.get("prediction_created_at_utc"))
    chall_at=_time(challenger.get("snapshot_at_utc"))
    final_at=_time(settlement.get("finalized_at_utc"))
    if not (incumbent_at and chall_at and final_at and incumbent_at < start and chall_at < start <= final_at):
        return bad
    bp=_number(incumbent.get("probability"),0,1)
    home_cb=_number(challenger.get("candidate_probability"),0,1)
    cb = (home_cb if chosen == challenger.get("home_team") else 1 - home_cb) if home_cb is not None else None
    lb=_number(incumbent.get("calibrated_lower_bound"),0,1)
    if bp is None or cb is None or lb is None or lb>bp:
        return bad
    y=int(settlement["outcome"])
    return _out("RESEARCH_ONLY_UNCALIBRATED_PAIR",baseline_probability=bp,
                challenger_probability=cb,challenger_lower_bound=None,
                baseline_lower_bound=lb,
                baseline_brier=(bp-y)**2,challenger_brier=(cb-y)**2,
                candidate_probability_is_certified=False,
                official_event_id=incumbent["official_event_id"],
                incumbent_receipt_id=incumbent["receipt_id"],
                candidate_model_artifact_version=challenger.get("model_artifact_version"),
                selected_participant=chosen,
                candidate_probability_basis="HOME_LOGIT_OR_COMPLEMENT_OF_HOME", 
                settlement_source_id=settlement["source_id"],
                certification_eligible=False)
