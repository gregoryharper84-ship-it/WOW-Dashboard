"""One-shot ESPN NCAAF/NHL raw-source capture (research only, no probabilities)."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
from urllib import error, request

HOST = "site.api.espn.com"
SPORT_CONFIG = {
    "NCAAF": {
        "path": "/apis/site/v2/sports/football/college-football/scoreboard",
        "query_suffix": "&groups=80&limit=500",
        "scope": "ESPN_COLLEGE_FOOTBALL_GROUP_80_LIMIT_500_NOT_COMPLETENESS_CERTIFIED",
    },
    "NHL": {
        "path": "/apis/site/v2/sports/hockey/nhl/scoreboard",
        "query_suffix": "",
        "scope": "ESPN_NHL_DATE_SCOREBOARD_NOT_COMPLETENESS_CERTIFIED",
    },
}
MAX_SOURCE_BYTES = 8_000_000
MAX_EVENT_ROWS = 500
MAX_CLOCK_SKEW_SECONDS = 600
MIN_LEAD_SECONDS = 600
SCHEMA = "WOW_NCAAF_NHL_ESPN_ONE_SHOT_RAW_CAPTURE_V1"
CAN_EXECUTE = False


class CaptureFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RefuseRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CaptureFailure("SOURCE_REDIRECT_REFUSED")


def validated_sport(sport: str) -> str:
    if sport not in SPORT_CONFIG:
        raise CaptureFailure("BAD_SPORT")
    return sport


def validated_date(value: str) -> str:
    try:
        if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError("noncanonical date")
        return value
    except (ValueError, TypeError) as exc:
        raise CaptureFailure("BAD_TARGET_DATE") from exc


def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CaptureFailure("SOURCE_EVENT_TIME_MALFORMED") from exc


def source_url(sport: str, target_date: str) -> str:
    config = SPORT_CONFIG[validated_sport(sport)]
    d = validated_date(target_date).replace("-", "")
    return f"https://{HOST}{config['path']}?dates={d}{config['query_suffix']}"


def fetch_source(sport: str, target_date: str, *, now: datetime, opener=None) -> tuple[bytes, dict]:
    if now.tzinfo is None:
        raise CaptureFailure("CAPTURE_CLOCK_NAIVE")
    now = now.astimezone(timezone.utc)
    url = source_url(sport, target_date)
    opener = opener or request.build_opener(RefuseRedirect())
    req = request.Request(url, headers={"Accept": "application/json", "User-Agent": "WOW-forward-research-capture/1.0"})
    try:
        with opener.open(req, timeout=15) as res:
            if res.geturl() != url:
                raise CaptureFailure("SOURCE_REDIRECT_REFUSED")
            if getattr(res, "status", res.getcode()) != 200:
                raise CaptureFailure("SOURCE_HTTP_UNEXPECTED_STATUS")
            ctype = res.headers.get("Content-Type", "")
            if "application/json" not in ctype.lower():
                raise CaptureFailure("SOURCE_CONTENT_TYPE_INVALID")
            date_header = res.headers.get("Date")
            if not date_header:
                raise CaptureFailure("SOURCE_HTTP_DATE_MISSING")
            try:
                source_dt = parsedate_to_datetime(date_header)
                if source_dt.tzinfo is None:
                    raise ValueError("UTC offset required")
                source_dt = source_dt.astimezone(timezone.utc)
            except (TypeError, ValueError, IndexError, OverflowError) as exc:
                raise CaptureFailure("SOURCE_HTTP_DATE_INVALID") from exc
            if abs((now - source_dt).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
                raise CaptureFailure("SOURCE_HTTP_DATE_STALE_OR_SKEWED")
            raw = res.read(MAX_SOURCE_BYTES + 1)
            if not raw:
                raise CaptureFailure("SOURCE_BODY_EMPTY")
            if len(raw) > MAX_SOURCE_BYTES:
                raise CaptureFailure("SOURCE_BODY_TOO_LARGE")
            return raw, {
                "source_url": url,
                "source_http_date": source_dt.isoformat(),
                "capture_host_clock_utc": now.isoformat(),
                "source_content_type": ctype,
                "raw_bytes": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
            }
    except CaptureFailure:
        raise
    except error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise CaptureFailure("SOURCE_REDIRECT_REFUSED") from exc
        raise CaptureFailure("SOURCE_HTTP_ERROR") from exc
    except (OSError, error.URLError) as exc:
        raise CaptureFailure("SOURCE_FETCH_FAILED") from exc


def _competitor_identity(event: dict) -> dict:
    try:
        competition = event["competitions"][0]
        competitors = competition["competitors"]
        if not isinstance(competitors, list) or len(competitors) != 2:
            raise ValueError("two competitors required")
        result = {}
        for item in competitors:
            home_away = item["homeAway"]
            team = item["team"]
            team_id = str(team["id"]).strip()
            if home_away not in {"home", "away"} or not team_id or home_away in result:
                raise ValueError("bad team identity")
            result[home_away] = {
                "team_id": team_id,
                "name": str(team.get("displayName", "")),
            }
        if set(result) != {"home", "away"} or result["home"]["team_id"] == result["away"]["team_id"]:
            raise ValueError("missing or duplicate identities")
        return {"home": result["home"], "away": result["away"]}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise CaptureFailure("SOURCE_TEAM_IDENTITY_MALFORMED") from exc


def classify_events(sport: str, raw: bytes, *, now: datetime, min_lead: int = MIN_LEAD_SECONDS) -> dict:
    validated_sport(sport)
    if min_lead < MIN_LEAD_SECONDS:
        raise CaptureFailure("BAD_MIN_LEAD")
    if now.tzinfo is None:
        raise CaptureFailure("CAPTURE_CLOCK_NAIVE")
    now = now.astimezone(timezone.utc)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CaptureFailure("SOURCE_JSON_INVALID") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise CaptureFailure("SOURCE_EVENTS_MALFORMED")
    events = payload["events"]
    if len(events) > MAX_EVENT_ROWS:
        raise CaptureFailure("SOURCE_EVENT_ROWS_EXCEED_CAP")
    eligible, skipped, seen = [], [], set()
    for event in events:
        if not isinstance(event, dict) or not str(event.get("id", "")).strip():
            raise CaptureFailure("SOURCE_EVENT_IDENTITY_MALFORMED")
        event_id = str(event["id"]).strip()
        if event_id in seen:
            raise CaptureFailure("SOURCE_EVENT_IDENTITY_DUPLICATE")
        seen.add(event_id)
        start = parse_utc(event.get("date"))
        status = event.get("status")
        state = (status.get("type") or {}).get("state") if isinstance(status, dict) else None
        if not isinstance(state, str) or state.lower() not in {"pre", "in", "post"}:
            raise CaptureFailure("SOURCE_EVENT_STATUS_MALFORMED")
        identities = _competitor_identity(event)
        lead_seconds = int((start - now).total_seconds())
        summary = {"event_id": event_id, "event_start_utc": start.isoformat(),
                   "state": state.lower(), "lead_seconds": lead_seconds, **identities}
        if state.lower() == "pre" and lead_seconds >= min_lead:
            eligible.append(summary)
        else:
            skipped.append({**summary, "reason": "EVENT_NOT_FUTURE_PREGAME" if state.lower() != "pre" else "MIN_LEAD_NOT_MET"})
    return {"events_total": len(events), "events_pregame_eligible": eligible, "events_skipped": skipped}


def _paths(sport: str, target_date: str, outdir: Path) -> tuple[Path, Path]:
    base = f"{validated_sport(sport).lower()}_{validated_date(target_date)}_espn"
    return outdir / f"{base}_raw.json", outdir / f"{base}_capture_receipt.json"


def capture(sport: str, target_date: str, outdir: Path, *, now: datetime | None = None, opener=None) -> dict:
    now = now or datetime.now(timezone.utc)
    raw, meta = fetch_source(sport, target_date, now=now, opener=opener)
    events = classify_events(sport, raw, now=now)
    manifest = {
        "schema_version": SCHEMA,
        "sport": sport,
        "target_date": target_date,
        **meta,
        **events,
        "data_provenance": "ESPN_PUBLIC_SCOREBOARD_HTTP",
        "capture_scope": SPORT_CONFIG[validated_sport(sport)]["scope"],
        "full_slate_completeness_attested": False,
        "independent_timestamp_attestation": "NOT_ESTABLISHED",
        "sport_feature_compatibility": "UNVERIFIED_NO_FEATURES_COMPUTED",
        "probability_publishable": False,
        "can_execute": False,
        "disposition": "RAW_SOURCE_CAPTURE_ONLY",
    }
    outdir = Path(outdir)
    raw_path, receipt_path = _paths(sport, target_date, outdir)
    try:
        outdir.mkdir(parents=True, exist_ok=False)  # atomic refusal of existing evidence directory
    except FileExistsError as exc:
        raise CaptureFailure("CAPTURE_OUTPUT_ALREADY_EXISTS") from exc
    try:
        with raw_path.open("xb") as stream:
            os.chmod(raw_path, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with receipt_path.open("xb") as stream:
            os.chmod(receipt_path, 0o600)
            stream.write((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise CaptureFailure("CAPTURE_FILE_WRITE_FAILED") from exc
    return manifest


def verify(sport: str, target_date: str, outdir: Path) -> dict:
    raw_path, receipt_path = _paths(sport, target_date, Path(outdir))
    try:
        raw = raw_path.read_bytes()
        manifest = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CaptureFailure("CAPTURE_RECEIPT_UNREADABLE") from exc
    if not isinstance(manifest, dict):
        raise CaptureFailure("CAPTURE_RECEIPT_MALFORMED")
    if (manifest.get("schema_version") != SCHEMA or manifest.get("sport") != sport or
        manifest.get("target_date") != target_date or manifest.get("source_url") != source_url(sport, target_date) or
        manifest.get("capture_scope") != SPORT_CONFIG[sport]["scope"] or
        manifest.get("can_execute") is not False or manifest.get("probability_publishable") is not False or
        manifest.get("full_slate_completeness_attested") is not False):
        raise CaptureFailure("CAPTURE_RECEIPT_MALFORMED")
    if hashlib.sha256(raw).hexdigest() != manifest.get("raw_sha256") or len(raw) != manifest.get("raw_bytes"):
        raise CaptureFailure("CAPTURE_RAW_HASH_MISMATCH")
    events = classify_events(sport, raw, now=parse_utc(manifest.get("capture_host_clock_utc")))
    if any(manifest.get(k) != value for k, value in events.items()):
        raise CaptureFailure("CAPTURE_RECEIPT_EVENT_MISMATCH")
    return {"status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
            "sport": sport, "raw_sha256": manifest["raw_sha256"], "events_total": events["events_total"],
            "events_pregame_eligible_count": len(events["events_pregame_eligible"]),
            "can_execute": False, "probability_publishable": False,
            "independent_timestamp_attestation": "NOT_ESTABLISHED",
            "sport_feature_compatibility": "UNVERIFIED"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("capture", "verify"):
        p = commands.add_parser(command)
        p.add_argument("--sport", choices=sorted(SPORT_CONFIG), required=True)
        p.add_argument("--date", required=True)
        p.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = capture(args.sport, args.date, args.outdir) if args.command == "capture" else verify(args.sport, args.date, args.outdir)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except CaptureFailure as exc:
        print(json.dumps({"status": "BLOCKED", "reason_code": exc.code, "can_execute": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())