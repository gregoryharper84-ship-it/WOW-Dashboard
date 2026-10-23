"""Fail-closed postgame grade of the immutable NCAAF Oct24 research shadow.

Not a WOW probability scorer. Numerical values in this file are historical
*research shadow* estimates, never official model probability or a wager.

Only the pinned pregame features/predictions can enter a grade. Outcomes come
from exact ESPN event-summary IDs and validated competitor IDs with RAW source
bytes retained as an audit artifact. Partial, changed or postponed games are
NOT treated as prediction failures or wins.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCHEMA = "WOW_NCAAF_RESULT_FORM_PROSPECTIVE_POSTMORTEM_V1"
EXPECTED_SHADOW_BLOB = "3816d37075faeeaf0b9f5ac91d77b731681975b5"
ORIGINAL_PREKICKOFF_SHA = "93e4ad38cd86c8b691e3bb536d46c63eaa3bb2db"
EXPECTED_INPUT_SHA256 = "83b97a86e770b79bb7d207bb8d57fdf157d8b3d35355fd6b99e52315f3e08b6f"
CANDIDATE_ID = "44d3dec0-e775-483f-b948-930214b69034"
ARTIFACT_SHA256 = "0de8ed68a96e48dcd60773b292c5a46401a090aa21dbdb32de0f5ef4f0f25c1a"
CFBD_ESPN_CAPTURE_RUN_ID = 39881490293
ESPN_ENDPOINT = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event="
CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
ALLOWED_FINAL_CODES = {"STATUS_FINAL", "STATUS_FINAL_OT"}


class GradingBlocked(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise GradingBlocked(code)


def _utc(raw: Any) -> datetime:
    try:
        date = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise GradingBlocked("NCAAF_POSTMORTEM_TIME_INVALID") from exc
    _require(date.tzinfo is not None, "NCAAF_POSTMORTEM_TIME_INVALID")
    return date.astimezone(timezone.utc)


def _git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\\0" + data).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def freeze_contract(shadow_bytes: bytes, feature_bytes: bytes) -> tuple[list[dict], dict, dict]:
    _require(_git_blob(shadow_bytes) == EXPECTED_SHADOW_BLOB,
             "NCAAF_POSTMORTEM_SHADOW_GIT_BLOB_CHANGED")
    _require(_sha(feature_bytes) == EXPECTED_INPUT_SHA256,
             "NCAAF_POSTMORTEM_PREGAME_FEATURE_HASH_CHANGED")
    try:
        shadow = json.loads(shadow_bytes)
        feature = json.loads(feature_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise GradingBlocked("NCAAF_POSTMORTEM_INPUT_JSON_INVALID") from exc
    _require(shadow.get("schema_version") == "WOW_NCAAF_RESULT_FORM_44_FORWARD_SHADOW_PREKICKOFF_V1"
             and shadow.get("status") == "RESEARCH_44_PREKICKOFF_FITTED_MODEL_SHADOW_FROZEN_UNCERTIFIED"
             and shadow.get("candidate_id") == CANDIDATE_ID
             and shadow.get("candidate_model_artifact_checksum") == ARTIFACT_SHA256
             and shadow.get("prospective_capture_github_run_id") == CFBD_ESPN_CAPTURE_RUN_ID
             and shadow.get("prospective_feature_file_sha256") == EXPECTED_INPUT_SHA256
             and shadow.get("calibration_certified") is False
             and shadow.get("calibrated_lower_bound") is None
             and shadow.get("governed_model_probability") is None
             and shadow.get("probability_publishable") is False
             and shadow.get("rank_eligible") is False
             and shadow.get("can_execute") is False,
             "NCAAF_POSTMORTEM_SHADOW_AUTHORITY_OR_IDENTITY_INVALID")
    _require(feature.get("sport") == "NCAAF" and
             feature.get("candidate_model_family") == "NCAAF_RESULT_FORM_LOGIT_V1" and
             feature.get("feature_schema_version") == "NCAAF_RESULT_FORM_PRIOR_V1" and
             feature.get("can_execute") is False and
             feature.get("probability_publishable") is False,
             "NCAAF_POSTMORTEM_FEATURE_CONTRACT_INVALID")
    se, fe = shadow.get("events"), feature.get("events")
    _require(isinstance(se, list) and isinstance(fe, list) and
             len(se) == len(fe) == 44 and shadow.get("cohort_event_count") == 44,
             "NCAAF_POSTMORTEM_44_COHORT_MISSING")
    by_id: dict[str, dict] = {}
    for f in fe:
        fid = str(f.get("official_event_id") or "")
        _require(fid.isdecimal() and fid not in by_id and
                 str(f.get("espn_event_id")) == fid and
                 str(f.get("espn_home_team_id", "")).isdecimal() and
                 str(f.get("espn_away_team_id", "")).isdecimal(),
                 "NCAAF_POSTMORTEM_FEATURE_EVENT_IDENTITY_INVALID")
        by_id[fid] = f
    captured = _utc(shadow.get("prospective_feature_capture_utc"))
    created = _utc(shadow.get("research_shadow_created_utc"))
    _require(captured <= created, "NCAAF_POSTMORTEM_CAPTURE_TIME_CONTRADICTION")
    seen = set()
    ordered = []
    for e in se:
        event_id = str(e.get("official_event_id") or "")
        f = by_id.get(event_id)
        _require(event_id not in seen and f is not None and
                 str(e.get("espn_event_id")) == event_id and
                 e.get("home_team") == f["home_team"] and
                 e.get("away_team") == f["away_team"] and
                 _utc(e.get("scheduled_start_utc")) == _utc(f["event_start_time"]),
                 "NCAAF_POSTMORTEM_SHADOW_EVENT_IDENTITY_CHANGED")
        seen.add(event_id)
        _require(created < _utc(e["scheduled_start_utc"]) and
                 captured < _utc(e["scheduled_start_utc"]),
                 "NCAAF_POSTMORTEM_PREDICTION_NOT_PREGAME")
        p = e.get("research_home_win_shadow_probability")
        _require(type(p) in (float, int) and math.isfinite(p) and 0 < p < 1 and
                 e.get("experiment_status") == "UNPUBLISHED_RESEARCH_FITTED_SHADOW_ONLY" and
                 e.get("calibrated_lower_bound") is None and
                 e.get("governed_model_probability") is None and
                 e.get("rank_eligible") is False and
                 e.get("probability_publishable") is False and
                 e.get("can_execute") is False,
                 "NCAAF_POSTMORTEM_SHADOW_SCORE_OR_AUTHORITY_CHANGED")
        ordered.append({"shadow": e, "feature": f})
    _require(seen == set(by_id), "NCAAF_POSTMORTEM_44_EVENT_SET_MISMATCH")
    return ordered, shadow, feature


def _score(value: Any) -> int:
    if isinstance(value, Mapping):
        value = value.get("displayValue", value.get("value"))
    _require(isinstance(value, (str, int)) and type(value) is not bool
             and re.fullmatch(r"[0-9]{1,3}", str(value)) is not None,
             "NCAAF_POSTMORTEM_FINAL_SCORE_INVALID")
    return int(value)


def inspect_espn(raw: bytes, *, event: Mapping[str, Any], now: datetime) -> dict:
    """Check exact ESPN event/side IDs; never accept a schedule as a final."""
    _require(now.tzinfo is not None, "NCAAF_POSTMORTEM_TIME_INVALID")
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise GradingBlocked("NCAAF_POSTMORTEM_SOURCE_JSON_INVALID") from exc
    header = doc.get("header") if isinstance(doc, dict) else None
    _require(isinstance(header, dict) and
             str(header.get("id", "")) == str(event["official_event_id"]),
             "NCAAF_POSTMORTEM_ESPN_EVENT_ID_MISMATCH")
    comps = header.get("competitions")
    _require(isinstance(comps, list) and len(comps) == 1 and
             isinstance(comps[0], dict), "NCAAF_POSTMORTEM_COMPETITION_AMBIGUOUS")
    comp = comps[0]
    sides = comp.get("competitors")
    _require(isinstance(sides, list) and len(sides) == 2 and
             all(isinstance(v, dict) for v in sides),
             "NCAAF_POSTMORTEM_COMPETITORS_INVALID")
    by_side = {v.get("homeAway"): v for v in sides}
    _require(set(by_side) == {"home", "away"},
             "NCAAF_POSTMORTEM_HOME_AWAY_INVALID")
    home, away = by_side["home"], by_side["away"]
    _require(str((home.get("team") or {}).get("id") or home.get("id") or "")
             == str(event["espn_home_team_id"]) and
             str((away.get("team") or {}).get("id") or away.get("id") or "")
             == str(event["espn_away_team_id"]),
             "NCAAF_POSTMORTEM_ESPN_TEAM_ID_MISMATCH")
    observed_start = comp.get("date") or header.get("date")
    _require(observed_start is not None, "NCAAF_POSTMORTEM_OBSERVED_START_MISSING")
    drift = abs((_utc(observed_start) - _utc(event["event_start_time"])).total_seconds())
    if drift > 3600:
        return {"grade_status": "RESCHEDULED_HOLD", "reason_code": "START_TIME_DRIFT_GT_60_MIN"}
    status = (comp.get("status") or {}).get("type") or {}
    if status.get("completed") is not True or status.get("name") not in ALLOWED_FINAL_CODES or status.get("state") != "post":
        return {"grade_status": "UNSETTLED_OR_POSTPONED", "reason_code": str(status.get("name") or "NOT_FINAL")}
    _require(now >= _utc(observed_start), "NCAAF_POSTMORTEM_SOURCE_FINAL_BEFORE_START")
    hs, ascore = _score(home.get("score")), _score(away.get("score"))
    _require(hs != ascore, "NCAAF_POSTMORTEM_TIE_NOT_VERIFIED")
    _require(type(home.get("winner")) is bool and
             type(away.get("winner")) is bool and
             home["winner"] is (hs > ascore) and
             away["winner"] is (ascore > hs),
             "NCAAF_POSTMORTEM_WINNER_SCORE_CONTRADICTION")
    return {
        "grade_status": "VERIFIED_FINAL",
        "home_points": hs, "away_points": ascore,
        "home_won": hs > ascore,
        "espn_status_name": status["name"],
        "observed_start_utc": _utc(observed_start).isoformat(),
    }


def _ece(records: list[dict]) -> float:
    n = len(records)
    if not n:
        return 0.0
    buckets: dict[int, list[dict]] = {}
    for item in records:
        idx = min(9, int(item["research_home_win_shadow_probability"] * 10))
        buckets.setdefault(idx, []).append(item)
    return sum(len(batch) / n * abs(
        sum(int(x["home_won"]) for x in batch) / len(batch)
        - sum(x["research_home_win_shadow_probability"] for x in batch) / len(batch)
    ) for batch in buckets.values())


def grade(ordered: list[dict], sources: Mapping[str, bytes], *, captured_at: datetime,
          github_sha: str, github_run_id: str) -> dict:
    _require(captured_at.tzinfo is not None and re.fullmatch("[0-9a-f]{40}", github_sha)
             and github_run_id.isdecimal(),
             "NCAAF_POSTMORTEM_CAPTURE_CONTEXT_INVALID")
    results, settled = [], []
    for pair in ordered:
        s, f = pair["shadow"], pair["feature"]
        event_id = str(s["official_event_id"])
        entry = {
            "official_event_id": event_id, "scheduled_start_utc": s["scheduled_start_utc"],
            "research_shadow_locked_to_original_commit": ORIGINAL_PREKICKOFF_SHA,
            "home_team": s["home_team"], "away_team": s["away_team"],
            "research_home_win_shadow_probability": s["research_home_win_shadow_probability"],
        }
        raw = sources.get(event_id)
        if raw is None:
            entry.update(grade_status="SOURCE_NOT_AVAILABLE", reason_code="NO_ESPN_PAYLOAD")
        else:
            entry["espn_raw_sha256"] = _sha(raw)
            try:
                evaluation = inspect_espn(raw, event=f, now=captured_at)
                entry.update(evaluation)
            except GradingBlocked as exc:
                entry.update(grade_status="SOURCE_REJECTED", reason_code=exc.code)
        if entry["grade_status"] == "VERIFIED_FINAL":
            entry["research_brier"] = (
                s["research_home_win_shadow_probability"] - int(entry["home_won"])
            ) ** 2
            p = s["research_home_win_shadow_probability"]
            entry["research_log_loss"] = (
                -math.log(p) if entry["home_won"] else -math.log1p(-p)
            )
            settled.append(entry)
        results.append(entry)
    brier = sum(x["research_brier"] for x in settled) / len(settled) if settled else None
    logloss = sum(x["research_log_loss"] for x in settled) / len(settled) if settled else None
    full = len(settled) == len(ordered)
    return {
        "schema_version": SCHEMA,
        "status": "RESEARCH_FORWARD_GRADED_ALL_44_UNCERTIFIED" if full else
                  "RESEARCH_FORWARD_GRADE_INCOMPLETE_UNCERTIFIED",
        "source": "ESPN_EVENT_SUMMARY_RAW_CAPTURE_AND_EXACT_TEAM_IDENTITY",
        "prediction_kind": "UNPUBLISHED_RESEARCH_SHADOW_NOT_GOVERNED_MODEL_PROBABILITY",
        "original_pregame_shadow_commit": ORIGINAL_PREKICKOFF_SHA,
        "original_shadow_git_blob_sha1": EXPECTED_SHADOW_BLOB,
        "prospective_feature_sha256": EXPECTED_INPUT_SHA256,
        "candidate_id": CANDIDATE_ID,
        "candidate_model_artifact_checksum": ARTIFACT_SHA256,
        "github_postgame_run_sha": github_sha,
        "github_postgame_run_id": int(github_run_id),
        "collected_at_utc": captured_at.astimezone(timezone.utc).isoformat(),
        "cohort_count": len(ordered),
        "verified_final_count": len(settled),
        "pending_or_held_count": len(ordered) - len(settled),
        "research_metrics": {
            "brier": brier, "log_loss": logloss,
            "ece_10_bins": _ece(settled) if settled else None,
            "denominator": len(settled),
            "complete_cohort": full,
        },
        "events": results,
        "historical_source_attestation": "HOLD",
        "calibration_certified": False, "calibrated_lower_bound": None,
        "model_probability": None, "rank_eligible": False,
        "probability_publishable": False, "can_execute": False,
    }


def collect(ordered: list[dict], *, output_dir: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, bytes] = {}
    failures: dict[str, str] = {}
    for pair in ordered:
        event_id = str(pair["shadow"]["official_event_id"])
        _require(re.fullmatch(r"[0-9]{5,12}", event_id) is not None,
                 "NCAAF_POSTMORTEM_FETCH_EVENT_ID_INVALID")
        url = ESPN_ENDPOINT + event_id
        try:
            req = Request(url, headers={"Accept": "application/json",
                                        "User-Agent": "WOW-ReadOnlyResearchCapture/1.0"})
            with urlopen(req, timeout=18) as resp:
                _require(resp.status == 200,
                         "NCAAF_POSTMORTEM_ESPN_HTTP_NOT_200")
                raw = resp.read(3_000_001)
                _require(len(raw) <= 3_000_000,
                         "NCAAF_POSTMORTEM_ESPN_BODY_LIMIT_EXCEEDED")
                data[event_id] = raw
                (output_dir / f"{event_id}.json").write_bytes(raw)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            failures[event_id] = type(exc).__name__
        time.sleep(0.08)
    return data, failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow", required=True, type=Path)
    ap.add_argument("--features", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--source-dir", type=Path)
    ap.add_argument("--fetch-live", action="store_true")
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    _require(args.source_dir is not None or args.fetch_live,
             "NCAAF_POSTMORTEM_SOURCE_REQUIRED")
    ordered, _, _ = freeze_contract(args.shadow.read_bytes(), args.features.read_bytes())
    now = datetime.now(timezone.utc)
    _require(now >= datetime(2026, 10, 25, 12, tzinfo=timezone.utc),
             "NCAAF_POSTMORTEM_FETCH_BEFORE_SETTLEMENT_WINDOW")
    _require(now < datetime(2026, 11, 1, tzinfo=timezone.utc),
             "NCAAF_POSTMORTEM_OUTSIDE_2026_WINDOW")
    _require(args.out != args.shadow and args.out != args.features,
             "NCAAF_POSTMORTEM_INPUT_OVERWRITE_FORBIDDEN")
    failures: dict[str, str] = {}
    if args.fetch_live:
        raw, failures = collect(ordered, output_dir=args.out.parent / "raw-espn")
    else:
        raw = {p.stem: p.read_bytes() for p in args.source_dir.glob("*.json")}
    report = grade(ordered, raw, captured_at=now,
                   github_sha=args.head_sha, github_run_id=args.run_id)
    report["fetch_errors"] = failures
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": report["status"], "cohort_count": report["cohort_count"],
        "verified_final_count": report["verified_final_count"],
        "pending_or_held_count": report["pending_or_held_count"],
        "brier": report["research_metrics"]["brier"],
        "calibration_certified": False,
        "probability_publishable": False, "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
