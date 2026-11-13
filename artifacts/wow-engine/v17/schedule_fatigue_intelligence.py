"""Pregame schedule/travel/workload evidence for the existing V17 team-event lane.

Research context only: no fitted coefficients, score modifications, probabilities,
rank eligibility or automatic execution. Input data must have been available before
an explicit pre-event freeze. This audits caller-supplied official-source receipts;
it does NOT independently fetch or authenticate an official schedule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
FEATURE_FAMILY_VERSION = "V17_SCHEDULE_FATIGUE_RESEARCH_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class ScheduleContextError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def _invalid(message: str) -> None:
    raise ScheduleContextError("SCHEDULE_EVIDENCE_INVALID", message)


def _time(raw: Any) -> datetime:
    if not isinstance(raw, (str, datetime)) or not raw:
        _invalid("explicit offset-aware event/source time is required")
    try:
        ts = raw if isinstance(raw, datetime) else datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ScheduleContextError("SCHEDULE_EVIDENCE_INVALID", "invalid timestamp") from exc
    if ts.tzinfo is None or ts.utcoffset() is None:
        _invalid("naive timestamp")
    return ts.astimezone(timezone.utc)


def _venue(raw: Any) -> tuple[float, float, ZoneInfo] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        _invalid("venue must be a mapping")
    if not all(key in raw for key in ("latitude", "longitude", "time_zone")):
        return None
    try:
        lat, lon = float(raw["latitude"]), float(raw["longitude"])
        tz = ZoneInfo(str(raw["time_zone"]))
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ScheduleContextError("SCHEDULE_EVIDENCE_INVALID", "invalid venue coordinates/zone") from exc
    if not all(map(math.isfinite, (lat, lon))) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        _invalid("venue coordinates out of range")
    return lat, lon, tz


def _source(event: Mapping[str, Any], freeze: datetime) -> datetime:
    uri = event.get("source_uri")
    digest = event.get("source_payload_sha256")
    if not isinstance(uri, str) or urlparse(uri).scheme != "https" or not urlparse(uri).netloc:
        _invalid("official-source HTTPS URI required")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        _invalid("immutable source payload hash required")
    available = _time(event.get("source_available_at"))
    if available > freeze:
        raise ScheduleContextError("SCHEDULE_EVIDENCE_AFTER_FREEZE", str(event.get("official_event_id")))
    return available


def _ids(event: Mapping[str, Any]) -> tuple[str, ...]:
    values = event.get("participants")
    if not isinstance(values, (list, tuple)) or not values:
        _invalid("event participants required")
    if any(not isinstance(v, (str, int)) or isinstance(v, bool) for v in values):
        _invalid("participant ID must be a non-null string or integer")
    ids = tuple(str(value).strip() for value in values)
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        _invalid("missing/duplicate participant identity")
    return ids


def _haversine(first: tuple[float, float, ZoneInfo], second: tuple[float, float, ZoneInfo]) -> float:
    lat1, lon1, _ = first
    lat2, lon2, _ = second
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3958.7613 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _profile(pid: str, target: Mapping[str, Any], games: Sequence[Mapping[str, Any]],
             complete: bool) -> dict[str, Any]:
    start = target["_start"]
    at_venue = target["_venue"]
    relevant = sorted((g for g in games if pid in g["_participants"]),
                      key=lambda g: (g["_start"], g["official_event_id"]))
    for earlier, later in zip(relevant, relevant[1:]):
        if later["_start"] < earlier["_end"]:
            _invalid("overlapping completed events for participant")
    previous = relevant[-1] if relevant else None
    target_role = target["roles"].get(pid)
    if target_role is not None and (not isinstance(target_role, str) or
                                    target_role not in {"home", "away", "neutral"}):
        _invalid("invalid target role")
    basis_zone = at_venue[2] if at_venue else timezone.utc
    target_date = start.astimezone(basis_zone).date()

    def recent(days: int) -> list[Mapping[str, Any]]:
        return [g for g in relevant if 0 <= (target_date - g["_start"].astimezone(basis_zone).date()).days < days]

    r4, r6, r7 = recent(4), recent(6), recent(7)
    prior_day = (target_date - previous["_start"].astimezone(basis_zone).date()).days if previous else None
    rest_hours = ((start - previous["_end"]).total_seconds() / 3600) if previous else None
    if rest_hours is not None and rest_hours < 0:
        _invalid("target overlaps previous completed event")
    road = None
    if target_role is not None:
        road = 0
        if target_role == "away":
            road = 1
            for game in reversed(relevant):
                role = game["roles"].get(pid)
                if role is None:
                    road = None
                    break
                if not isinstance(role, str) or role not in {"home", "away", "neutral"}:
                    _invalid("invalid history role")
                if role != "away":
                    break
                road += 1

    last_leg = None
    shift = None
    if previous and previous["_venue"] and at_venue:
        last_leg = round(_haversine(previous["_venue"], at_venue), 1)
        previous_offset = previous["_start"].astimezone(previous["_venue"][2]).utcoffset()
        new_offset = start.astimezone(at_venue[2]).utcoffset()
        shift = round((new_offset - previous_offset).total_seconds() / 3600, 2)

    week_travel = None
    if r7 and at_venue and all(g["_venue"] is not None for g in r7):
        sequence = r7 + [target]
        week_travel = round(sum(_haversine(a["_venue"], b["_venue"])
                                for a, b in zip(sequence, sequence[1:])), 1)

    workload = None
    unit = None
    if r7 and all(isinstance(g.get("workloads"), Mapping) and
                  isinstance(g["workloads"].get(pid), Mapping) for g in r7):
        items = [g["workloads"][pid] for g in r7]
        unit_values = [i.get("unit") for i in items]
        values = [i.get("value") for i in items]
        if (unit_values and all(isinstance(u, str) and u == unit_values[0] for u in unit_values)
                and unit_values[0] in {"minutes", "pitches", "balls", "rounds", "hours", "innings"}):
            try:
                parsed = [float(v) for v in values]
            except (ValueError, TypeError):
                parsed = []
            if parsed and all(math.isfinite(v) and 0 <= v <= 10000 for v in parsed):
                workload, unit = round(sum(parsed), 2), unit_values[0]

    result = {
        "rest_hours": round(rest_hours, 2) if rest_hours is not None and complete else None,
        "back_to_back": (prior_day == 1) if prior_day is not None and complete else None,
        "games_in_4_days_including_target": (len(r4) + 1) if complete else None,
        "games_in_6_days_including_target": (len(r6) + 1) if complete else None,
        "consecutive_road_events_including_target": road if complete else None,
        "last_trip_miles": last_leg if complete else None,
        "travel_miles_last_7d": week_travel if complete else None,
        "last_trip_time_zone_offset_change_hours": shift if complete else None,
        "workload_7d": workload if complete else None,
        "workload_unit": unit if complete else None,
        "overnight_travel_verified": None,  # never infer itinerary/flight timing from venue changes
        "history_completeness_attested": complete,
        "games_observed": len(relevant),
    }
    return result


def build_schedule_matchup_context(*, target: Mapping[str, Any],
                                    history: Sequence[Mapping[str, Any]],
                                    history_completeness: Mapping[str, bool]) -> dict[str, Any]:
    """Audit point-in-time official-source schedule history, never a probability.

    history_completeness is a caller attestation, *not* independently proven.
    Count windows use target venue local calendar dates (or UTC if venue missing).
    Distances are straight-line venue miles, not verified itineraries.
    """
    if not isinstance(target, Mapping) or not isinstance(history_completeness, Mapping):
        _invalid("missing target/completeness")
    if not isinstance(history, (list, tuple)):
        _invalid("history must be a finite sequence")
    sport = str(target.get("sport") or "").upper()
    if sport not in EXPECTED_TEAM_EVENT_SPORTS:
        _invalid("unsupported sport")
    tid = str(target.get("official_event_id") or "").strip()
    if not tid:
        _invalid("missing official event identity")
    start, freeze = _time(target.get("event_start_utc")), _time(target.get("as_of_utc"))
    if start <= freeze:
        _invalid("freeze must strictly precede target event")
    _source(target, freeze)
    ids = _ids(target)
    target_roles = target.get("roles")
    if not isinstance(target_roles, Mapping):
        _invalid("roles mapping required")
    target_venue = _venue(target.get("venue"))
    normalized_target = {**target, "_start": start, "_venue": target_venue}
    normalized: list[dict[str, Any]] = []
    by_id: dict[str, str] = {}
    shared_identities: dict[str, str] = {}
    scheduled_participants: dict[str, set[str]] = {}
    for raw in history:
        if not isinstance(raw, Mapping) or str(raw.get("sport") or "").upper() != sport:
            _invalid("mixed/unknown sport in history")
        eid = str(raw.get("official_event_id") or "").strip()
        if not eid or eid == tid:
            _invalid("invalid/target history event identity")
        g_start = _time(raw.get("event_start_utc"))
        rids = _ids(raw)
        if g_start >= start:
            _invalid("future event in completed historical input")
        _source(raw, freeze)
        shared = json.dumps([sport, g_start.isoformat(), sorted(rids)], sort_keys=True)
        if eid in shared_identities and shared_identities[eid] != shared:
            raise ScheduleContextError("SCHEDULE_IDENTITY_CONFLICT", eid)
        shared_identities[eid] = shared
        status = str(raw.get("status") or "").upper()
        if status not in {"FINAL", "CANCELLED", "POSTPONED", "SCHEDULED"}:
            _invalid("unrecognized event status")
        if status != "FINAL":
            if status == "SCHEDULED":
                scheduled_participants.setdefault(eid, set()).update(rids)
            continue
        end = _time(raw.get("event_end_utc"))
        if not g_start < end <= freeze:
            _invalid("completed game chronology or source freeze invalid")
        if _time(raw["source_available_at"]) < end:
            _invalid("final status available before completion")
        roles = raw.get("roles")
        if not isinstance(roles, Mapping):
            _invalid("historical roles required")
        if any(roles.get(pid) is not None and
               (not isinstance(roles.get(pid), str) or
                roles[pid] not in {"home", "away", "neutral"}) for pid in rids):
            _invalid("invalid historical role")
        payload = {**raw, "_start": g_start, "_end": end,
                   "_venue": _venue(raw.get("venue")), "_participants": rids}
        identity = json.dumps([sport, g_start.isoformat(), end.isoformat(), sorted(rids),
                               {k: roles.get(k) for k in sorted(rids)},
                               raw.get("venue"), raw.get("workloads")], sort_keys=True, default=str)
        if eid in by_id:
            if by_id[eid] != identity:
                raise ScheduleContextError("SCHEDULE_IDENTITY_CONFLICT", eid)
            continue  # identical multi-provider or schedule-mirror event, counted only once
        by_id[eid] = identity
        normalized.append(payload)
    # Only never-finalized scheduled records hold the frozen schedule context.
    unresolved_ids = set(scheduled_participants) - set(by_id)
    unresolved_participants = {pid for eid in unresolved_ids for pid in scheduled_participants[eid]}
    per = {pid: _profile(pid, normalized_target, normalized,
                         history_completeness.get(pid) is True and pid not in unresolved_participants)
           for pid in ids}
    missing = {pid: tuple(k for k in ("rest_hours", "back_to_back", "games_in_4_days_including_target",
                                     "last_trip_miles", "travel_miles_last_7d",
                                     "consecutive_road_events_including_target", "workload_7d") if data[k] is None)
               for pid, data in per.items()}
    differences: dict[str, float | None] = {}
    if len(ids) == 2:
        a, b = (per[pid] for pid in ids)
        for key, outkey in (("rest_hours", "rest_hours_first_minus_second"),
                            ("games_in_4_days_including_target", "density_4d_first_minus_second"),
                            ("last_trip_miles", "last_trip_miles_first_minus_second")):
            differences[outkey] = (round(a[key] - b[key], 2)
                                   if isinstance(a[key], (int, float)) and isinstance(b[key], (int, float)) else None)
        differences["workload_7d_first_minus_second"] = (
            round(a["workload_7d"] - b["workload_7d"], 2)
            if a["workload_unit"] and a["workload_unit"] == b["workload_unit"] and
            a["workload_7d"] is not None and b["workload_7d"] is not None else None)
    # Commit to the entire evidence input, not merely IDs and a caller-supplied hash.
    # Distinct travel/workload/role values must yield distinct evidence fingerprints.
    def audit_event(raw: Mapping[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in raw.items() if not str(k).startswith("_")}

    receipt = {"family": FEATURE_FAMILY_VERSION, "sport": sport, "target": audit_event(target),
               "freeze": freeze.isoformat(),
               # All source observations, including identical identity mirrors and
               # excluded fixtures, are preserved in an order-independent receipt.
               "histories": sorted((audit_event(r) for r in history),
                                    key=lambda r: json.dumps(r, sort_keys=True, default=str)),
               "completeness": {pid: history_completeness.get(pid) is True for pid in ids}}
    fingerprint = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                                          default=str).encode("utf-8")).hexdigest()
    return {"feature_family_version": FEATURE_FAMILY_VERSION, "sport": sport,
            "official_event_id": tid, "as_of_utc": freeze.isoformat(),
            "status": "SCHEDULE_CONTEXT_INCOMPLETE" if any(missing.values()) else "RESEARCH_CONTEXT_ONLY",
            "per_participant": per, "matchup_differences": differences,
            "unresolved_prior_event_ids": sorted(unresolved_ids),
            "missing_fields": missing, "evidence_sha256": fingerprint,
            "source_authenticity_independently_verified": False,
            "probabilities": None, "probability_publishable": False,
            "model_feature_consumption_verified": False,
            "terminal_authority": TERMINAL_AUTHORITY, "can_execute": False}
