"""Audit frozen NCAAF/NHL ESPN event identities against gated fitted-model inputs.

This research-only script never scores, makes model features from scoreboard odds,
certifies artifacts, touches databases or places wagers.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
from pathlib import Path
import zipfile
from typing import Any

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SCHEMA = "WOW_FORWARD_MODEL_IDENTITY_FIXTURE_V1"
NCAAF_SCHEMA = "NCAAF_FEATURES_V1"
NHL_SCHEMA = "NHL_REGULAR_SEASON_FEATURES_V1"  # Historical candidate only.
REQUIRED_TEAM_STEMS = (
    "power_rating", "off_epa", "def_epa", "success_rate", "explosiveness",
    "qb_value", "qb_certainty", "ol_health", "def_front_health",
    "skill_availability", "rest_days", "tempo", "turnover_volatility",
    "special_teams_rating",
)
NCAAF_NUMERIC_RAW = tuple(f"{side}_{stem}" for stem in REQUIRED_TEAM_STEMS for side in ("home", "away")) + (
    "travel_distance_miles", "weather_wind_mph", "weather_precip_probability",
)
NHL_CANDIDATE_FEATURES = (
    "elo_delta", "win_pct_l10_delta", "goal_diff_l10_delta",
    "goals_for_l10_delta", "goals_against_l10_delta", "season_win_pct_delta",
    "rest_days_delta", "home_back_to_back", "away_back_to_back", "neutral_site",
)


class AuditFailure(ValueError):
    pass


def _aware(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AuditFailure("INVALID_TIME") from exc
    if dt.utcoffset() is None:
        raise AuditFailure("TIME_MUST_BE_AWARE")
    return dt.astimezone(timezone.utc)


def _unique_index(rows: list[dict], key: str, kind: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or not str(row.get(key) or "").strip():
            raise AuditFailure(f"{kind}_IDENTITY_MISSING")
        name = str(row[key])
        if name in out:
            raise AuditFailure(f"{kind}_IDENTITY_DUPLICATE")
        out[name] = row
    return out


def _read_json(z: zipfile.ZipFile, name: str) -> Any:
    try:
        return json.loads(z.read(name))
    except (KeyError, ValueError) as exc:
        raise AuditFailure("SOURCE_EVIDENCE_FILE_INVALID") from exc


def verify_original_evidence(evidence: Path, fixture: dict) -> dict:
    """Check off-host raw bytes against their own receipts AND frozen IDs."""
    checked = {}
    with zipfile.ZipFile(evidence) as z:
        for sport, data in fixture["sports"].items():
            name = sport.lower()
            date = data["target_date"]
            root = f"{name}/original/{name}_{date}_espn"
            try:
                raw = z.read(root + "_raw.json")
            except KeyError as exc:
                raise AuditFailure("SOURCE_RAW_BYTES_MISSING") from exc
            receipt = _read_json(z, root + "_capture_receipt.json")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != receipt.get("raw_sha256") or digest != data.get("raw_sha256"):
                raise AuditFailure("SOURCE_RAW_HASH_MISMATCH")
            if len(raw) != receipt.get("raw_bytes"):
                raise AuditFailure("SOURCE_RAW_LENGTH_MISMATCH")
            if (receipt.get("sport") != sport or receipt.get("target_date") != date or
                receipt.get("can_execute") is not False or
                receipt.get("probability_publishable") is not False or
                receipt.get("full_slate_completeness_attested") is not False):
                raise AuditFailure("SOURCE_RECEIPT_CONTRACT_INVALID")
            document = json.loads(raw)
            source_events = _unique_index(document.get("events") or [], "id", "RAW_EVENT")
            receipt_events = _unique_index(receipt.get("events_pregame_eligible") or [], "event_id", "RECEIPT_EVENT")
            fixture_events = _unique_index(data["events"], "espn_event_id", "FIXTURE_EVENT")
            if set(source_events) != set(receipt_events) or set(receipt_events) != set(fixture_events):
                raise AuditFailure("SOURCE_EVENT_SET_MISMATCH")
            capture_at = _aware(receipt.get("capture_host_clock_utc"))
            for event_id, candidate in source_events.items():
                frozen = fixture_events[event_id]
                received = receipt_events[event_id]
                starts = [_aware(candidate.get("date")), _aware(frozen["event_start_utc"]), _aware(received["event_start_utc"])]
                if len(set(starts)) != 1 or starts[0] <= capture_at:
                    raise AuditFailure("SOURCE_EVENT_TIME_MISMATCH")
                if ((candidate.get("status") or {}).get("type") or {}).get("state") != "pre":
                    raise AuditFailure("SOURCE_EVENT_NOT_PREGAME")
                if (starts[0] - capture_at).total_seconds() < 600:
                    raise AuditFailure("SOURCE_MIN_LEAD_MISSING")
                competitions = candidate.get("competitions")
                if not isinstance(competitions, list) or len(competitions) != 1:
                    raise AuditFailure("SOURCE_COMPETITION_INVALID")
                competitors = competitions[0].get("competitors")
                if not isinstance(competitors, list) or len(competitors) != 2:
                    raise AuditFailure("SOURCE_TEAMS_INVALID")
                teams = {}
                for c in competitors:
                    side = c.get("homeAway")
                    if side in teams or side not in {"home", "away"} or not isinstance(c.get("team"), dict):
                        raise AuditFailure("SOURCE_TEAMS_INVALID")
                    teams[side] = c["team"]
                for side in ("home", "away"):
                    orig = teams[side]
                    if (str(orig.get("id")) != frozen[f"{side}_espn_team_id"] or
                        str(orig.get("displayName")) != frozen[f"{side}_name"] or
                        str(orig.get("id")) != received[side]["team_id"]):
                        raise AuditFailure("SOURCE_TEAM_IDENTITY_MISMATCH")
                if teams["home"]["id"] == teams["away"]["id"]:
                    raise AuditFailure("SOURCE_TEAMS_INVALID")
            checked[sport] = {"source_sha256": digest, "events_reconciled": len(source_events), "result": "RAW_AND_IDENTITY_REPLAY_PASS"}
    return checked


def validate_ncaaf_snapshot(event: dict, snapshot: dict) -> str:
    if str(snapshot.get("official_event_id") or "") != event["espn_event_id"]:
        return "NCAAF_EVENT_IDENTITY_MISMATCH"
    if str(snapshot.get("feature_schema_version") or "") != NCAAF_SCHEMA:
        return "NCAAF_FEATURE_SCHEMA_MISMATCH"
    if (snapshot.get("home_team") != event["home_name"] or
        snapshot.get("away_team") != event["away_name"]):
        return "NCAAF_TEAM_NAME_IDENTITY_UNATTESTED"
    try:
        if _aware(snapshot.get("event_start_time")) != _aware(event["event_start_utc"]):
            return "NCAAF_EVENT_START_MISMATCH"
        if not _aware(snapshot.get("feature_as_of")) < _aware(event["event_start_utc"]):
            return "NCAAF_FEATURE_TIMESTAMP_NOT_PREGAME"
        if not _aware(snapshot.get("injury_evidence_timestamp")) < _aware(event["event_start_utc"]):
            return "NCAAF_INJURY_TIMESTAMP_NOT_PREGAME"
        if snapshot.get("market_timestamp") is not None and not _aware(snapshot["market_timestamp"]) < _aware(event["event_start_utc"]):
            return "NCAAF_MARKET_TIMESTAMP_NOT_PREGAME"
    except AuditFailure:
        return "NCAAF_SNAPSHOT_TIMESTAMP_INVALID"
    if snapshot.get("can_execute") is not False:
        return "NCAAF_SNAPSHOT_EXECUTION_POLICY_INVALID"
    for key in NCAAF_NUMERIC_RAW:
        value = snapshot.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            return "NCAAF_REQUIRED_RAW_FEATURE_INVALID"
    if snapshot.get("neutral_site") not in (True, False):
        return "NCAAF_NEUTRAL_SITE_INVALID"
    return "NCAAF_SNAPSHOT_STRUCTURE_READY_UNCERTIFIED"


def audit(fixture: dict, evidence_zip: Path, snapshots: list[dict] | None = None) -> dict:
    if (fixture.get("schema_version") != SCHEMA or fixture.get("can_execute") is not False or
        fixture.get("probability_publishable") is not False or fixture.get("full_slate_completeness_attested") is not False or
        set(fixture.get("sports") or {}) != {"NCAAF", "NHL"}):
        raise AuditFailure("FIXTURE_CONTRACT_INVALID")
    evidence = verify_original_evidence(evidence_zip, fixture)
    snapshots = snapshots or []
    snapshot_index = _unique_index(snapshots, "official_event_id", "SNAPSHOT")
    rows=[];counts={}
    for sport, data in fixture["sports"].items():
        for event in data["events"]:
            event_id = event["espn_event_id"]
            if sport == "NCAAF":
                snap = snapshot_index.get(event_id)
                status = "NCAAF_FEATURE_SNAPSHOT_NOT_FOUND" if snap is None else validate_ncaaf_snapshot(event, snap)
            else:
                status = "NHL_CANONICAL_FIRST_PARTY_IDENTITY_NOT_RECONCILED"
            counts[status] = counts.get(status, 0) + 1
            rows.append({"sport":sport,"espn_event_id":event_id,
                         "event_start_utc":event["event_start_utc"],
                         "home_espn_team_id":event["home_espn_team_id"],
                         "away_espn_team_id":event["away_espn_team_id"],
                         "model_feature_disposition":status,
                         "sporting_probability_available":False,
                         "can_execute":False})
    return {"audit_version":"WOW_FORWARD_MODEL_INPUT_IDENTITY_AUDIT_V1",
            "disposition":"SOURCE_VERIFIED_MODEL_INPUTS_NOT_CERTIFIED",
            "source_verification":evidence,"event_count":len(rows),
            "blocker_counts":counts,"rows":rows,
            "ncaaf_expected_feature_schema":NCAAF_SCHEMA,
            "nhl_candidate_feature_schema":NHL_SCHEMA,
            "nhl_candidate_only_feature_count":len(NHL_CANDIDATE_FEATURES),
            "full_slate_completeness_attested":False,
            "feature_consumption_verified":False,
            "probability_publishable":False,"can_execute":False}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,type=Path)
    parser.add_argument('--evidence-zip',required=True,type=Path)
    parser.add_argument('--ncaaf-snapshots',type=Path)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    try:
        fixture=json.loads(args.fixture.read_text())
        snapshots=json.loads(args.ncaaf_snapshots.read_text()) if args.ncaaf_snapshots else None
        result=audit(fixture,args.evidence_zip,snapshots)
        data=json.dumps(result,indent=2,sort_keys=True)+'\n'
        if args.out:
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(data)
        print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2,sort_keys=True))
        return 0
    except (AuditFailure,OSError,ValueError,KeyError,zipfile.BadZipFile) as exc:
        print(json.dumps({'status':'BLOCKED','reason_code':str(exc),'can_execute':False}))
        return 2

if __name__=='__main__':
    raise SystemExit(main())