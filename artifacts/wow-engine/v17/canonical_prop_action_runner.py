"""Canonical V17 prop Action runner used by GitHub Actions.

This preserves the governed /score-pick-request path and fail-closed semantics.
It adds bounded retries for transient HTTP/transport disconnects and writes a
checkpoint artifact after each completed batch so partial progress is retained.
No wager execution is possible; can_execute remains false.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ORIGIN = os.environ["WOW_ACTION_ORIGIN"].rstrip("/")
ENDPOINT = ORIGIN + "/score-pick-request"
OUTPUT_PATH = Path("v17_canonical_prop_action_result.json")
request_path = Path(os.environ["WOW_REQUEST_FILE"])
request_doc = json.loads(request_path.read_text(encoding="utf-8"))
if request_doc.get("can_execute") is not False:
    raise AssertionError("REQUEST_CAN_EXECUTE_MUST_BE_FALSE")
lines = request_doc.get("lines") or []
if not lines:
    raise AssertionError("PROP_REQUEST_LINES_EMPTY")
slate_date = str(request_doc.get("slate_date") or "").strip()
if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", slate_date):
    raise AssertionError("PROP_REQUEST_SLATE_DATE_INVALID")


def fresh_oidc_token() -> str:
    oidc_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    separator = "&" if "?" in oidc_url else "?"
    oidc_url += separator + urllib.parse.urlencode({"audience": "wow-v17-multiscout"})
    oidc_request = urllib.request.Request(
        oidc_url,
        headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"},
    )
    with urllib.request.urlopen(oidc_request, timeout=20) as response:
        payload = json.load(response)
    token = str(payload.get("value") or "")
    if not token:
        raise RuntimeError("GITHUB_OIDC_TOKEN_MISSING")
    return token


def normalized_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


TEAM_ALIASES = {
    "ARI": "AZ",
    "AZ": "AZ",
    "CHW": "CWS",
    "CWS": "CWS",
    "WSN": "WSH",
    "WSH": "WSH",
    "OAK": "ATH",
    "ATH": "ATH",
}


def canonical_team(value: object) -> str:
    raw = str(value or "").strip().upper()
    return TEAM_ALIASES.get(raw, raw)


schedule_url = (
    "https://statsapi.mlb.com/api/v1/schedule?"
    + urllib.parse.urlencode(
        {
            "sportId": "1",
            "startDate": slate_date,
            "endDate": slate_date,
            "hydrate": "probablePitcher,team",
        }
    )
)
with urllib.request.urlopen(schedule_url, timeout=30) as response:
    schedule = json.load(response)

games: list[dict] = []
for block in schedule.get("dates") or []:
    for game in block.get("games") or []:
        teams = game.get("teams") or {}
        home_node = teams.get("home") or {}
        away_node = teams.get("away") or {}
        games.append(
            {
                "gamePk": game.get("gamePk"),
                "gameDate": game.get("gameDate"),
                "home": canonical_team((home_node.get("team") or {}).get("abbreviation")),
                "away": canonical_team((away_node.get("team") or {}).get("abbreviation")),
                "home_prob": (home_node.get("probablePitcher") or {}).get("fullName") or "",
                "away_prob": (away_node.get("probablePitcher") or {}).get("fullName") or "",
            }
        )


def resolve_game(line: dict) -> dict | None:
    team = canonical_team(line.get("team"))
    opponent = canonical_team(line.get("opponent"))
    matchup = [g for g in games if {g["home"], g["away"]} == {team, opponent}]
    if len(matchup) == 1:
        return matchup[0]
    player_key = normalized_name(line.get("player"))
    probable = [
        g
        for g in games
        if player_key in {normalized_name(g.get("home_prob")), normalized_name(g.get("away_prob"))}
    ]
    return probable[0] if len(probable) == 1 else None


prepared: list[dict] = []
preflight_failures: list[dict] = []
for line in lines:
    game = resolve_game(line)
    if not game or not game.get("gamePk") or not game.get("gameDate"):
        preflight_failures.append(
            {
                "line_key": line.get("row_key"),
                "player": line.get("player"),
                "terminal_status": "HELD",
                "code": "CANONICAL_EVENT_RESOLUTION_FAILED",
                "model_evaluated": False,
                "probability_publishable": False,
                "can_execute": False,
            }
        )
        continue
    directions = [str(v).strip().upper() for v in (line.get("available_directions") or [])]
    if not directions or any(v not in {"MORE", "LESS"} for v in directions):
        preflight_failures.append(
            {
                "line_key": line.get("row_key"),
                "player": line.get("player"),
                "terminal_status": "HELD",
                "code": "DIRECTION_SET_INVALID",
                "model_evaluated": False,
                "probability_publishable": False,
                "can_execute": False,
            }
        )
        continue
    for direction in directions:
        prepared.append(
            {
                "row_key": f"{line['row_key']}-{direction}",
                "event_id": str(game["gamePk"]),
                "event_start_time": str(game["gameDate"]),
                "sport": "MLB",
                "player": line["player"],
                "stat_type": line["stat_type"],
                "line": float(line["line"]),
                "direction": direction,
                "source_type": str(request_doc.get("source_type") or "NORMALIZED"),
                "platform": str(request_doc.get("platform") or "PrizePicks"),
                "league": str(request_doc.get("league") or "MLB"),
                "opponent": line.get("opponent"),
                "source_capture_timestamp": line.get("source_capture_timestamp"),
                "money_lane_status": "PAYOUT_UNRESOLVED",
            }
        )


def write_checkpoint(action_batches: list[dict], *, last_error: str | None = None) -> None:
    checkpoint = {
        "checkpoint": True,
        "request_id": request_doc.get("request_id"),
        "source_request": request_doc,
        "prepared_rows": prepared,
        "preflight_failures": preflight_failures,
        "action_batches": action_batches,
        "last_error": last_error,
        "can_execute": False,
    }
    OUTPUT_PATH.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")


TRANSIENT_TRANSPORT_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    socket.timeout,
    ssl.SSLError,
)

action_batches: list[dict] = []
batch_size = 6
auth_warmup = True
for offset in range(0, len(prepared), batch_size):
    batch = prepared[offset : offset + batch_size]
    batch_number = offset // batch_size + 1
    body = json.dumps(
        {
            "request_id": f"{request_doc.get('request_id')}-{batch_number:02d}",
            "rows": batch,
        }
    ).encode("utf-8")
    result = None
    max_attempts = 60 if auth_warmup else 5
    for attempt in range(1, max_attempts + 1):
        token = fresh_oidc_token()
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.load(response)
            auth_warmup = False
            break
        except urllib.error.HTTPError as exc:
            transient = exc.code in {401, 404, 429, 502, 503, 504}
            if not transient or attempt == max_attempts:
                body_text = exc.read().decode("utf-8", errors="replace")
                error_code = f"PROP_ACTION_HTTP_{exc.code}:{body_text[:1000]}"
                write_checkpoint(action_batches, last_error=error_code)
                raise RuntimeError(error_code) from exc
            delay = 20 if exc.code == 401 else min(5 * attempt, 30)
            print(f"TRANSIENT_HTTP_RETRY batch={batch_number} attempt={attempt} status={exc.code} delay={delay}")
            time.sleep(delay)
        except TRANSIENT_TRANSPORT_ERRORS as exc:
            if attempt == max_attempts:
                error_code = f"PROP_ACTION_TRANSPORT_FAILED:{type(exc).__name__}"
                write_checkpoint(action_batches, last_error=error_code)
                raise RuntimeError(error_code) from exc
            delay = min(5 * attempt, 30)
            print(
                f"TRANSIENT_TRANSPORT_RETRY batch={batch_number} attempt={attempt} "
                f"error={type(exc).__name__} delay={delay}"
            )
            time.sleep(delay)
    if not isinstance(result, dict):
        write_checkpoint(action_batches, last_error="PROP_ACTION_RESPONSE_MISSING")
        raise RuntimeError("PROP_ACTION_RESPONSE_MISSING")
    if result.get("can_execute") is not False:
        write_checkpoint(action_batches, last_error="PROP_ACTION_CAN_EXECUTE_INVARIANT_FAILED")
        raise AssertionError("PROP_ACTION_CAN_EXECUTE_INVARIANT_FAILED")
    rows_out = result.get("rows") or []
    if len(rows_out) != len(batch):
        write_checkpoint(action_batches, last_error="PROP_ACTION_BATCH_RECONCILIATION_FAILED")
        raise AssertionError("PROP_ACTION_BATCH_RECONCILIATION_FAILED")
    action_batches.append(result)
    write_checkpoint(action_batches)
    time.sleep(1)

outcomes: list[dict] = []
for batch in action_batches:
    outcomes.extend(batch.get("rows") or [])

compact_rows: list[dict] = []
for outcome in outcomes:
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    prediction = result.get("prediction") if isinstance(result.get("prediction"), dict) else result
    compact_rows.append(
        {
            "row_key": outcome.get("row_key"),
            "terminal_status": outcome.get("terminal_status"),
            "code": outcome.get("code"),
            "terminal_label": outcome.get("terminal_label"),
            "model_evaluated": outcome.get("model_evaluated"),
            "rank_eligible": outcome.get("rank_eligible"),
            "probability_publishable": outcome.get("probability_publishable"),
            "raw_model_probability": prediction.get("raw_model_probability"),
            "calibrated_probability": prediction.get("calibrated_probability"),
            "calibrated_lower_bound": prediction.get("calibrated_probability_lower_bound")
            or prediction.get("calibrated_lower_bound"),
            "calibrated_upper_bound": prediction.get("calibrated_probability_upper_bound")
            or prediction.get("calibrated_upper_bound"),
            "lineup_evidence_state": outcome.get("lineup_evidence_state")
            or result.get("lineup_evidence_state"),
            "final_refresh_required": outcome.get("final_refresh_required")
            or result.get("final_refresh_required"),
            "blockers": outcome.get("blockers") or result.get("blockers") or [],
            "can_execute": False,
        }
    )

summary = {
    "request_id": request_doc.get("request_id"),
    "source_lines": len(lines),
    "directional_rows_prepared": len(prepared),
    "line_preflight_failures": len(preflight_failures),
    "returned_rows": len(outcomes),
    "completed": sum(1 for r in outcomes if r.get("terminal_status") == "COMPLETED"),
    "held": sum(1 for r in outcomes if r.get("terminal_status") == "HELD"),
    "rejected": sum(1 for r in outcomes if r.get("terminal_status") == "REJECTED"),
    "model_evaluated": sum(1 for r in outcomes if r.get("model_evaluated") is True),
    "probability_publishable": sum(1 for r in outcomes if r.get("probability_publishable") is True),
    "can_execute": False,
}
if len(outcomes) != len(prepared):
    write_checkpoint(action_batches, last_error="PROP_ACTION_GLOBAL_RECONCILIATION_FAILED")
    raise AssertionError("PROP_ACTION_GLOBAL_RECONCILIATION_FAILED")

output = {
    "summary": summary,
    "source_request": request_doc,
    "prepared_rows": prepared,
    "preflight_failures": preflight_failures,
    "action_batches": action_batches,
    "compact_rows": compact_rows,
    "can_execute": False,
}
OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
print("RUN_SUMMARY " + json.dumps(summary, sort_keys=True))
for row in compact_rows:
    print("ROW_RESULT " + json.dumps(row, sort_keys=True, ensure_ascii=False))
