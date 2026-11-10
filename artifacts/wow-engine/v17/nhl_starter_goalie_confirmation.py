"""Source-side NHL starting-goalie confirmation contract (pure, offline, no scorer).

Two server-collected receipts from genuinely independent OFFICIAL providers must
agree on event identity, scheduled start and both starting goalies before a
`goalie_status=CONFIRMED` evidence boundary may be emitted. A caller-supplied
status string is never trusted: the status and the identity snapshot id are
derived here. Projected starters, Gamecenter participation, single-provider
evidence, stale evidence, conflicts and malformed receipts all HOLD with a typed
code. This module never scores, never persists, never fetches, and never
publishes a probability.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping

from v17.nhl_candidate_preflight import (
    CONFIRMED_STARTING_STATUSES,
    NHLCandidatePreflightError,
    _aware_snapshot_time,
    _is_sha256,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
START_TIME_TOLERANCE = timedelta(seconds=60)
VERIFICATION_WINDOW = timedelta(minutes=90)
MAX_EVIDENCE_AGE = timedelta(hours=24)
OFFICIAL_AUTHORITY = "OFFICIAL"
REQUIRED_RECEIPTS = 2


def _text(value: Any) -> str:
    return str(value or "").strip()


def _hold(code: str, detail: str = "") -> NHLCandidatePreflightError:
    return NHLCandidatePreflightError(code, detail or code)


def _validate_receipt(receipt: Any, label: str) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise _hold("NHL_STARTER_RECEIPTS_INCOMPLETE", label)
    if _text(receipt.get("source_authority")).upper() != OFFICIAL_AUTHORITY:
        raise _hold("NHL_STARTER_SOURCE_NOT_OFFICIAL", label)
    for key in ("source_id", "provider_family", "source_snapshot_id", "official_event_id",
                "home_team_id", "away_team_id"):
        if not _text(receipt.get(key)):
            raise _hold("NHL_STARTER_RECEIPTS_INCOMPLETE", f"{label}:{key}")
    if not _is_sha256(receipt.get("source_payload_sha256")):
        raise _hold("NHL_STARTER_RECEIPTS_INCOMPLETE", f"{label}:source_payload_sha256")
    start = _aware_snapshot_time(receipt.get("event_start_time_utc"), "NHL_STARTER_RECEIPTS_INCOMPLETE")
    as_of = _aware_snapshot_time(receipt.get("as_of"), "NHL_STARTER_RECEIPTS_INCOMPLETE")
    goalies: dict[str, dict[str, str]] = {}
    for side in ("home", "away"):
        goalie = receipt.get(side)
        if not isinstance(goalie, Mapping) or not _text(goalie.get("goalie_id")):
            raise _hold("NHL_STARTER_GOALIE_MISSING", f"{label}:{side}")
        goalies[side] = {
            "goalie_id": _text(goalie.get("goalie_id")),
            "starting_status": _text(goalie.get("starting_status")).upper(),
        }
    return {"receipt": receipt, "start": start, "as_of": as_of, "goalies": goalies}


def confirm_starting_goalies(receipts: Any, *, now: datetime) -> dict[str, Any]:
    """Return a derived CONFIRMED boundary or raise a typed hold."""
    if now.utcoffset() is None:
        raise _hold("NHL_STARTER_RECEIPTS_INCOMPLETE", "now must be timezone-aware")
    if not isinstance(receipts, (list, tuple)) or len(receipts) != REQUIRED_RECEIPTS:
        raise _hold("NHL_STARTER_RECEIPTS_INCOMPLETE", "exactly two provider receipts required")
    a, b = (_validate_receipt(r, f"receipt_{i}") for i, r in enumerate(receipts))
    ra, rb = a["receipt"], b["receipt"]

    # Independence: distinct provider, provider family, snapshot and content.
    if (_text(ra["source_id"]) == _text(rb["source_id"])
            or _text(ra["provider_family"]).upper() == _text(rb["provider_family"]).upper()
            or _text(ra["source_snapshot_id"]) == _text(rb["source_snapshot_id"])
            or str(ra["source_payload_sha256"]).lower() == str(rb["source_payload_sha256"]).lower()):
        raise _hold("NHL_STARTER_PROVIDERS_NOT_INDEPENDENT")

    # Exact event identity.
    for key in ("official_event_id", "home_team_id", "away_team_id"):
        if _text(ra[key]) != _text(rb[key]):
            raise _hold("NHL_STARTER_EVENT_IDENTITY_MISMATCH", key)
    if abs(a["start"] - b["start"]) > START_TIME_TOLERANCE:
        raise _hold("NHL_STARTER_START_TIME_DISAGREEMENT")
    start = min(a["start"], b["start"])

    # Pregame window and freshness: evidence is before start, within 90 minutes of it,
    # and no older than 24 hours at evaluation time.
    for item in (a, b):
        if item["as_of"] >= start or start - item["as_of"] > VERIFICATION_WINDOW:
            raise _hold("NHL_STARTER_VERIFICATION_WINDOW")
        if now - item["as_of"] > MAX_EVIDENCE_AGE or item["as_of"] > now:
            raise _hold("NHL_STARTER_EVIDENCE_STALE")
    if now >= start:
        raise _hold("NHL_STARTER_VERIFICATION_WINDOW", "evaluation at or after scheduled start")

    # Both providers must call both goalies confirmed starters, and agree on identity.
    for side in ("home", "away"):
        for item in (a, b):
            if item["goalies"][side]["starting_status"] not in CONFIRMED_STARTING_STATUSES:
                raise _hold("NHL_STARTING_GOALIE_NOT_CONFIRMED", side)
        if a["goalies"][side]["goalie_id"] != b["goalies"][side]["goalie_id"]:
            raise _hold("NHL_STARTER_GOALIE_CONFLICT", side)

    commitment = {
        "official_event_id": _text(ra["official_event_id"]),
        "home_goalie_id": a["goalies"]["home"]["goalie_id"],
        "away_goalie_id": a["goalies"]["away"]["goalie_id"],
        "receipts": sorted(
            [{"source_id": _text(r["source_id"]), "source_snapshot_id": _text(r["source_snapshot_id"]),
              "source_payload_sha256": str(r["source_payload_sha256"]).lower()} for r in (ra, rb)],
            key=lambda r: r["source_id"],
        ),
    }
    digest = hashlib.sha256(json.dumps(commitment, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "goalie_status": "CONFIRMED",
        "goalie_identity_snapshot_id": "sha256:" + digest,
        "official_event_id": commitment["official_event_id"],
        "home_goalie_id": commitment["home_goalie_id"],
        "away_goalie_id": commitment["away_goalie_id"],
        "verified_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "provider_receipts": commitment["receipts"],
        "derived_server_side": True,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
    }


__all__ = ["CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "confirm_starting_goalies"]
