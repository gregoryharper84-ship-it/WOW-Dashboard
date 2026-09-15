"""Governed V17 dynamic team-state challenger training.

Implements the 1-11 LLP accuracy feature program as CANDIDATE evidence only.
It never self-certifies, self-promotes, publishes a probability, or executes.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from v17.binary_candidate_lifecycle import BinaryCandidateError, BinaryTrainingRow, train_binary_candidate
from v17.multiclass_candidate_lifecycle import MulticlassCandidateError, MulticlassTrainingRow, train_multiclass_candidate
from v17.team_state_intelligence import FEATURE_FAMILY_VERSION, build_team_state, champion_challenger_metrics, paired_matchup_features

CAN_EXECUTE = False
MODEL_PROGRAM = "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1"


class TeamStateChallengerUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message); self.code = code


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _prior_strength(history: Sequence[Mapping[str, Any]]) -> float:
    prior = list(history)[-10:]
    return sum(float(row.get("point_diff") or 0.0) for row in prior) / len(prior) if prior else 0.0


def _style(row: Mapping[str, Any], side: str) -> dict[str, float]:
    return {"attack_style_index": float(row.get(f"{side}_attack_style_index") or 0.0),
            "defense_style_index": float(row.get(f"{side}_defense_style_index") or 0.0),
            "pace_or_tempo_index": float(row.get(f"{side}_pace_or_tempo_index") or 0.0)}


def _append_history(history: dict[str, list[dict[str, Any]]], event: Mapping[str, Any], *,
                    home: str, away: str, start: datetime, home_score: float, away_score: float) -> None:
    home_tags = event.get("home_structural_tags") or []; away_tags = event.get("away_structural_tags") or []
    home_tags = [home_tags] if isinstance(home_tags, str) else list(home_tags)
    away_tags = [away_tags] if isinstance(away_tags, str) else list(away_tags)
    hline = list(event.get("home_history_lineup_ids") or event.get("home_lineup_ids") or [])
    aline = list(event.get("away_history_lineup_ids") or event.get("away_lineup_ids") or [])
    prev_hline = list((history.get(home) or [{}])[-1].get("lineup_ids") or [])
    prev_aline = list((history.get(away) or [{}])[-1].get("lineup_ids") or [])
    if hline and prev_hline and set(hline) != set(prev_hline): home_tags.append("LINEUP_OR_STARTER_CHANGE")
    if aline and prev_aline and set(aline) != set(prev_aline): away_tags.append("LINEUP_OR_STARTER_CHANGE")
    history.setdefault(home, []).append({
        "event_time": start.isoformat(), "season": event.get("season"), "won": home_score > away_score,
        "point_diff": home_score-away_score,
        "process_margin": float(event.get("home_process_margin") if event.get("home_process_margin") is not None else home_score-away_score),
        "opponent_strength_prior": _prior_strength(history.get(away, [])),
        "travel_load": float(event.get("home_travel_load") or 0), "congestion": float(event.get("home_congestion") or 0),
        "roster_ids": list(event.get("home_history_roster_ids") or event.get("home_roster_ids") or []),
        "lineup_ids": hline, "structural_tags": list(event.get("home_history_structural_tags") or home_tags), **_style(event,"home")})
    history.setdefault(away, []).append({
        "event_time": start.isoformat(), "season": event.get("season"), "won": away_score > home_score,
        "point_diff": away_score-home_score,
        "process_margin": float(event.get("away_process_margin") if event.get("away_process_margin") is not None else away_score-home_score),
        "opponent_strength_prior": _prior_strength(history.get(home, [])),
        "travel_load": float(event.get("away_travel_load") or 0), "congestion": float(event.get("away_congestion") or 0),
        "roster_ids": list(event.get("away_history_roster_ids") or event.get("away_roster_ids") or []),
        "lineup_ids": aline, "structural_tags": list(event.get("away_history_structural_tags") or away_tags), **_style(event,"away")})


def _states(history: dict[str, list[dict[str, Any]]], event: Mapping[str, Any], home: str, away: str,
            start: datetime, expected: int | None):
    hh, ah = history.get(home, []), history.get(away, [])
    hs = build_team_state(hh, target_time=start, expected_season_games=expected,
                          current_roster=event.get("home_roster_ids"), current_lineup=event.get("home_lineup_ids"),
                          target_season=event.get("season"))
    aws = build_team_state(ah, target_time=start, expected_season_games=expected,
                           current_roster=event.get("away_roster_ids"), current_lineup=event.get("away_lineup_ids"),
                           target_season=event.get("season"))
    return hh, ah, hs, aws


def build_dynamic_binary_rows(events: Sequence[Mapping[str, Any]], *, expected_season_games: int | None,
                              min_prior_games: int = 5):
    history: dict[str,list[dict[str,Any]]] = {}; rows=[]; metadata=[]; names=None
    for event in sorted(events, key=lambda r: (_dt(r["event_start_time"]), str(r["event_id"]))):
        start=_dt(event["event_start_time"]); home,away=str(event["home_team"]),str(event["away_team"])
        hs,aws=float(event["home_score"]),float(event["away_score"])
        if hs == aws:
            _append_history(history,event,home=home,away=away,start=start,home_score=hs,away_score=aws); continue
        hh,ah=history.get(home,[]),history.get(away,[])
        if len(hh)>=min_prior_games and len(ah)>=min_prior_games:
            _,_,home_state,away_state=_states(history,event,home,away,start,expected_season_games)
            features=paired_matchup_features(home_state,away_state); names=names or tuple(sorted(features))
            vector={name:float(features[name]) for name in names}
            manifest={"program":MODEL_PROGRAM,"feature_family_version":FEATURE_FAMILY_VERSION,
                      "event_id":str(event["event_id"]),"home_prior_events":len(hh),"away_prior_events":len(ah),
                      "market_features_used":False,"manual_probability_adjustments":False,
                      "source_manifest":dict(event.get("source_manifest") or {})}
            rows.append(BinaryTrainingRow(str(event["event_id"]),start.isoformat(),(start-timedelta(seconds=1)).isoformat(),
                                          hs>aws,vector,_hash(manifest)))
            metadata.append({"source_manifest":manifest,"home_team_state":asdict(home_state),"away_team_state":asdict(away_state)})
        _append_history(history,event,home=home,away=away,start=start,home_score=hs,away_score=aws)
    if not rows or names is None: raise TeamStateChallengerUnavailable("TEAM_STATE_BINARY_ROWS_EMPTY","no leakage-safe dynamic rows")
    return rows,metadata,names


def build_dynamic_multiclass_rows(events: Sequence[Mapping[str, Any]], *, expected_season_games: int | None,
                                  min_prior_games: int = 5):
    history: dict[str,list[dict[str,Any]]] = {}; rows=[]; metadata=[]; names=None
    for event in sorted(events, key=lambda r: (_dt(r["event_start_time"]),str(r["event_id"]))):
        start=_dt(event["event_start_time"]); home,away=str(event["home_team"]),str(event["away_team"])
        hs,aws=float(event["home_score"]),float(event["away_score"]); hh,ah=history.get(home,[]),history.get(away,[])
        if len(hh)>=min_prior_games and len(ah)>=min_prior_games:
            _,_,home_state,away_state=_states(history,event,home,away,start,expected_season_games)
            features=paired_matchup_features(home_state,away_state); names=names or tuple(sorted(features))
            outcome="HOME" if hs>aws else ("AWAY" if aws>hs else "DRAW")
            manifest={"program":MODEL_PROGRAM,"feature_family_version":FEATURE_FAMILY_VERSION,"event_id":str(event["event_id"]),
                      "outcome_space":["HOME","DRAW","AWAY"],"market_features_used":False,
                      "manual_probability_adjustments":False,"source_manifest":dict(event.get("source_manifest") or {})}
            rows.append(MulticlassTrainingRow(str(event["event_id"]),start.isoformat(),(start-timedelta(seconds=1)).isoformat(),
                                              outcome,{name:float(features[name]) for name in names},_hash(manifest)))
            metadata.append({"source_manifest":manifest,"home_team_state":asdict(home_state),"away_team_state":asdict(away_state)})
        _append_history(history,event,home=home,away=away,start=start,home_score=hs,away_score=aws)
    if not rows or names is None: raise TeamStateChallengerUnavailable("TEAM_STATE_MULTICLASS_ROWS_EMPTY","no leakage-safe dynamic rows")
    return rows,metadata,names


def _persist_rows(client: Any, *, sport: str, league: str, schema: str, model_family: str,
                  rows: Sequence[Any], metadata: Sequence[Mapping[str, Any]], multiclass: bool) -> None:
    payloads=[]
    for row,meta in zip(rows,metadata):
        outcome={"outcome":row.outcome} if multiclass else {"home_win":bool(row.positive_outcome)}
        payloads.append({"sport":sport,"league":league,"official_event_id":row.event_id,"event_start_time":row.event_start_time,
                         "feature_as_of":row.feature_as_of,"feature_schema_version":schema,"model_family":model_family,
                         "features":dict(row.features),"outcome_json":outcome,"source_manifest":dict(meta["source_manifest"]),
                         "source_manifest_sha256":row.source_manifest_sha256,"historical_reconstruction":True,
                         "archived_pregame_snapshot":False,"market_features_used":False,"can_execute":False})
    for offset in range(0,len(payloads),250):
        client.table("wow_d1_training_rows").upsert(payloads[offset:offset+250],
            on_conflict="sport,official_event_id,feature_schema_version,source_manifest_sha256").execute()


def _persist_artifact(client: Any, *, sport: str, league: str, family: str, schema: str,
                      training_code_sha: str, candidate: Any, metrics: dict[str,Any]) -> str:
    artifact=dict(candidate.artifact_payload); version=f"{family}_{candidate.dataset_hash[:16]}_{training_code_sha[:12]}"
    client.table("wow_d1_candidate_artifacts").upsert({
        "sport":sport,"league":league,"market_family":"OUTRIGHT_WINNER","model_family":family,
        "model_artifact_version":version,"feature_schema_version":schema,"source_policy_id":"TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1",
        "training_dataset_hash":candidate.dataset_hash,"training_code_sha":training_code_sha,"artifact_checksum":_hash(artifact),
        "artifact_payload":artifact,"calibrator_payload":dict(candidate.calibrator_payload),"validation_metrics":metrics,
        "training_rows":candidate.metrics.train_n,"calibration_rows":candidate.metrics.calibration_n,"test_rows":candidate.metrics.test_n,
        "research_screen_pass":candidate.research_screen_pass,"source_review_status":"DYNAMIC_PRIOR_RECONSTRUCTION_READY",
        "lifecycle_state":"CANDIDATE","promoted":False,"active":False,"automatic_certification":False,
        "automatic_promotion":False,"probability_publishable":False,"can_execute":False},
        on_conflict="model_artifact_version").execute()
    return version


def train_binary_challenger(client: Any, *, sport: str, league: str, events: Sequence[Mapping[str,Any]],
                            expected_season_games: int | None, training_code_sha: str, min_rows: int = 300) -> dict[str,Any]:
    rows,metadata,names=build_dynamic_binary_rows(events,expected_season_games=expected_season_games)
    family=f"{sport}_DYNAMIC_TEAM_STATE_LOGIT_V2"; schema=f"{sport}_DYNAMIC_TEAM_STATE_FEATURES_V2"
    try: candidate=train_binary_candidate(rows,model_family=family,feature_names=names,min_rows=min_rows)
    except BinaryCandidateError as exc: raise TeamStateChallengerUnavailable(exc.code,str(exc)) from exc
    _persist_rows(client,sport=sport,league=league,schema=schema,model_family=family,rows=rows,metadata=metadata,multiclass=False)
    metrics=asdict(candidate.metrics)|{"research_screen_pass":candidate.research_screen_pass,"feature_family_version":FEATURE_FAMILY_VERSION,
             "manual_probability_adjustments":False,"market_features_used":False,"champion_challenger_required":True}
    version=_persist_artifact(client,sport=sport,league=league,family=family,schema=schema,training_code_sha=training_code_sha,candidate=candidate,metrics=metrics)
    return {"sport":sport,"league":league,"model_family":family,"feature_schema_version":schema,"model_artifact_version":version,
            "eligible_rows":len(rows),"feature_count":len(names),"metrics":metrics,"research_screen_pass":candidate.research_screen_pass,
            "lifecycle_state":"CANDIDATE","automatic_certification":False,"automatic_promotion":False,
            "probability_publishable":False,"can_execute":False}


def train_multiclass_challenger(client: Any, *, sport: str, league: str, events: Sequence[Mapping[str,Any]],
                                expected_season_games: int | None, training_code_sha: str, min_rows: int = 500) -> dict[str,Any]:
    rows,metadata,names=build_dynamic_multiclass_rows(events,expected_season_games=expected_season_games)
    family=f"{sport}_{league}_DYNAMIC_TEAM_STATE_1X2_V2"; schema=f"{sport}_{league}_DYNAMIC_TEAM_STATE_FEATURES_V2"
    try: candidate=train_multiclass_candidate(rows,model_family=family,feature_names=names,expected_classes=("HOME","DRAW","AWAY"),min_rows=min_rows)
    except MulticlassCandidateError as exc: raise TeamStateChallengerUnavailable(exc.code,str(exc)) from exc
    _persist_rows(client,sport=sport,league=league,schema=schema,model_family=family,rows=rows,metadata=metadata,multiclass=True)
    metrics=asdict(candidate.metrics)|{"research_screen_pass":candidate.research_screen_pass,"feature_family_version":FEATURE_FAMILY_VERSION,
             "outcome_space":["HOME","DRAW","AWAY"],"manual_probability_adjustments":False,"market_features_used":False,"champion_challenger_required":True}
    version=_persist_artifact(client,sport=sport,league=league,family=family,schema=schema,training_code_sha=training_code_sha,candidate=candidate,metrics=metrics)
    return {"sport":sport,"league":league,"model_family":family,"feature_schema_version":schema,"model_artifact_version":version,
            "eligible_rows":len(rows),"feature_count":len(names),"metrics":metrics,"research_screen_pass":candidate.research_screen_pass,
            "lifecycle_state":"CANDIDATE","automatic_certification":False,"automatic_promotion":False,
            "probability_publishable":False,"can_execute":False}


def postmortem_feature_snapshot(*, prediction_id: str, home_state: Mapping[str,Any], away_state: Mapping[str,Any],
                                champion_probability: float|None=None, challenger_probability: float|None=None) -> dict[str,Any]:
    flags=[]
    for side,state in (("HOME",home_state),("AWAY",away_state)):
        for field,tag in (("streak_without_driver","STREAK_WITHOUT_DRIVER"),("results_process_divergence","RESULTS_PROCESS_DIVERGENCE"),
                          ("form_schedule_inflated","FORM_SCHEDULE_INFLATED"),("form_schedule_suppressed","FORM_SCHEDULE_SUPPRESSED")):
            if float(state.get(field) or 0)>=1: flags.append(f"{side}:{tag}")
    return {"prediction_id":prediction_id,"feature_family_version":FEATURE_FAMILY_VERSION,"home_team_state":dict(home_state),
            "away_team_state":dict(away_state),"diagnostic_flags":flags,"champion_probability":champion_probability,
            "challenger_probability":challenger_probability,"probability_rewritten":False,"can_execute":False}


def compare_shadow_predictions(rows: Sequence[Mapping[str,Any]]) -> dict[str,Any]:
    return champion_challenger_metrics(rows)


__all__=["CAN_EXECUTE","MODEL_PROGRAM","TeamStateChallengerUnavailable","build_dynamic_binary_rows","build_dynamic_multiclass_rows",
         "train_binary_challenger","train_multiclass_challenger","postmortem_feature_snapshot","compare_shadow_predictions"]
