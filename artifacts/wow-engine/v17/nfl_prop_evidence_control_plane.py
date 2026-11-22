"""Bounded OIDC control plane for NFL core player-prop forward evidence.

This producer is intentionally separate from the existing NFL team-event forward
shadow. It discovers only players attached to official pregame ESPN events,
selects route-eligible offensive positions, hydrates through the governed NFL
prop evidence adapter, and persists immutable discovery-only snapshots.

No sporting probability, calibration, certification, promotion, publication,
ranking, pricing, or execution authority is created. can_execute=false.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable, Mapping

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from github_actions_oidc import scout_route_auth_dependency
from v17 import memory_admission
from pick_request_runtime_core import PickRequestRow, RawPropEvidence, _snapshot_payload, _validate_evidence
from prop_auto_hydration import PropAutoHydrationError
from prop_auto_hydration_router import auto_hydrate_prop_evidence
import nfl_prop_auto_hydration as nfl
from v17.nfl_prop_event_aware_player_identity import install_nfl_event_aware_player_identity
from v17.player_availability_gate import evaluate_player_availability

CAN_EXECUTE = False
ROUTE_PATH = "/internal/v17/nfl-prop-forward-evidence/acquire"
SPORT = "NFL"
SOURCE_TYPE = "AUTONOMOUS_DISCOVERY"
PLATFORM = "ESPN_NFL_IDENTITY_NFLVERSE_STATS_FORWARD_V1"
SCOREBOARD_URL = nfl.ESPN_SCOREBOARD_URL
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
STAT_TYPES = ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD")
POSITION_STATS: dict[str, tuple[str, ...]] = {
    "QB": ("PASSING_YARDS", "RUSHING_YARDS", "ANYTIME_TD"),
    "RB": ("RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"),
    "FB": ("RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"),
    "WR": ("RECEIVING_YARDS", "ANYTIME_TD"),
    "TE": ("RECEIVING_YARDS", "ANYTIME_TD"),
}

# Make the #982 event-aware identity boundary unconditional for this producer.
install_nfl_event_aware_player_identity()


class NFLForwardEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_date: str
    candidate_offset: int = Field(default=0, ge=0, le=4000)
    max_candidates: int = Field(default=32, ge=1, le=64)
    # Optional deterministic scheduler rotation; existing offset clients
    # retain their exact historical semantics when this is absent.
    candidate_rotation: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @model_validator(mode="after")
    def _exclusive_paging(self) -> "NFLForwardEvidenceRequest":
        if self.candidate_rotation is not None and self.candidate_offset != 0:
            raise ValueError("candidate_rotation and candidate_offset are mutually exclusive")
        return self


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _cached_http_get(http_get: Callable[..., Any]) -> Callable[..., Any]:
    cache: dict[tuple[Any, ...], Any] = {}

    def get(url: str, params=None, headers=None, **kwargs: Any) -> Any:
        params_key = tuple(sorted((str(k), str(v)) for k, v in dict(params or {}).items()))
        headers_key = tuple(sorted((str(k).lower(), str(v)) for k, v in dict(headers or {}).items()))
        key = (str(url), params_key, headers_key)
        if key not in cache:
            cache[key] = http_get(url, params=params, headers=headers, **kwargs)
        return cache[key]

    return get


def _json(url: str, *, http_get: Callable[..., Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = http_get(
        url,
        params=params or {},
        headers={"User-Agent": "WOW-Research/1.0", "Accept": "application/json"},
        timeout=12.0,
        follow_redirects=True,
    )
    status = int(getattr(response, "status_code", 200))
    if status >= 400:
        raise RuntimeError(f"HTTP_{status}")
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise TypeError("JSON_NOT_OBJECT")
    return dict(payload)


def _pregame_events(requested_date: str, *, now: datetime, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    date_key = requested_date.replace("-", "")
    payload = _json(SCOREBOARD_URL, http_get=http_get, params={"dates": date_key, "limit": 100})
    events: list[dict[str, Any]] = []
    for raw in payload.get("events") or []:
        if not isinstance(raw, Mapping):
            continue
        event = dict(raw)
        start = _aware(event.get("date"))
        if start is None or start <= now:
            continue
        status_type = ((event.get("status") or {}).get("type") or {}) if isinstance(event.get("status"), Mapping) else {}
        if bool(status_type.get("completed")) or str(status_type.get("state") or "pre").lower() != "pre":
            continue
        canonical = nfl._canonical_event_metadata(event)
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions and isinstance(competitions[0], Mapping) else {}
        competitors = competition.get("competitors") if isinstance(competition, Mapping) else []
        teams: list[dict[str, Any]] = []
        for competitor in competitors or []:
            if not isinstance(competitor, Mapping):
                continue
            team = competitor.get("team") if isinstance(competitor.get("team"), Mapping) else {}
            team_id = str(team.get("id") or "").strip()
            abbr = str(team.get("abbreviation") or "").upper().strip()
            if team_id and abbr:
                teams.append({"team_id": team_id, "abbr": abbr})
        if len(teams) != 2:
            continue
        events.append({
            "provider_event_id": str(event.get("id") or ""),
            "canonical_event_id": canonical["verified_canonical_event_id"],
            "event_start_time": start.isoformat(),
            "teams": teams,
        })
    events.sort(key=lambda row: (row["event_start_time"], row["canonical_event_id"]))
    return events


def _roster(team_id: str, *, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    payload = _json(ROSTER_URL.format(team_id=team_id), http_get=http_get)
    athletes = payload.get("athletes") or []
    if athletes and isinstance(athletes[0], Mapping) and "items" in athletes[0]:
        flattened: list[Any] = []
        for group in athletes:
            if isinstance(group, Mapping):
                flattened.extend(group.get("items") or [])
        athletes = flattened
    rows: list[dict[str, Any]] = []
    for athlete in athletes:
        if not isinstance(athlete, Mapping):
            continue
        name = " ".join(str(athlete.get("fullName") or athlete.get("displayName") or "").split())
        position = str((athlete.get("position") or {}).get("abbreviation") or "").upper().strip()
        if not name or position not in POSITION_STATS:
            continue
        rows.append({
            "player": name,
            "player_id": str(athlete.get("id") or "").strip() or None,
            "position": position,
        })
    rows.sort(key=lambda row: (row["position"], row["player"]))
    return rows


def _discover_candidates(requested_date: str, *, now: datetime, http_get: Callable[..., Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in _pregame_events(requested_date, now=now, http_get=http_get):
        teams = event["teams"]
        for team in teams:
            opponent = next(item["abbr"] for item in teams if item["abbr"] != team["abbr"])
            for player in _roster(team["team_id"], http_get=http_get):
                for stat_type in POSITION_STATS[player["position"]]:
                    identity = (event["canonical_event_id"], str(player["player_id"] or player["player"]), stat_type)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    candidates.append({
                        **event,
                        **player,
                        "team": team["abbr"],
                        "opponent": opponent,
                        "stat_type": stat_type,
                    })
    candidates.sort(key=lambda row: (
        row["event_start_time"], row["team"], row["player"], row["stat_type"]
    ))
    return candidates


def _candidate_line(game_log: list[float], stat_type: str) -> float:
    values = [float(value) for value in game_log]
    if not values:
        raise ValueError("game_log cannot be empty")
    if stat_type == "ANYTIME_TD":
        return 0.5
    return float(int(float(median(values))) + 0.5)


def _existing_snapshot(db: Any, candidate: dict[str, Any], *, now: datetime) -> bool:
    """Only skip a current, officially ready snapshot; stale/blocked evidence must refresh.

    Historical existence alone is not proof of present availability. A prior
    blocked or stale snapshot cannot permanently starve an exact NFL prop
    identity from evidence re-acquisition. The scorer's independent pregame
    gate remains authoritative after a newly captured snapshot.
    """
    result = (
        db.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id,captured_at,hydration_status,blockers,role_status")
        .eq("event_id", str(candidate["canonical_event_id"]))
        .eq("sport", SPORT)
        .eq("player", str(candidate["player"]))
        .eq("stat_type", str(candidate["stat_type"]))
        .order("captured_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return False
    latest = rows[0]
    if str(latest.get("hydration_status") or "").upper() != "PASS":
        return False
    if latest.get("blockers"):
        return False
    availability = evaluate_player_availability(latest.get("role_status"))
    if availability.blocker or availability.requires_revalidation:
        return False
    captured_at = _aware(latest.get("captured_at"))
    if captured_at is None or captured_at > now:
        return False
    return now - captured_at <= timedelta(minutes=30)


def acquire_nfl_forward_evidence_batch(
    req: NFLForwardEvidenceRequest,
    *,
    db: Any,
    now: datetime | None = None,
    http_get: Callable[..., Any] = httpx.get,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cached_get = _cached_http_get(http_get)
    result: dict[str, Any] = {
        "status": "COMPLETED",
        "sport": SPORT,
        "requested_date": req.requested_date,
        "candidate_offset": req.candidate_offset,
        "candidate_rotation": req.candidate_rotation,
        "rotation_page_n": 0,
        "max_candidates": req.max_candidates,
        "total_candidates": 0,
        "window_candidate_n": 0,
        "attempted": 0,
        "already_captured": 0,
        "hydrated": 0,
        "persisted": 0,
        "held": 0,
        "snapshot_write_failed": 0,
        "snapshot_write_succeeded": 0,
        "explicit_prewrite_exclusions": 0,
        "receipts": [],
        "next_offset": None,
        "blockers": [],
        "stat_types": list(STAT_TYPES),
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
    try:
        candidates = _discover_candidates(req.requested_date, now=now, http_get=cached_get)
    except Exception as exc:
        result["status"] = "DATA_UNOBTAINABLE"
        result["blockers"] = [getattr(exc, "code", f"NFL_PROP_DISCOVERY_FAILED:{type(exc).__name__}")]
        return result

    result["total_candidates"] = len(candidates)
    page_n = (len(candidates) + req.max_candidates - 1) // req.max_candidates
    result["rotation_page_n"] = page_n
    if req.candidate_rotation is None:
        start = min(req.candidate_offset, len(candidates))
    else:
        # Bound every scheduled batch to a real candidate page, including
        # small slates; no fixed ten-page cap can starve the rest of a slate.
        start = (req.candidate_rotation % page_n) * req.max_candidates if page_n else 0
    result["candidate_offset"] = start
    end = min(start + req.max_candidates, len(candidates))
    window = candidates[start:end]
    result["window_candidate_n"] = len(window)
    result["next_offset"] = end if end < len(candidates) else None

    for candidate in window:
        result["attempted"] += 1
        receipt = {
            "event_id": candidate["canonical_event_id"],
            "event_start_time": candidate["event_start_time"],
            "sport": SPORT,
            "player": candidate["player"],
            "stat_type": candidate["stat_type"],
            "source_type": SOURCE_TYPE,
            "source_snapshot_id": None,
            "write_status": "PREWRITE_PENDING",
            "terminal_reason": None,
            "can_execute": False,
        }
        result["receipts"].append(receipt)
        try:
            if _existing_snapshot(db, candidate, now=now):
                result["already_captured"] += 1
                result["explicit_prewrite_exclusions"] += 1
                receipt["write_status"] = "ALREADY_CAPTURED"
                receipt["terminal_reason"] = "NFL_EXISTING_SNAPSHOT_ALREADY_CAPTURED"
                continue
        except Exception as exc:
            result["held"] += 1
            result["explicit_prewrite_exclusions"] += 1
            reason = f" NFL_EXISTING_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}".strip()
            receipt.update(write_status="PREWRITE_EXCLUDED", terminal_reason=reason)
            result["blockers"].append(f"{candidate['player']}:{candidate['stat_type']}:{reason}")
            continue
        writing = False
        try:
            raw = auto_hydrate_prop_evidence(
                sport=SPORT,
                player=str(candidate["player"]),
                stat_type=str(candidate["stat_type"]),
                event_start_time=str(candidate["event_start_time"]),
                http_get=cached_get,
                now=now,
                source_capture_timestamp=now.isoformat(),
                source_label="V17_NFL_OIDC_FORWARD_DISCOVERY",
                opponent=str(candidate["opponent"]),
                canonical_event_id=str(candidate["canonical_event_id"]),
            )
            evidence = RawPropEvidence.model_validate(raw)
            line = _candidate_line(evidence.game_log, str(candidate["stat_type"]))
            row = PickRequestRow(
                row_key=(
                    f"nfl-forward:{candidate['canonical_event_id']}:{candidate['player']}:"
                    f"{candidate['stat_type']}:{line}"
                ),
                event_id=str(candidate["canonical_event_id"]),
                event_start_time=str(candidate["event_start_time"]),
                sport=SPORT,
                player=str(candidate["player"]),
                stat_type=str(candidate["stat_type"]),
                line=line,
                direction="MORE",
                evidence=evidence,
                source_type=SOURCE_TYPE,
                platform=PLATFORM,
                opponent=str(candidate["opponent"]),
                source_capture_timestamp=now.isoformat(),
            )
            normalized = _validate_evidence(row, str(candidate["stat_type"]))
            snapshot_id, _fingerprint, snapshot = _snapshot_payload(row, normalized)
            snapshot["source_snapshot_id"] = snapshot_id
            result["hydrated"] += 1
            writing = True
            receipt.update(source_snapshot_id=snapshot_id, write_status="SNAPSHOT_WRITE_PENDING")
            db.table("wow_prop_evidence_snapshots").upsert(snapshot, on_conflict="source_snapshot_id").execute()
            result["persisted"] += 1
            result["snapshot_write_succeeded"] += 1
            receipt.update(
                write_status="SNAPSHOT_WRITE_SUCCEEDED",
                terminal_reason="PERSISTED_AWAITING_CANONICAL_READBACK",
            )
        except PropAutoHydrationError as exc:
            result["held"] += 1
            result["explicit_prewrite_exclusions"] += 1
            receipt.update(write_status="PREWRITE_EXCLUDED", terminal_reason=exc.code)
            result["blockers"].append(f"{candidate['player']}:{candidate['stat_type']}:{exc.code}")
        except ValueError as exc:
            reason = " ".join(str(exc).split())[:120] or type(exc).__name__
            result["held"] += 1
            if writing:
                result["snapshot_write_failed"] += 1
                receipt.update(write_status="SNAPSHOT_WRITE_FAILED",
                               terminal_reason=f"NFL_FORWARD_SNAPSHOT_WRITE_FAILED:{type(exc).__name__}")
            else:
                result["explicit_prewrite_exclusions"] += 1
                receipt.update(write_status="PREWRITE_EXCLUDED",
                               terminal_reason=f"NFL_FORWARD_EVIDENCE_INVALID:{reason}")
            result["blockers"].append(
                f"{candidate['player']}:{candidate['stat_type']}:{receipt['terminal_reason']}"
            )
        except Exception as exc:
            result["held"] += 1
            if writing:
                result["snapshot_write_failed"] += 1
                receipt.update(write_status="SNAPSHOT_WRITE_FAILED",
                               terminal_reason=f"NFL_FORWARD_SNAPSHOT_WRITE_FAILED:{type(exc).__name__}")
            else:
                result["explicit_prewrite_exclusions"] += 1
                receipt.update(write_status="PREWRITE_EXCLUDED",
                               terminal_reason=f"NFL_FORWARD_ACQUISITION_ERROR:{type(exc).__name__}")
            result["blockers"].append(
                f"{candidate['player']}:{candidate['stat_type']}:{receipt['terminal_reason']}"
            )

    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    if result["snapshot_write_failed"] and result["persisted"] == 0:
        result["status"] = "RUN_INVALID_PROP_SNAPSHOT_WRITE_FAILURE"
    elif result["held"]:
        result["status"] = "COMPLETED_WITH_ROW_BLOCKERS"
    return result


def install_nfl_prop_forward_evidence_route(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    if any(getattr(route, "path", None) == ROUTE_PATH for route in app.router.routes):
        return

    @app.post(
        ROUTE_PATH,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="acquireWowV17NflPropForwardEvidence",
    )
    def acquire(req: NFLForwardEvidenceRequest) -> dict[str, Any]:
        try:
            return acquire_nfl_forward_evidence_batch(req, db=db_client_fn())
        finally:
            memory_admission.release_process_memory()


__all__ = [
    "CAN_EXECUTE",
    "ROUTE_PATH",
    "STAT_TYPES",
    "POSITION_STATS",
    "NFLForwardEvidenceRequest",
    "acquire_nfl_forward_evidence_batch",
    "install_nfl_prop_forward_evidence_route",
]
