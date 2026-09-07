"""Automatic WOW V17 Multi-Scout -> governed scoring handoff.

The Nightly Multi-Scout is discovery-only.  This bridge converts its stamped
handoff into the *existing* production scoring contracts rather than creating a
second probability implementation:

* autonomous scalar/player props -> POST /score-pick-request
* team/event winner + upset objectives -> POST /score-team-event-request

Those server-owned routes retain evidence hydration, fitted specialist,
calibration, market/portfolio/final-refresh/terminal behavior for the lanes they
support.  Unsupported lanes remain typed fail-closed outcomes.  Sportsbook
prices and Scout agreement are never promoted to model probabilities here.

The bridge writes a complete receipt even when the backend/auth/transport is
unavailable, so a completed Scout run can never silently look downstream-
complete.  can_execute is false throughout.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ACTION_ORIGIN = os.environ.get(
    "WOW_ACTION_ORIGIN",
    "https://wow-governed-probability-engine.onrender.com",
).rstrip("/")
USER_TIMEZONE = os.environ.get("WOW_USER_TIMEZONE", "America/Chicago")
MAX_PROP_ROWS = 50
MAX_TEAM_EVENT_ROWS = 100

SPORT_KEY_MAP = {
    "baseball_mlb": ("MLB", "MLB"),
    "basketball_nba": ("NBA", "NBA"),
    "basketball_wnba": ("WNBA", "WNBA"),
    "basketball_ncaab": ("NCAAB", "NCAAB"),
    "americanfootball_nfl": ("NFL", "NFL"),
    "americanfootball_ncaaf": ("NCAAF", "NCAAF"),
    "icehockey_nhl": ("NHL", "NHL"),
}


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _sport_identity(sport_key: str) -> tuple[str, str]:
    key = str(sport_key or "").strip().lower()
    if key in SPORT_KEY_MAP:
        return SPORT_KEY_MAP[key]
    if key.startswith("tennis_"):
        league = "WTA" if "wta" in key else ("ATP" if "atp" in key else "TENNIS")
        return "TENNIS", league
    # Unknown public-feed sport identities stay explicit and will fail closed
    # in the controlling specialist lane rather than being guessed into a
    # supported sport.
    return key.upper(), key.upper()


def _direction(value: Any) -> str | None:
    label = str(value or "").strip().upper()
    if label in {"OVER", "MORE"}:
        return "MORE"
    if label in {"UNDER", "LESS"}:
        return "LESS"
    return None


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _event_date(value: Any, timezone_name: str = USER_TIMEZONE) -> str | None:
    parsed = _aware(value)
    if parsed is None:
        return None
    try:
        return parsed.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return None


def _prop_row(candidate: dict[str, Any], *, generated_at: str, index: int) -> tuple[dict[str, Any] | None, str | None]:
    evidence = candidate.get("market_evidence")
    if not isinstance(evidence, dict):
        return None, "PROP_MARKET_EVIDENCE_MISSING"

    participant = str(evidence.get("description") or "").strip()
    direction = _direction(evidence.get("outcome_name"))
    line = evidence.get("point")
    event_id = str(candidate.get("official_event_id") or "").strip()
    event_start = str(candidate.get("commence_time") or "").strip()
    stat_type = str(evidence.get("market_key") or "").strip()
    sport, league = _sport_identity(str(candidate.get("sport_key") or ""))

    if not participant:
        return None, "PROP_PARTICIPANT_UNRESOLVED"
    if direction is None:
        return None, "PROP_DIRECTION_UNSUPPORTED"
    if isinstance(line, bool) or not isinstance(line, (int, float)):
        return None, "PROP_EXACT_LINE_UNRESOLVED"
    if not event_id or not event_start or _aware(event_start) is None:
        return None, "PROP_EVENT_IDENTITY_INCOMPLETE"
    if not stat_type or not sport:
        return None, "PROP_MARKET_IDENTITY_INCOMPLETE"

    captured_at = (
        evidence.get("market_last_update")
        or evidence.get("bookmaker_last_update")
        or generated_at
    )
    return {
        "row_key": f"scout-prop-{index}",
        "event_id": event_id,
        "event_start_time": event_start,
        "sport": sport,
        "player": participant,
        "stat_type": stat_type,
        "line": float(line),
        "direction": direction,
        "source_type": "AUTONOMOUS_DISCOVERY",
        "league": league or None,
        "source_capture_timestamp": captured_at,
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }, None


def _prop_dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("event_id") or ""),
        str(row.get("sport") or "").upper(),
        str(row.get("player") or "").casefold(),
        str(row.get("stat_type") or "").upper(),
        float(row.get("line")),
        str(row.get("direction") or "").upper(),
    )


def _team_rows(candidate: dict[str, Any], *, research_run_id: str) -> tuple[list[dict[str, Any]], str | None]:
    event_id = str(candidate.get("official_event_id") or "").strip()
    sport, league = _sport_identity(str(candidate.get("sport_key") or ""))
    event_start = candidate.get("commence_time")
    event_date = _event_date(event_start)
    if not event_id or not sport or not league or not event_date:
        return [], "TEAM_EVENT_IDENTITY_INCOMPLETE"

    event_key = f"{sport}:{event_id}"
    common = {
        "research_run_id": research_run_id,
        "sport": sport,
        "league": league,
        "event_key": event_key,
        "event_state": "PREGAME",
        "event_date": event_date,
        "timezone": USER_TIMEZONE,
    }
    return [
        {
            **common,
            "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
            "price_required_for_objective": False,
        },
        {
            **common,
            "objective_lane": "UPSET_PROBABILITY",
            "price_required_for_objective": True,
        },
    ], None


def build_dispatch(handoff: dict[str, Any]) -> dict[str, Any]:
    run_id = str(handoff.get("run_id") or "").strip()
    research_run_id = str(handoff.get("research_run_id") or "").strip()
    governance = handoff.get("governance") if isinstance(handoff.get("governance"), dict) else {}
    model_handoff = handoff.get("model_handoff") if isinstance(handoff.get("model_handoff"), dict) else {}

    if not run_id or not research_run_id:
        raise ValueError("SCOUT_RUN_IDENTITY_MISSING")
    if handoff.get("status") != "DISCOVERY_COMPLETE" or handoff.get("model_handoff_ready") is not True:
        raise ValueError("SCOUT_HANDOFF_NOT_READY")
    if governance.get("can_execute") is not False:
        raise ValueError("SCOUT_EXECUTION_GOVERNANCE_VIOLATION")

    generated_at = str(handoff.get("generated_at") or datetime.now(timezone.utc).isoformat())
    raw_props = model_handoff.get("prop_candidates") if isinstance(model_handoff.get("prop_candidates"), list) else []
    raw_team = model_handoff.get("team_event_candidates") if isinstance(model_handoff.get("team_event_candidates"), list) else []

    prop_rows: list[dict[str, Any]] = []
    prop_rejected: list[dict[str, Any]] = []
    prop_duplicates = 0
    seen_props: set[tuple[Any, ...]] = set()
    for index, candidate in enumerate(raw_props, 1):
        if not isinstance(candidate, dict):
            prop_rejected.append({"source_index": index, "code": "PROP_CANDIDATE_INVALID"})
            continue
        row, code = _prop_row(candidate, generated_at=generated_at, index=index)
        if row is None:
            prop_rejected.append({"source_index": index, "code": code or "PROP_MAPPING_FAILED"})
            continue
        key = _prop_dedupe_key(row)
        if key in seen_props:
            prop_duplicates += 1
            continue
        seen_props.add(key)
        prop_rows.append(row)

    team_rows: list[dict[str, Any]] = []
    team_rejected: list[dict[str, Any]] = []
    team_events_mapped = 0
    for index, candidate in enumerate(raw_team, 1):
        if not isinstance(candidate, dict):
            team_rejected.append({"source_index": index, "code": "TEAM_EVENT_CANDIDATE_INVALID"})
            continue
        rows, code = _team_rows(candidate, research_run_id=research_run_id)
        if not rows:
            team_rejected.append({"source_index": index, "code": code or "TEAM_EVENT_MAPPING_FAILED"})
            continue
        team_events_mapped += 1
        team_rows.extend(rows)

    source_prop_balanced = len(raw_props) == len(prop_rows) + prop_duplicates + len(prop_rejected)
    source_team_balanced = len(raw_team) == team_events_mapped + len(team_rejected)
    return {
        "source_run_id": run_id,
        "research_run_id": research_run_id,
        "prop_batches": [
            {"request_id": f"{research_run_id}:props:{i + 1}", "rows": rows}
            for i, rows in enumerate(_chunks(prop_rows, MAX_PROP_ROWS))
        ],
        "team_event_batches": [
            {"rows": rows}
            for rows in _chunks(team_rows, MAX_TEAM_EVENT_ROWS)
        ],
        "mapping": {
            "source_prop_rows": len(raw_props),
            "mapped_prop_rows": len(prop_rows),
            "duplicate_prop_rows": prop_duplicates,
            "rejected_prop_rows": prop_rejected,
            "source_team_event_rows": len(raw_team),
            "mapped_team_events": team_events_mapped,
            "downstream_team_objective_rows": len(team_rows),
            "rejected_team_event_rows": team_rejected,
            "source_prop_reconciliation_pass": source_prop_balanced,
            "source_team_reconciliation_pass": source_team_balanced,
        },
        "governance": {
            "source_is_discovery_only": True,
            "scout_probability_authority": False,
            "sportsbook_probability_authority": False,
            "controlling_specialist_required": True,
            "can_execute": False,
        },
    }


def _post_json(origin: str, path: str, token: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    request = Request(
        f"{origin.rstrip('/')}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return {"ok": True, "http_status": int(response.status), "body": body, "can_execute": False}
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"code": f"HTTP_{exc.code}"}
        return {"ok": False, "http_status": int(exc.code), "body": body, "can_execute": False}
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "http_status": None,
            "body": {"code": "AUTO_ADVANCE_TRANSPORT_FAILURE", "error_type": type(exc).__name__},
            "can_execute": False,
        }


def execute_auto_advance(
    handoff: dict[str, Any],
    *,
    token: str | None,
    origin: str = ACTION_ORIGIN,
    post_fn: Any = _post_json,
) -> dict[str, Any]:
    try:
        dispatch = build_dispatch(handoff)
    except Exception as exc:
        return {
            "schema_version": "wow.v17.multiscout.auto-advance.v1",
            "status": "BLOCKED_INVALID_SCOUT_HANDOFF",
            "code": str(exc),
            "can_execute": False,
        }

    base = {
        "schema_version": "wow.v17.multiscout.auto-advance.v1",
        "source_run_id": dispatch["source_run_id"],
        "research_run_id": dispatch["research_run_id"],
        "mapping": dispatch["mapping"],
        "routes": {
            "props": "/score-pick-request",
            "team_events": "/score-team-event-request",
        },
        "governance": dispatch["governance"],
        "can_execute": False,
    }
    if not token:
        return {
            **base,
            "status": "BLOCKED_BACKEND_HANDOFF",
            "code": "WOW_ACTION_API_KEY_UNCONFIGURED",
            "prop_receipts": [],
            "team_event_receipts": [],
        }

    prop_receipts = [
        post_fn(origin, "/score-pick-request", token, batch)
        for batch in dispatch["prop_batches"]
    ]
    team_receipts = [
        post_fn(origin, "/score-team-event-request", token, batch)
        for batch in dispatch["team_event_batches"]
    ]
    calls = [*prop_receipts, *team_receipts]
    transport_ok = all(receipt.get("ok") is True for receipt in calls)

    expected_prop_rows = dispatch["mapping"]["mapped_prop_rows"]
    expected_team_rows = dispatch["mapping"]["downstream_team_objective_rows"]
    returned_prop_rows = sum(
        len((receipt.get("body") or {}).get("outcomes") or (receipt.get("body") or {}).get("rows") or [])
        for receipt in prop_receipts if receipt.get("ok") is True
    )
    returned_team_rows = sum(
        len((receipt.get("body") or {}).get("rows") or [])
        for receipt in team_receipts if receipt.get("ok") is True
    )

    response_reconciled = (
        returned_prop_rows == expected_prop_rows
        and returned_team_rows == expected_team_rows
        and dispatch["mapping"]["source_prop_reconciliation_pass"] is True
        and dispatch["mapping"]["source_team_reconciliation_pass"] is True
    )
    mapping_blocked_rows = (
        len(dispatch["mapping"].get("rejected_prop_rows") or [])
        + len(dispatch["mapping"].get("rejected_team_event_rows") or [])
    )
    downstream_blocked_rows = 0
    completed_rows = 0
    for receipt in calls:
        body = receipt.get("body") or {}
        rows = body.get("outcomes") or body.get("rows") or []
        for row in rows if isinstance(rows, list) else []:
            status = str((row or {}).get("terminal_status") or (row or {}).get("status") or "").upper()
            if status == "COMPLETED":
                completed_rows += 1
            elif status in {"HELD", "REJECTED", "BLOCKED"}:
                downstream_blocked_rows += 1

    if not transport_ok:
        status = "BLOCKED_BACKEND_HANDOFF"
        code = "AUTO_ADVANCE_BACKEND_REQUEST_FAILED"
    elif not response_reconciled:
        status = "BLOCKED_RECONCILIATION"
        code = "AUTO_ADVANCE_RECONCILIATION_FAILED"
    elif downstream_blocked_rows:
        status = "AUTO_ADVANCE_COMPLETE_WITH_BLOCKERS"
        code = "DOWNSTREAM_GOVERNED_BLOCKERS_PRESENT"
    elif mapping_blocked_rows:
        status = "AUTO_ADVANCE_COMPLETE_WITH_BLOCKERS"
        code = "SCOUT_MAPPING_BLOCKERS_PRESENT"
    else:
        status = "AUTO_ADVANCE_COMPLETE"
        code = "AUTO_ADVANCE_RECONCILED"

    return {
        **base,
        "status": status,
        "code": code,
        "prop_receipts": prop_receipts,
        "team_event_receipts": team_receipts,
        "reconciliation": {
            "expected_prop_rows": expected_prop_rows,
            "returned_prop_rows": returned_prop_rows,
            "expected_team_objective_rows": expected_team_rows,
            "returned_team_objective_rows": returned_team_rows,
            "completed_downstream_rows": completed_rows,
            "blocked_downstream_rows": downstream_blocked_rows,
            "mapping_blocked_rows": mapping_blocked_rows,
            "response_reconciliation_pass": response_reconciled,
        },
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    handoff = json.loads(Path(args.input).read_text(encoding="utf-8"))
    receipt = execute_auto_advance(
        handoff,
        token=os.environ.get("WOW_ACTION_API_KEY"),
        origin=ACTION_ORIGIN,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt.get("status"),
        "code": receipt.get("code"),
        "source_run_id": receipt.get("source_run_id"),
        "research_run_id": receipt.get("research_run_id"),
        "can_execute": False,
    }))
    return 0 if str(receipt.get("status") or "").startswith("AUTO_ADVANCE_COMPLETE") else 3


if __name__ == "__main__":
    raise SystemExit(main())
