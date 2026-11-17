"""Offline, chronological NFL challenger replay with explicit provenance boundaries.

Only evaluates historical *prior-stat* features. Many feature rows were built
retrospectively; even improved holdout scores cannot certify pregame model lift.
No live scoring, source acquisition, database writes, promotion, or wagers.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import exp, isfinite, log, sqrt
from random import Random
import json
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
_SHA_LENGTH = 64


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if isfinite(x) else None


def _date(v: Any) -> date | None:
    if not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v)
    except (ValueError, OverflowError):
        return None


def _utc(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        x = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return x.astimezone(timezone.utc) if x.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _sha(v: Any) -> bool:
    return isinstance(v, str) and len(v) == _SHA_LENGTH and all(ch in "0123456789abcdef" for ch in v)


def classify_feature_receipt(row: Mapping[str, Any]) -> str:
    """Source hashes are syntactic, not cryptographic independent attestations."""
    if not isinstance(row, Mapping):
        return "FEATURE_CHRONOLOGY_INVALID"
    day, cutoff, prior = _date(row.get("gameday")), _date(row.get("feature_cutoff_date")), _date(row.get("max_prior_gameday"))
    if day is None or cutoff != day or prior is None or prior >= day:
        return "FEATURE_CHRONOLOGY_INVALID"
    hashes = row.get("source_content_sha256s")
    if (not isinstance(hashes, list) or not hashes or not all(_sha(h) for h in hashes)
            or not _sha(row.get("row_inputs_hash"))):
        return "SOURCE_MANIFEST_MISSING"
    lock, start = _utc(row.get("locked_at")), _utc(row.get("event_start_time_utc"))
    if lock is None or start is None:
        return "PREGAME_TIMESTAMP_UNVERIFIED"
    if start.date() not in (day, day + timedelta(days=1)):
        # NFL local calendar days may cross midnight UTC. Other mismatches
        # require independent official fixture resolution.
        return "PREGAME_TIMESTAMP_UNVERIFIED"
    return "PRE_EVENT_LOCKED_UNAUTHENTICATED" if lock < start else "RETROSPECTIVE_PRIOR_ONLY"


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + exp(-max(-35.0, min(35.0, z))))


def _clip(p: float) -> float:
    return max(1e-6, min(1 - 1e-6, p))


def _score(rows: Sequence[tuple[int, float]]) -> dict[str, Any] | None:
    if not rows:
        return None
    n = len(rows)
    return {
        "n": n,
        "brier": sum((p-y)**2 for y,p in rows)/n,
        "log_loss": sum(-(y*log(_clip(p))+(1-y)*log(1-_clip(p))) for y,p in rows)/n,
        "accuracy": sum((p>=.5)==bool(y) for y,p in rows)/n,
        "observed_home_rate": sum(y for y,_ in rows)/n,
        "mean_home_probability": sum(p for _,p in rows)/n,
    }


def paired_cluster_ci(deltas: Sequence[tuple[int, float]], *, seed: int = 20261117, draws: int = 2000) -> tuple[float, float] | None:
    """Cluster bootstrap for week-level paired Brier differences (new-old)."""
    if not deltas or draws < 1:
        return None
    clusters: dict[int, list[float]] = {}
    for week, delta in deltas:
        x = _number(delta)
        if type(week) is not int or x is None:
            return None
        clusters.setdefault(week, []).append(x)
    keys = sorted(clusters)
    rng = Random(seed)
    samples: list[float] = []
    for _ in range(draws):
        chosen = [keys[rng.randrange(len(keys))] for __ in keys]
        vals = [v for k in chosen for v in clusters[k]]
        samples.append(sum(vals)/len(vals))
    samples.sort()
    return (samples[int(.025*(draws-1))], samples[int(.975*(draws-1))])


def _failure() -> dict[str, Any]:
    return {"status": "REPLAY_INPUT_INVALID", "can_execute": False,
            "probability_publishable": False, "model_promoted": False,
            "terminal_authority": TERMINAL_AUTHORITY, "certification_eligible": False}


def evaluate_chronological_replay(
    rows: Sequence[Mapping[str, Any]],
    incumbent: Mapping[str, Mapping[str, Any]],
    *,
    fit_end_season: int = 2024,
    calibration_season: int = 2025,
    holdout_season: int = 2026,
) -> dict[str, Any]:
    """Deterministic zero-execution fit / calibration / untouched chronological replay.

    Calibrator sees calibration season only. Holdout outcomes are only read for
    reporting, not for feature selection, parameter fitting or thresholds.
    """
    invalid = _failure()
    if (not isinstance(rows, (list, tuple)) or not isinstance(incumbent, Mapping)
            or not rows or type(fit_end_season) is not int
            or type(calibration_season) is not int or type(holdout_season) is not int
            or not (fit_end_season < calibration_season < holdout_season)):
        return invalid
    clean: list[dict[str, Any]] = []
    input_manifest: list[dict[str, Any]] = []
    seen: set[str] = set()
    names = None
    forbidden_exact = {"home_score", "away_score", "target_outcome", "game_outcome", "winner",
                       "final_margin", "final_score", "postgame_result"}
    for row in rows:
        if not isinstance(row, Mapping):
            return invalid
        ident = row.get("game_id")
        year, week = row.get("season"), row.get("week")
        order, vec = row.get("feature_order"), row.get("feature_vector")
        if (not isinstance(ident, str) or not ident.strip() or ident in seen
                or type(year) is not int or year > holdout_season or type(week) is not int or week < 1
                or row.get("target_outcome") not in ("HOME_WIN", "AWAY_WIN")
                or not isinstance(order, list) or not order or not all(isinstance(s, str) and s for s in order)
                or len(set(order)) != len(order)
                or any(str(name).lower() in forbidden_exact or str(name).lower().startswith(("postgame_", "final_score_")) for name in order)
                or not isinstance(vec, list) or len(vec) != len(order)
                or any(_number(v) is None for v in vec)
                or classify_feature_receipt(row) in ("FEATURE_CHRONOLOGY_INVALID", "SOURCE_MANIFEST_MISSING")
                or (names is not None and order != names)):
            return invalid
        names = order
        seen.add(ident)
        input_manifest.append({
            "game_id": ident, "season": year, "gameday": row.get("gameday"),
            "feature_cutoff_date": row.get("feature_cutoff_date"),
            "max_prior_gameday": row.get("max_prior_gameday"),
            "feature_vector": vec,
            "source_content_sha256s": row.get("source_content_sha256s"),
            "row_inputs_hash": row.get("row_inputs_hash"),
            "target_outcome": row.get("target_outcome"),
            "locked_at": row.get("locked_at"),
            "event_start_time_utc": row.get("event_start_time_utc"),
        })
        clean.append({"id":ident,"year":year,"week":week,"y":int(row["target_outcome"]=="HOME_WIN"),
                      "x":[float(v) for v in vec],"provenance":classify_feature_receipt(row),
                      "event_start_time_utc":row.get("event_start_time_utc")})
    fit = [r for r in clean if r["year"] <= fit_end_season]
    cal = [r for r in clean if r["year"] == calibration_season]
    hold = [r for r in clean if r["year"] == holdout_season]
    if not fit or not cal or not hold:
        return invalid
    d = len(names)
    try:
        mu = [sum(r["x"][j] for r in fit)/len(fit) for j in range(d)]
        sd = [sqrt(sum((r["x"][j]-mu[j])**2 for r in fit)/len(fit)) or 1.0 for j in range(d)]
        if not all(isfinite(x) for x in mu+sd):
            return invalid
        for r in clean:
            r["z"] = [max(-7.,min(7.,(r["x"][j]-mu[j])/sd[j])) for j in range(d)]
            if not all(isfinite(v) for v in r["z"]):
                return invalid
    except (ArithmeticError, ValueError):
        return invalid
    weights = [0.0]*(d+1)
    for step in range(1800):
        grad = [0.0]*(d+1)
        for r in fit:
            err = _sigmoid(weights[0]+sum(weights[j+1]*r["z"][j] for j in range(d)))-r["y"]
            grad[0]+=err
            for j in range(d):
                grad[j+1]+=err*r["z"][j]
        rate = .13/(1+step/300)
        for j in range(d+1):
            weights[j]-=rate*(grad[j]/len(fit)+(0.016*weights[j] if j else 0))
    def base(r):
        return _clip(_sigmoid(weights[0]+sum(weights[j+1]*r["z"][j] for j in range(d))))
    a,b=1.,0.
    for step in range(1200):
        ga,gb=0.,0.
        for r in cal:
            p=base(r); l=log(p/(1-p)); e=_sigmoid(a*l+b)-r["y"]
            ga+=e*l;gb+=e
        rate=.025/(1+step/400)
        a-=rate*(ga/len(cal)+.015*(a-1))
        b-=rate*(gb/len(cal)+.015*b)
    def pred(r):
        p=base(r)
        return _clip(_sigmoid(a*log(p/(1-p))+b))
    fit_eval=_score([(r["y"], pred(r)) for r in fit])
    cal_eval=_score([(r["y"], pred(r)) for r in cal])
    hold_eval=_score([(r["y"], pred(r)) for r in hold])
    paired_chal=[];paired_inc=[];deltas=[];excluded=0
    for r in hold:
        base_row=incumbent.get(r["id"])
        if base_row is None:
            continue
        if not isinstance(base_row, Mapping) or base_row.get("official_event_id") != r["id"]:
            excluded+=1;continue
        at,start=_utc(base_row.get("created_at")),_utc(base_row.get("event_start_time_utc"))
        p=_number(base_row.get("probability"))
        row_start=_utc(r.get("event_start_time_utc"))
        if (at is None or start is None or at >= start or p is None or not 0 <= p <= 1
                or (row_start is not None and start != row_start)):
            excluded+=1;continue
        pc=pred(r);p0=_clip(p)
        paired_chal.append((r["y"],pc));paired_inc.append((r["y"],p0))
        deltas.append((r["week"],(pc-r["y"])**2-(p0-r["y"])**2))
    manifest={"fit_end_season":fit_end_season,"calibration_season":calibration_season,
              "holdout_season":holdout_season,"feature_order":names,
              "fit_game_ids":sorted(r["id"] for r in fit),
              "calibration_game_ids":sorted(r["id"] for r in cal),
              "holdout_game_ids":sorted(r["id"] for r in hold),
              "fit_method":"fixed_l2_gradient_logit_1800_steps",
              "calibration_method":"2025_platt_1200_steps",
              "note":"Derived research hash, not independently authenticated pregame receipt"}
    manifest["row_source_digest"] = sha256(json.dumps(
        sorted(input_manifest, key=lambda r:r["game_id"]), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    source_hash=sha256(json.dumps(manifest,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return {
        "status":"RETROSPECTIVE_RESEARCH_ONLY","sample_sizes":{"fit":len(fit),"calibration":len(cal),"holdout":len(hold),"paired":len(paired_inc)},
        "holdout":{"challenger":hold_eval,"paired_challenger":_score(paired_chal),"paired_incumbent":_score(paired_inc),
                   "paired_brier_delta_ci95_by_week":paired_cluster_ci(deltas)},
        "calibration_period":{"challenger":cal_eval},"fit_period":{"challenger":fit_eval},
        "source_status":{"holdout_prospectively_locked_unverified":sum(r["provenance"]=="PRE_EVENT_LOCKED_UNAUTHENTICATED" for r in hold),
                         "holdout_retrospective":sum(r["provenance"]=="RETROSPECTIVE_PRIOR_ONLY" for r in hold),
                         "holdout_timestamp_unverified":sum(r["provenance"]=="PREGAME_TIMESTAMP_UNVERIFIED" for r in hold),
                         "excluded_nonpregame_incumbent":excluded,
                         "source_authentication":"NOT_INDEPENDENTLY_VERIFIED"},
        "source_manifest_sha256":source_hash,"model_artifact_checksum":sha256(json.dumps([weights,a,b],separators=(",",":")).encode()).hexdigest(),
        "calibrator_parameters":{"a":a,"b":b},"can_execute":False,"probability_publishable":False,
        "model_promoted":False,"terminal_authority":TERMINAL_AUTHORITY,"certification_eligible":False,
    }
