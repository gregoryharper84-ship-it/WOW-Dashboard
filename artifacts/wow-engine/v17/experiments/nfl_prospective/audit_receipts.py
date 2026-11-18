"""Independent, read-only verification of exported NFL prospective shadow rows.

This module never downloads data, writes SQL, fetches credentials, promotes a
model, or certifies sports probability. Independent-source inputs must be
acquired and authenticated externally; supplied digest strings are *not* proof
that a provider actually published their asserted bytes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from math import isclose, isfinite, log
from pathlib import Path
import json
import re
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
SCIENTIFICALLY_CERTIFIED = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
SHA_RE = re.compile(r"[a-fA-F0-9]{64}\Z")
IDENTITY_RE = re.compile(r"20\d{2}_(?:\d{1,2})_[A-Z]{2,4}_[A-Z]{2,4}\Z")


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt.astimezone(timezone.utc) if dt.utcoffset() is not None else None


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA_RE.fullmatch(value))


def _prob(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    p = float(value)
    return p if isfinite(p) and 0 <= p <= 1 else None


def _int_points(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _paired_incumbent(row: Mapping[str, Any], kick: datetime) -> tuple[bool, float | None]:
    meta = row.get("research_metadata") or {}
    inc = meta.get("incumbent") if isinstance(meta, Mapping) else None
    if not isinstance(inc, Mapping):
        return False, None
    p = _prob(inc.get("calibrated_home_probability"))
    ts = _utc(inc.get("created_at"))
    start = _utc(inc.get("event_start_time_utc"))
    frozen_at = _utc(row.get("prediction_created_at"))
    if (p is None or ts is None or start != kick or ts >= kick
            or frozen_at is None or ts > frozen_at
            or not isinstance(inc.get("event_prediction_id"), str)
            or not inc["event_prediction_id"].strip()
            or not isinstance(inc.get("model_version"), str)
            or not inc["model_version"].strip()):
        return False, None
    # Do not rebrand an uncertified champion or substitute market odds.
    return True, p


def audit_forecast(row: Mapping[str, Any]) -> dict[str, Any]:
    """Check exported evidence shape/chronology, not underlying data-source bytes."""
    reasons: list[str] = []
    kickoff = _utc(row.get("event_start_time_utc"))
    created = _utc(row.get("created_at"))
    predicted = _utc(row.get("prediction_created_at"))
    event = row.get("official_event_id")
    if not isinstance(event, str) or not IDENTITY_RE.fullmatch(event):
        reasons.append("EVENT_IDENTITY_INVALID")
    if (not isinstance(row.get("challenger_id"), str) or not row["challenger_id"]
            or row.get("model_family") != row.get("challenger_id")):
        reasons.append("CHALLENGER_MODEL_IDENTITY_INVALID")
    if (row.get("provider_event_id") != event
            or not row.get("home_team") or not row.get("away_team")
            or row.get("home_team") == row.get("away_team")
            or row.get("selected_participant") not in {row.get("home_team"), row.get("away_team")}):
        reasons.append("FIXTURE_SIDE_MISMATCH")
    if kickoff is None or created is None or predicted is None or not (created < kickoff and predicted < kickoff):
        reasons.append("SERVER_PREGAME_CHRONOLOGY_UNPROVEN")
    for f in ("can_execute", "probability_publishable", "promotion_authorized"):
        if row.get(f) is not False:
            reasons.append(f.upper() + "_NOT_FALSE")
    if row.get("terminal_authority") != TERMINAL_AUTHORITY:
        reasons.append("TERMINAL_AUTHORITY_INVALID")
    p = _prob(row.get("calibrated_probability"))
    lbs = [_prob(row.get(x)) for x in ("local_wilson_lower", "bootstrap_q10_lower", "composite_lower_bound")]
    if p is None or any(v is None or v > p for v in lbs):
        reasons.append("PROBABILITY_OR_LOWER_BOUND_INVALID")
    elif lbs[2] > min(lbs[:2]) + 1e-10:
        reasons.append("COMPOSITE_LOWER_BOUND_INVALID")
    if row.get("training_cutoff") != "2024-12-31" or row.get("calibration_season") != 2025 or row.get("calibration_method") != "PLATT_TIME_SPLIT_V1":
        reasons.append("MODEL_FIT_CHRONOLOGY_INVALID")
    if row.get("lifecycle_state") not in ("FORWARD_SHADOW", "GRADED"):
        reasons.append("LIFECYCLE_INVALID")

    metadata = row.get("research_metadata")
    if not isinstance(metadata, Mapping):
        reasons.append("RESEARCH_METADATA_MISSING")
        metadata = {}
    fetched = _utc(metadata.get("source_snapshot_fetched_at"))
    if (created is None or fetched is None or fetched > created
            or (created - fetched) > timedelta(hours=96)):
        reasons.append("CANONICAL_SOURCE_FRESHNESS_UNPROVEN")
    if not _sha(metadata.get("source_snapshot_sha256")) or not isinstance(metadata.get("source_snapshot_id"), str) or not metadata["source_snapshot_id"]:
        reasons.append("SCHEDULE_DIGEST_OR_SNAPSHOT_MISSING")
    if not _sha(row.get("source_feature_hash")) or row.get("source_feature_hash") != metadata.get("feature_row_inputs_hash"):
        reasons.append("FEATURE_INPUT_HASH_INVALID")
    if not _sha(metadata.get("model_artifact_sha256")):
        reasons.append("MODEL_ARTIFACT_DIGEST_MISSING")
    fs = metadata.get("feature_source_sha256s")
    if not isinstance(fs, list) or not fs or any(not _sha(x) for x in fs):
        reasons.append("FEATURE_SOURCE_MANIFEST_INVALID")
    home_p = _prob(metadata.get("calibrated_home_probability"))
    if (home_p is None or p is None or not row.get("home_team")
            or not isclose(p, home_p if row.get("selected_participant") == row.get("home_team") else 1-home_p, abs_tol=1e-8, rel_tol=0)):
        reasons.append("HOME_PROBABILITY_INCONSISTENT")
    paired, _ = _paired_incumbent(row, kickoff) if kickoff else (False, None)
    return {
        "status": "PREGAME_RECEIPT_INVALID" if reasons else "PREGAME_RECEIPT_VALID_UNCERTIFIED",
        "ok": not reasons,
        "reasons": reasons,
        "paired_incumbent_eligible": paired,
        "can_execute": False,
        "scientifically_certified": False,
        "raw_source_bytes_independently_authenticated": False,
    }


def audit_grade(row: Mapping[str, Any], source_final: Mapping[str, Any] | None) -> dict[str, Any]:
    """Recompute outcome metrics from two externally SUPPLIED source summaries.

    The caller must independently obtain and authenticate provider raw bytes.
    The code checks consistency of supplied hashes but does not fetch sources.
    """
    forecast = audit_forecast(row)
    base = dict(ok=False, can_execute=False, scientifically_certified=False,
                independently_league_certified=False, paired_incumbent_eligible=False,
                paired_brier_delta=None, paired_log_loss_delta=None)
    if not forecast["ok"]:
        return {**base, "status": "PREGAME_RECEIPT_INVALID", "reasons": forecast["reasons"]}
    if row.get("lifecycle_state") != "GRADED":
        return {**base, "status": "AWAITING_PERSISTED_GRADE"}
    if not isinstance(source_final, Mapping):
        return {**base, "status": "INDEPENDENT_FINAL_EVIDENCE_MISSING"}
    n = source_final.get("nflverse")
    e = source_final.get("espn")
    kickoff = _utc(row.get("event_start_time_utc"))
    graded_at = _utc(row.get("graded_at"))
    manifest = row.get("grade_source_manifest")
    if not isinstance(n, Mapping) or not isinstance(e, Mapping) or not isinstance(manifest, Mapping):
        return {**base, "status": "INVALID_PROVIDER_OR_GRADE_MANIFEST"}
    if (graded_at is None or graded_at <= kickoff
            or _utc(n.get("fetched_at")) is None or _utc(n.get("fetched_at")) <= kickoff
            or _utc(e.get("fetched_at")) is None or _utc(e.get("fetched_at")) <= kickoff
            or n.get("completed") is not True or e.get("completed") is not True):
        return {**base, "status": "FINAL_CHRONOLOGY_NOT_VERIFIED"}
    if (n.get("game_id") != row.get("official_event_id")
            or not isinstance(e.get("event_id"), str) or not e["event_id"]
            or _utc(n.get("event_start_time_utc")) != kickoff
            or _utc(e.get("event_start_time_utc")) != kickoff):
        return {**base, "status": "PROVIDER_EVENT_IDENTITY_MISMATCH"}
    if any(s.get(side) != row.get(side) for s in (n, e) for side in ("home_team", "away_team")):
        return {**base, "status": "PROVIDER_TEAM_IDENTITY_MISMATCH"}
    pts = [( _int_points(s.get("home_score")), _int_points(s.get("away_score"))) for s in (n,e)]
    if any(None in item for item in pts) or pts[0] != pts[1] or pts[0][0] == pts[0][1]:
        return {**base, "status": "PROVIDER_SCORE_CONFLICT_OR_TIE"}
    sources = manifest.get("sources")
    if (not isinstance(sources, list) or len(sources) != 2
            or any(not isinstance(z, str) for z in sources)):
        return {**base, "status": "GRADE_SOURCE_MANIFEST_MISMATCH"}
    if (not _sha(n.get("raw_sha256")) or not _sha(e.get("raw_sha256"))
            or manifest.get("nflverse_content_sha256") != n.get("raw_sha256")
            or manifest.get("espn_raw_sha256") != e.get("raw_sha256")
            or manifest.get("espn_event_id") != e.get("event_id")
            or set(sources) != {"NFLVERSE", "ESPN"}
            or manifest.get("status") != "TWO_PROVIDER_RECONCILED_NOT_LEAGUE_CERTIFIED"):
        return {**base, "status": "GRADE_SOURCE_MANIFEST_MISMATCH"}
    home, away = pts[0]
    if manifest.get("home_score") != home or manifest.get("away_score") != away:
        return {**base, "status": "GRADE_FINAL_SCORE_MISMATCH"}
    y = int((home > away and row.get("selected_participant") == row.get("home_team"))
            or (away > home and row.get("selected_participant") == row.get("away_team")))
    p = _prob(row.get("calibrated_probability"))
    # Stored predictions never use market quotes, and score arithmetic is independent.
    cp = min(1 - 1e-6, max(1e-6, p))
    brier = (p-y)**2
    loss = -(y*log(cp) + (1-y)*log(1-cp))
    stored = (row.get("brier"), row.get("log_loss"))
    if (row.get("outcome") != y or row.get("hit") is not bool(y)
            or any(isinstance(z, bool) or not isinstance(z,(int,float)) or not isfinite(z) for z in stored)
            or not isclose(stored[0],brier,abs_tol=1e-8,rel_tol=0)
            or not isclose(stored[1],loss,abs_tol=1e-8,rel_tol=0)):
        return {**base, "status": "PERSISTED_GRADE_MATH_MISMATCH"}
    paired, ip = _paired_incumbent(row, kickoff)
    result = {**base, "ok": True, "status": "GRADE_MATH_AND_SUPPLIED_SOURCES_MATCH_RESEARCH_ONLY",
              "paired_incumbent_eligible": paired, "brier": brier, "log_loss": loss,
              "home_win": int(home > away), "verified_home_score": home, "verified_away_score": away}
    if paired:
        hwin = int(home > away)
        hp = _prob(row["research_metadata"].get("calibrated_home_probability"))
        c_brier = (hp-hwin)**2
        ip_clip = min(1-1e-6,max(1e-6,ip))
        hp_clip = min(1-1e-6,max(1e-6,hp))
        c_loss = -(hwin*log(hp_clip)+(1-hwin)*log(1-hp_clip))
        i_loss = -(hwin*log(ip_clip)+(1-hwin)*log(1-ip_clip))
        result["paired_brier_delta"] = c_brier - (ip-hwin)**2
        result["paired_log_loss_delta"] = c_loss - i_loss
    return result


def audit_cohort(rows: Sequence[Mapping[str, Any]], source_finals: Mapping[str, Any], *, minimum_forward: int = 100) -> dict[str,Any]:
    """Research cohort diagnostics; no scientific decision or model promotion."""
    base = dict(can_execute=False, scientifically_certified=False, model_promoted=False,
                total_rows=len(rows), verified_grades=0, paired_count=0,
                mean_paired_brier_delta=None, mean_paired_log_loss_delta=None,
                minimum_forward_required=minimum_forward,
                forward_sample_minimum_met=False)
    if any(not isinstance(r, Mapping) for r in rows):
        return {**base,"status":"INVALID_COHORT_ROW"}
    ids = [(r.get("challenger_id"),r.get("official_event_id")) for r in rows]
    if any(not isinstance(a, str) or not isinstance(b, str) for a,b in ids):
        return {**base,"status":"INVALID_COHORT_EVENT_IDENTITY"}
    if len(set(ids)) != len(ids):
        return {**base,"status":"DUPLICATE_EVENT_OR_VERSION"}
    if len({r.get("challenger_id") for r in rows}) > 1 or len({r.get("model_family") for r in rows})>1:
        return {**base,"status":"MIXED_CHALLENGER_VERSION"}
    hashes = {r.get("research_metadata",{}).get("model_artifact_sha256")
              for r in rows if isinstance(r.get("research_metadata"),Mapping)}
    if len(hashes)>1:
        return {**base,"status":"MIXED_MODEL_ARTIFACT_VERSION"}
    if not isinstance(minimum_forward,int) or minimum_forward < 100:
        return {**base, "status":"MINIMUM_FORWARD_POLICY_INVALID"}
    audited = [audit_grade(r,source_finals.get(r.get("official_event_id"))) for r in rows]
    verified = [a for a in audited if a["ok"]]
    paired = [a for a in verified if a["paired_incumbent_eligible"]]
    status_counts: dict[str,int] = {}
    for rec in audited:
        st=rec["status"]
        status_counts[st]=status_counts.get(st,0)+1
    n=len(paired)
    return {**base, "status":"PROSPECTIVE_EVIDENCE_REQUIRES_SCIENTIFIC_REVIEW" if len(verified)>=minimum_forward and n>=minimum_forward else "PROSPECTIVE_SAMPLE_INSUFFICIENT",
            "verified_grades":len(verified), "paired_count":n,
            "grade_status_counts":status_counts,
            "forward_sample_minimum_met":len(verified)>=minimum_forward and n>=minimum_forward,
            "mean_paired_brier_delta":sum(a["paired_brier_delta"] for a in paired)/n if n else None,
            "mean_paired_log_loss_delta":sum(a["paired_log_loss_delta"] for a in paired)/n if n else None,
            "raw_provider_bytes_independently_authenticated":False}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rows",type=Path,required=True,help="JSON array of exported immutable rows")
    p.add_argument("--finals",type=Path,required=True,help="JSON object keyed by official event ID of independently gathered source summaries")
    p.add_argument("--out",type=Path)
    args=p.parse_args()
    rows=json.loads(args.rows.read_text(encoding="utf-8"))
    finals=json.loads(args.finals.read_text(encoding="utf-8"))
    if not isinstance(rows,list) or not isinstance(finals,dict):
        raise SystemExit("AUDIT_INPUT_TYPES_INVALID")
    result=audit_cohort(rows,finals)
    text=json.dumps(result,sort_keys=True,allow_nan=False)
    print(text)
    if args.out:
        args.out.write_text(text+"\n",encoding="utf-8")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
