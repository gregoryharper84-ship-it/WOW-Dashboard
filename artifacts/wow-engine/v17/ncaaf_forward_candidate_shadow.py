"""Read-only prospective fitted *research* inference for NCAAF result/form candidates.

This deliberately does not register a production bridge or issue a forecast.
A D1 research candidate is not a certified specialist; source, independent
verification, calibration bounds, immutable Action and reducer remain separate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from collections.abc import Mapping, Sequence
from math import exp, isfinite, log1p
import json
from typing import Any

CAN_EXECUTE = False
MODEL_FAMILY = "NCAAF_RESULT_FORM_LOGIT_V1"
FEATURE_SCHEMA = "NCAAF_RESULT_FORM_PRIOR_V1"
SOURCE_POLICY = "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1"
FEATURES = (
    "home_win_rate_prior", "away_win_rate_prior", "home_point_diff_prior",
    "away_point_diff_prior", "home_games_prior_log", "away_games_prior_log",
    "home_rest_days_capped", "away_rest_days_capped", "neutral_site",
)


class ShadowScoringBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ShadowScoringBlocked(code)


def _when(value: Any) -> datetime:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ShadowScoringBlocked("NCAAF_SHADOW_TIME_INVALID") from exc
    _require(dt.utcoffset() is not None, "NCAAF_SHADOW_TIME_INVALID")
    return dt.astimezone(timezone.utc)


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _candidate(candidate: Mapping[str, Any], as_of: datetime) -> Mapping[str, Any]:
    _require(
        candidate.get("sport") == "NCAAF"
        and candidate.get("league") == "NCAAF"
        and candidate.get("market_family") == "OUTRIGHT_WINNER"
        and candidate.get("model_family") == MODEL_FAMILY
        and candidate.get("feature_schema_version") == FEATURE_SCHEMA
        and candidate.get("source_policy_id") == SOURCE_POLICY
        and candidate.get("lifecycle_state") == "CANDIDATE"
        and candidate.get("research_screen_pass") is True
        and all(candidate.get(x) is False for x in (
            "active", "promoted", "probability_publishable", "can_execute",
            "automatic_certification", "automatic_promotion",
        )),
        "NCAAF_SHADOW_CANDIDATE_LIFECYCLE_INVALID",
    )
    _require(_when(candidate.get("created_at")) <= as_of, "NCAAF_SHADOW_CANDIDATE_POST_AS_OF")
    data_hash, sha = str(candidate.get("training_dataset_hash") or ""), str(candidate.get("training_code_sha") or "")
    _require(len(data_hash) == 64 and len(sha) >= 12 and candidate.get("model_artifact_version") == f"{MODEL_FAMILY}_{data_hash[:16]}_{sha[:12]}", "NCAAF_SHADOW_CANDIDATE_VERSION_INVALID")
    payload = candidate.get("artifact_payload")
    _require(isinstance(payload, Mapping), "NCAAF_SHADOW_ARTIFACT_MISSING")
    _require(_digest(payload) == candidate.get("artifact_checksum"), "NCAAF_SHADOW_ARTIFACT_CHECKSUM_MISMATCH")
    _require(payload.get("model_family") == MODEL_FAMILY and payload.get("artifact_format") == "STANDARDIZED_LOGISTIC_JSON_V1"
             and payload.get("split_policy") == "CHRONOLOGICAL_60_20_20"
             and tuple(payload.get("feature_names") or ()) == FEATURES,
             "NCAAF_SHADOW_ARTIFACT_SCHEMA_INVALID")
    for name in ("coefficients", "scaler_mean", "scaler_scale"):
        values = payload.get(name)
        _require(isinstance(values, list) and len(values) == len(FEATURES) and all(
            type(v) in (int, float) and isfinite(float(v)) for v in values
        ), "NCAAF_SHADOW_ARTIFACT_NUMERIC_INVALID")
    _require(all(float(x) > 0 for x in payload["scaler_scale"])
             and type(payload.get("intercept")) in (int, float)
             and isfinite(float(payload["intercept"])), "NCAAF_SHADOW_ARTIFACT_NUMERIC_INVALID")
    cal = candidate.get("calibrator_payload")
    _require(isinstance(cal, Mapping) and cal.get("method") == "IDENTITY_RAW_PROBABILITY_V1"
             and cal.get("bins") == []
             and cal.get("selection_version") == "FORWARD_CALIBRATION_SELECTION_V2"
             and isinstance(cal.get("selection"), Mapping)
             and cal["selection"].get("test_block_used_for_selection") is False,
             "NCAAF_SHADOW_CALIBRATOR_UNSUPPORTED")
    return payload


def score_forward_shadow(
    identity: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any],
    *,
    as_of_utc: Any,
) -> dict[str, Any]:
    """Compute a genuine fitted model shadow P, never a certified publication P.

    Identity is an upstream input; callers must independently prove its
    provenance before ever exposing a new API. This pure function does not
    establish authority from caller-provided identifiers.
    """
    now = _when(as_of_utc)
    kickoff = _when(identity.get("event_start_time"))
    _require(kickoff > now, "NCAAF_SHADOW_NOT_PREGAME")
    home, away = str(identity.get("home_team") or "").strip(), str(identity.get("away_team") or "").strip()
    _require(home and away and home != away
             and isinstance(identity.get("neutral_site"), bool)
             and bool(identity.get("event_id"))
             and identity.get("identity_provider") == "CFBD:/games"
             and identity.get("identity_resolution") == "CFBD_EXACT_PARTICIPANTS_START_MATCH",
             "NCAAF_SHADOW_CANONICAL_IDENTITY_UNVERIFIED")
    payload = _candidate(candidate, now)
    seen = set()
    prior = {home: [], away: []}
    used = []
    for row in history:
        _require(isinstance(row, Mapping), "NCAAF_SHADOW_HISTORY_INVALID")
        h, a = str(row.get("home_team") or ""), str(row.get("away_team") or "")
        if home not in (h, a) and away not in (h, a):
            continue
        eid = str(row.get("official_event_id") or "")
        _require(eid and eid != str(identity["event_id"]) and eid not in seen, "NCAAF_SHADOW_HISTORY_DUPLICATE")
        seen.add(eid)
        when = _when(row.get("event_start_time"))
        sourced = _when(row.get("result_source_timestamp"))
        _require(when < now and when < kickoff and when <= sourced <= now,
                 "NCAAF_SHADOW_HISTORY_NOT_AS_OF")
        _require(row.get("result_source") == "CFBD:/games", "NCAAF_SHADOW_SOURCE_UNVERIFIED")
        hp, ap, won = row.get("home_points"), row.get("away_points"), row.get("home_won")
        _require(type(hp) is int and type(ap) is int and hp != ap and type(won) is bool
                 and won == (hp > ap), "NCAAF_SHADOW_HISTORY_OUTCOME_INCONSISTENT")
        used.append((eid,when.isoformat(),sourced.isoformat(),h,a,hp,ap))
        if h in prior:
            prior[h].append((when, hp > ap, hp - ap))
        if a in prior:
            prior[a].append((when, ap > hp, ap - hp))
    _require(all(len(rows) >= 3 for rows in prior.values()), "NCAAF_SHADOW_PRIOR_SAMPLE_INSUFFICIENT")
    values = {}
    for label, team in (("home", home), ("away", away)):
        games = sorted(prior[team], key=lambda x:x[0])
        values[f"{label}_win_rate_prior"] = sum(int(x[1]) for x in games) / len(games)
        values[f"{label}_point_diff_prior"] = sum(x[2] for x in games) / len(games)
        values[f"{label}_games_prior_log"] = log1p(len(games))
        values[f"{label}_rest_days_capped"] = min(28., max(0., (kickoff-games[-1][0]).total_seconds()/86400))
    values["neutral_site"] = float(identity["neutral_site"])
    z = float(payload["intercept"])
    for i,name in enumerate(FEATURES):
        z += float(payload["coefficients"][i]) * ((float(values[name])-float(payload["scaler_mean"][i]))/float(payload["scaler_scale"][i]))
    prob = 1/(1+exp(-z)) if z >= 0 else exp(z)/(1+exp(z))
    return {
        "status": "FITTED_SHADOW_ONLY", "sport": "NCAAF", "official_event_id": str(identity["event_id"]),
        "as_of_utc": now.isoformat(), "event_start_time_utc": kickoff.isoformat(),
        "model_family": MODEL_FAMILY, "feature_schema_version": FEATURE_SCHEMA,
        "model_artifact_version": candidate["model_artifact_version"], "model_artifact_checksum": candidate["artifact_checksum"],
        "candidate_id": candidate.get("candidate_id"), "source_history_digest": _digest(sorted(used)),
        "historical_home_games":len(prior[home]), "historical_away_games":len(prior[away]),
        "features": {name: float(values[name]) for name in FEATURES},
        "research_probability_home":round(prob, 9), "research_probability_away":round(1-prob, 9),
        "calibrator_method": "IDENTITY_RAW_PROBABILITY_V1", "calibrated_lower_bound":None,
        "blocking_reason":"INDEPENDENT_CERTIFICATION_AND_PREGAME_SOURCE_ACCEPTANCE_REQUIRED",
        "immutable_prediction_receipt":None, "automatic_certification":False,"automatic_promotion":False,
        "probability_publishable":False,"rank_eligible":False,"can_execute":False,
    }


def run_fitted_ncaaf_shadow(
    db: Any,
    *,
    event_start_time: str,
    home_team: str,
    away_team: str,
    as_of_utc: Any | None = None,
    identity_resolver: Any | None = None,
) -> dict[str, Any]:
    """Read-only server-side candidate-backed scorer, not an Action or publisher.

    Production callers must not accept a caller-supplied identity_resolver or
    as_of_utc; those injection points are only for deterministic research tests.
    The canonical CFBD resolver is the only normal source of event identity.
    """
    now = _when(as_of_utc if as_of_utc is not None else datetime.now(timezone.utc))
    try:
        if identity_resolver is None:
            from v17.ncaaf_event_identity import resolve_ncaaf_current_event_identity
            identity_resolver = resolve_ncaaf_current_event_identity
        identity = identity_resolver(
            event_start_time=event_start_time,home_team=home_team,away_team=away_team,
        )
    except Exception as exc:
        raise ShadowScoringBlocked("NCAAF_SHADOW_CANONICAL_SOURCE_UNAVAILABLE") from exc
    _require(isinstance(identity, Mapping), "NCAAF_SHADOW_CANONICAL_IDENTITY_UNVERIFIED")

    try:
        result = (db.table("wow_d1_candidate_artifacts")
                  .select("*")
                  .eq("sport", "NCAAF")
                  .eq("model_family", MODEL_FAMILY)
                  .order("created_at", desc=True)
                  .limit(1).execute())
        candidates = getattr(result, "data", None)
    except Exception as exc:
        raise ShadowScoringBlocked("NCAAF_SHADOW_MODEL_REGISTRY_READ_FAILED") from exc
    _require(isinstance(candidates,list) and len(candidates)==1,
             "NCAAF_SHADOW_FITTED_CANDIDATE_NOT_FOUND")
    candidate = candidates[0]
    history = []
    start = 0
    while True:
        try:
            response = (db.table("wow_ncaaf_training_games")
                        .select("official_event_id,event_start_time,home_team,away_team,home_points,away_points,home_won,result_source,result_source_timestamp")
                        .order("event_start_time")
                        .range(start,start+999).execute())
            batch = getattr(response,"data",None)
        except Exception as exc:
            raise ShadowScoringBlocked("NCAAF_SHADOW_HISTORY_READ_FAILED") from exc
        _require(isinstance(batch,list) and all(isinstance(row,Mapping) for row in batch),
                 "NCAAF_SHADOW_HISTORY_READ_FAILED")
        history.extend(batch)
        if len(batch)<1000:
            break
        start += 1000
    return score_forward_shadow(identity,history,candidate,as_of_utc=now)


__all__ = ["CAN_EXECUTE","MODEL_FAMILY","FEATURE_SCHEMA","ShadowScoringBlocked","score_forward_shadow","run_fitted_ncaaf_shadow"]