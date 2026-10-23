"""One-shot raw ESPN NBA evidence capture. Research only; no scoring or wagers."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib import error, request

SOURCE_HOST = "site.api.espn.com"
SOURCE_PATH = "/apis/site/v2/sports/basketball/nba/scoreboard"
MAX_SOURCE_BYTES = 3_000_000
MIN_LEAD_SECONDS = 600
MAX_CLOCK_SKEW_SECONDS = 600
MANIFEST_SCHEMA = "WOW_NBA_ESPN_ONE_SHOT_RAW_CAPTURE_V1"
CAN_EXECUTE = False

class CaptureFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

class RefuseRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CaptureFailure("SOURCE_REDIRECT_REFUSED")

def utc_date(value: str) -> str:
    try:
        if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
            raise ValueError("not canonical ISO date")
        return value
    except ValueError as exc:
        raise CaptureFailure("BAD_TARGET_DATE") from exc

def utc_datetime(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError) as exc:
        raise CaptureFailure("SOURCE_EVENT_TIME_MALFORMED") from exc

def source_url(target_date: str) -> str:
    d = utc_date(target_date)
    return f"https://{SOURCE_HOST}{SOURCE_PATH}?dates={d.replace('-', '')}"

def fetch_source(target_date: str, *, now: datetime | None = None, opener=None) -> tuple[bytes, dict]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise CaptureFailure("CAPTURE_CLOCK_NAIVE")
    now = now.astimezone(timezone.utc)
    url = source_url(target_date)
    opener = opener or request.build_opener(RefuseRedirect())
    req = request.Request(url, headers={"Accept": "application/json", "User-Agent": "WOW-NBA-research-one-shot/1.0"})
    try:
        with opener.open(req, timeout=12) as res:
            if res.geturl() != url:
                raise CaptureFailure("SOURCE_REDIRECT_REFUSED")
            if getattr(res, "status", res.getcode()) != 200:
                raise CaptureFailure("SOURCE_HTTP_UNEXPECTED_STATUS")
            content_type = res.headers.get("Content-Type", "")
            if "application/json" not in content_type.lower():
                raise CaptureFailure("SOURCE_CONTENT_TYPE_INVALID")
            date_header = res.headers.get("Date")
            if not date_header:
                raise CaptureFailure("SOURCE_HTTP_DATE_MISSING")
            try:
                source_time = parsedate_to_datetime(date_header)
                if source_time.tzinfo is None:
                    raise ValueError("timezone required")
                source_time = source_time.astimezone(timezone.utc)
            except (TypeError, ValueError, IndexError) as exc:
                raise CaptureFailure("SOURCE_HTTP_DATE_INVALID") from exc
            if abs((now - source_time).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
                raise CaptureFailure("SOURCE_HTTP_DATE_STALE_OR_SKEWED")
            raw = res.read(MAX_SOURCE_BYTES + 1)
            if len(raw) > MAX_SOURCE_BYTES:
                raise CaptureFailure("SOURCE_BODY_TOO_LARGE")
            if not raw:
                raise CaptureFailure("SOURCE_BODY_EMPTY")
            meta = {
                "source_url": url, "source_http_date": source_time.isoformat(),
                "capture_host_clock_utc": now.isoformat(), "source_content_type": content_type,
                "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
            }
            return raw, meta
    except CaptureFailure:
        raise
    except error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise CaptureFailure("SOURCE_REDIRECT_REFUSED") from exc
        raise CaptureFailure("SOURCE_HTTP_ERROR") from exc
    except (OSError, error.URLError) as exc:
        raise CaptureFailure("SOURCE_FETCH_FAILED") from exc

def _competitor_identity(event: dict) -> dict:
    """Extract provider home/away IDs, never infer participants from matchup names.

    Research receipts must preserve exact team identity for a later independent
    canonical resolver. Missing, duplicated or mismatched IDs fail closed.
    """
    try:
        competitions = event["competitions"]
        competitors = competitions[0]["competitors"]
        if not isinstance(competitors, list) or len(competitors) != 2:
            raise ValueError("exactly two competitors required")
        resolved = {}
        for competitor in competitors:
            side = competitor["homeAway"]
            team = competitor["team"]
            if not isinstance(team, dict):
                raise ValueError("team metadata invalid")
            team_id = str(team.get("id") or "").strip()
            name = str(team.get("displayName") or "").strip()
            if side not in {"home", "away"} or side in resolved:
                raise ValueError("duplicate/unknown side")
            if not team_id.isdecimal() or not name:
                raise ValueError("missing/untrusted team identity")
            resolved[side] = {"team_id": team_id, "name": name}
        if set(resolved) != {"home", "away"} or resolved["home"]["team_id"] == resolved["away"]["team_id"]:
            raise ValueError("missing sides or same team")
        return {"home": resolved["home"], "away": resolved["away"]}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise CaptureFailure("SOURCE_TEAM_IDENTITY_MALFORMED") from exc


def classify_events(raw: bytes, *, now: datetime, min_lead_seconds: int = MIN_LEAD_SECONDS) -> dict:
    if min_lead_seconds < MIN_LEAD_SECONDS:
        raise CaptureFailure("BAD_MIN_LEAD")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CaptureFailure("SOURCE_JSON_INVALID") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise CaptureFailure("SOURCE_EVENTS_MALFORMED")
    eligible, skipped = [], []
    seen = set()
    for event in payload["events"]:
        if not isinstance(event, dict) or not str(event.get("id", "")).strip():
            raise CaptureFailure("SOURCE_EVENT_IDENTITY_MALFORMED")
        event_id = str(event["id"]).strip()
        if event_id in seen:
            raise CaptureFailure("SOURCE_EVENT_IDENTITY_DUPLICATE")
        seen.add(event_id)
        event_start = utc_datetime(event.get("date"))
        state = ((event.get("status") or {}).get("type") or {}).get("state")
        if not isinstance(state, str):
            raise CaptureFailure("SOURCE_EVENT_STATUS_MALFORMED")
        lead_seconds = (event_start - now).total_seconds()
        identity = _competitor_identity(event)
        summary = {"event_id": event_id, "event_start_utc": event_start.isoformat(),
                   "state": state, "lead_seconds": int(lead_seconds), **identity}
        if state.lower() == "pre" and lead_seconds >= min_lead_seconds:
            eligible.append(summary)
        else:
            skipped.append({**summary, "reason": "EVENT_NOT_FUTURE_PREGAME" if state.lower() != "pre" else "MIN_LEAD_NOT_MET"})
    return {"events_total": len(seen), "events_pregame_eligible": eligible, "events_skipped": skipped}

def _atomic_bytes(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".nba-one-shot-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

def capture(target_date: str, outdir: Path, *, now: datetime | None = None, opener=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise CaptureFailure("CAPTURE_CLOCK_NAIVE")
    now = now.astimezone(timezone.utc)
    raw, meta = fetch_source(target_date, now=now, opener=opener)
    count = classify_events(raw, now=now)
    manifest = {
        "schema_version": MANIFEST_SCHEMA, "sport": "NBA", "target_date": utc_date(target_date),
        **meta, **count, "data_provenance": "ESPN_PUBLIC_SCOREBOARD_HTTP",
        "independent_timestamp_attestation": "NOT_ESTABLISHED",
        "v5_1_feature_compatibility": "UNVERIFIED_NO_FEATURES_COMPUTED",
        "probability_publishable": False, "can_execute": False,
        "disposition": "RAW_SOURCE_CAPTURE_ONLY",
    }
    outdir = Path(outdir)
    raw_path = outdir / f"nba_{target_date}_espn_raw.json"
    receipt_path = outdir / f"nba_{target_date}_capture_receipt.json"
    if raw_path.exists() or receipt_path.exists():
        raise CaptureFailure("CAPTURE_OUTPUT_ALREADY_EXISTS")
    _atomic_bytes(raw_path, raw)
    _atomic_bytes(receipt_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return manifest

def verify(outdir: Path, target_date: str) -> dict:
    outdir = Path(outdir)
    raw_path = outdir / f"nba_{utc_date(target_date)}_espn_raw.json"
    receipt_path = outdir / f"nba_{target_date}_capture_receipt.json"
    try:
        raw = raw_path.read_bytes()
        manifest = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CaptureFailure("CAPTURE_RECEIPT_UNREADABLE") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise CaptureFailure("CAPTURE_RECEIPT_MALFORMED")
    if manifest.get("target_date") != target_date or manifest.get("can_execute") is not False or manifest.get("probability_publishable") is not False:
        raise CaptureFailure("CAPTURE_RECEIPT_MALFORMED")
    if hashlib.sha256(raw).hexdigest() != manifest.get("raw_sha256") or len(raw) != manifest.get("raw_bytes"):
        raise CaptureFailure("CAPTURE_RAW_HASH_MISMATCH")
    now = utc_datetime(manifest.get("capture_host_clock_utc"))
    events = classify_events(raw, now=now)
    for k, value in events.items():
        if value != manifest.get(k):
            raise CaptureFailure("CAPTURE_RECEIPT_EVENT_MISMATCH")
    return {"status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS", "raw_sha256": manifest["raw_sha256"],
            "can_execute": False, "independent_timestamp_attestation": "NOT_ESTABLISHED",
            "v5_1_feature_compatibility": "UNVERIFIED"}

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    c = commands.add_parser("capture")
    c.add_argument("--date", required=True)
    c.add_argument("--outdir", required=True, type=Path)
    v = commands.add_parser("verify")
    v.add_argument("--date", required=True)
    v.add_argument("--outdir", required=True, type=Path)
    args = p.parse_args()
    try:
        result = capture(args.date, args.outdir) if args.command == "capture" else verify(args.outdir, args.date)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except CaptureFailure as exc:
        print(json.dumps({"status": "BLOCKED", "reason_code": exc.code, "can_execute": False}))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
