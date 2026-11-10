"""Independent NHL starting-goalie confirmation contract (research evidence only).

The official Gamecenter boxscore capture is participation evidence. It does not
prove who starts in goal, so this module refuses it as a confirmation source.
A goalie becomes ``CONFIRMED`` only when at least two independent official
sources agree on the exact canonical event, both team sides and the starting
status inside the pregame window.

Nothing here scores a probability, certifies an artifact, publishes a rank or
grants execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from v17.nhl_event_identity import team_abbreviation

CAN_EXECUTE = False
MIN_INDEPENDENT_SOURCES = 2
MAX_CONFIRMATION_AGE_MINUTES = 90
MAX_IDENTITY_RECEIPT_AGE_HOURS = 24
_MAX_CLOCK_DRIFT_SECONDS = 60

# Providers that can issue a real starting-goalie designation. A public
# participation feed is deliberately absent.
OFFICIAL_CONFIRMATION_PROVIDERS = frozenset(
    {
        "NHL_PUBLIC_WEB_API",
        "NHL_OFFICIAL_CLUB_FEED",
        "NHL_OFFICIAL_LINEUP_FEED",
    }
)
CONFIRMING_SOURCE_KINDS = frozenset(
    {
        "STARTING_GOALIE_DESIGNATION",
        "STARTING_LINEUP",
        "OFFICIAL_ROSTER_DESIGNATION",
    }
)
# Gamecenter boxscore/participation rows, projections and last-start inference
# are evidence about a roster, never proof of the starter.
NON_CONFIRMING_SOURCE_KINDS = frozenset(
    {
        "GAMECENTER_BOXSCORE_PARTICIPATION",
        "PROJECTED_STARTER",
        "LAST_START_INFERENCE",
    }
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SCHEDULE_SOURCE = re.compile(
    r"https://api-web\.nhle\.com/v1/club-schedule-season/[A-Z]{3}/\d{8}\Z"
)


class NHLGoalieConfirmationError(ValueError):
    """Raised for programmer errors, never for missing sport evidence."""


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _held(
    code: str,
    blockers: Sequence[str] = (),
    *,
    event_id: str | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "status": "HELD",
        "code": code,
        "blockers": sorted({code, *(str(value) for value in blockers)}),
        "sport": "NHL",
        "market_scope": "FULL_GAME_WIN_INCLUDING_OT_SO",
        "official_event_id": event_id,
        "goalie_status": "UNVERIFIED",
        "goalie_status_observed_at": None,
        "goalie_identity_snapshot_id": None,
        "goalie_confirmation_status": "NOT_ESTABLISHED",
        "confirmation_sources": [],
        "independent_source_count": 0,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _identity_blockers(
    identity: Mapping[str, Any], *, event_id: str | None, now: datetime
) -> tuple[list[str], dict[str, Any]]:
    """Validate the server-owned canonical identity receipt for this event."""
    blockers: list[str] = []
    source = identity.get("identity_source")
    verified_at = _time(identity.get("identity_verified_at"))
    start = _time(identity.get("event_start_time"))
    home = identity.get("home_team")
    away = identity.get("away_team")
    home = team_abbreviation(home) if isinstance(home, str) else None
    away = team_abbreviation(away) if isinstance(away, str) else None
    if (
        identity.get("identity_provider") != "NHL_PUBLIC_WEB_API"
        or not isinstance(source, str)
        or _SCHEDULE_SOURCE.fullmatch(source) is None
        or not isinstance(event_id, str)
        or str(identity.get("event_id") or "") != event_id
        or home is None
        or away is None
        or home == away
        or start is None
        or verified_at is None
        or verified_at > now
        or (now - verified_at).total_seconds() > MAX_IDENTITY_RECEIPT_AGE_HOURS * 3600
        or identity.get("market_features_used") is not False
        or identity.get("prediction_authority") is not False
        or identity.get("can_execute") is not False
    ):
        blockers.append("NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH")
    if start is not None and start <= now:
        blockers.append("NHL_EVENT_NOT_PREGAME")
    return blockers, {
        "home_team": home,
        "away_team": away,
        "event_start_time": start,
    }


def _source_blockers(
    source: Mapping[str, Any],
    *,
    event_id: str | None,
    canonical: Mapping[str, Any],
    now: datetime,
) -> list[str]:
    """Return typed blockers for one claimed starter-confirmation receipt."""
    blockers: list[str] = []
    provider = source.get("provider")
    kind = str(source.get("source_kind") or "").strip().upper()
    snapshot = str(source.get("source_snapshot_id") or "").strip()
    payload_sha = str(source.get("source_payload_sha256") or "").strip().lower()
    observed = _time(source.get("observed_at"))
    start = canonical["event_start_time"]

    if kind in NON_CONFIRMING_SOURCE_KINDS:
        # Explicitly: a Gamecenter boxscore is participation, not starter proof.
        blockers.append("NHL_GAMECENTER_PARTICIPATION_NOT_CONFIRMATION")
    elif kind not in CONFIRMING_SOURCE_KINDS:
        blockers.append("NHL_GOALIE_CONFIRMATION_SOURCE_KIND_UNSUPPORTED")
    if provider not in OFFICIAL_CONFIRMATION_PROVIDERS:
        blockers.append("NHL_GOALIE_CONFIRMATION_PROVIDER_NOT_OFFICIAL")
    if not snapshot:
        blockers.append("NHL_GOALIE_CONFIRMATION_SNAPSHOT_MISSING")
    if _SHA256.fullmatch(payload_sha) is None:
        blockers.append("NHL_GOALIE_CONFIRMATION_PAYLOAD_HASH_INVALID")
    if (
        str(source.get("event_id") or "") != str(event_id or "")
        or _time(source.get("event_start_time_utc")) is None
        or start is None
        or abs(
            (_time(source.get("event_start_time_utc")) - start).total_seconds()
        )
        > _MAX_CLOCK_DRIFT_SECONDS
    ):
        blockers.append("NHL_GOALIE_CONFIRMATION_EVENT_MISMATCH")
    source_home = source.get("home_team")
    source_away = source.get("away_team")
    source_home = team_abbreviation(source_home) if isinstance(source_home, str) else None
    source_away = team_abbreviation(source_away) if isinstance(source_away, str) else None
    if (
        source_home is None
        or source_away is None
        or source_home != canonical["home_team"]
        or source_away != canonical["away_team"]
    ):
        blockers.append("NHL_GOALIE_CONFIRMATION_TEAM_MISMATCH")
    if observed is None:
        blockers.append("NHL_GOALIE_CONFIRMATION_TIME_MISSING")
    else:
        if observed > now:
            blockers.append("NHL_GOALIE_CONFIRMATION_TIME_IN_FUTURE")
        if start is not None and observed >= start:
            blockers.append("NHL_GOALIE_CONFIRMATION_AFTER_KICKOFF")
        if (now - observed).total_seconds() > MAX_CONFIRMATION_AGE_MINUTES * 60:
            blockers.append("NHL_GOALIE_CONFIRMATION_STALE")
    if str(source.get("starting_status") or "").strip().upper() != "CONFIRMED":
        blockers.append("NHL_STARTING_GOALIE_NOT_CONFIRMED")
    home_goalie = str(source.get("home_goalie_id") or "").strip()
    away_goalie = str(source.get("away_goalie_id") or "").strip()
    if not home_goalie or not away_goalie:
        blockers.append("NHL_GOALIE_IDENTITY_MISSING")
    elif home_goalie == away_goalie:
        blockers.append("NHL_GOALIE_IDENTITY_INVALID")
    return blockers


def confirm_nhl_starter_goalie(
    *,
    identity: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    as_of_utc: datetime,
) -> dict[str, Any]:
    """Confirm both starters from two independent official pregame sources.

    Returns a HELD receipt with typed blockers unless every source agrees. The
    returned ``goalie_status``/``goalie_status_observed_at``/
    ``goalie_identity_snapshot_id`` fields are the exact inputs consumed by
    ``v17.nhl_pregame_evidence_boundary``; populating them is evidence
    completeness, never certification.
    """
    if as_of_utc.tzinfo is None:
        raise NHLGoalieConfirmationError("as_of_utc must be timezone-aware")
    now = as_of_utc.astimezone(timezone.utc)
    if not isinstance(identity, Mapping):
        return _held("NHL_CANONICAL_IDENTITY_RECEIPT_MISSING")
    event_id = str(identity.get("event_id") or "").strip() or None
    identity_blockers, canonical = _identity_blockers(
        identity, event_id=event_id, now=now
    )
    if identity_blockers:
        return _held(
            "NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH",
            identity_blockers,
            event_id=event_id,
        )

    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
        return _held(
            "NHL_GOALIE_INDEPENDENT_CONFIRMATION_INSUFFICIENT", event_id=event_id
        )
    usable = [row for row in sources if isinstance(row, Mapping)]
    if len(usable) < MIN_INDEPENDENT_SOURCES:
        return _held(
            "NHL_GOALIE_INDEPENDENT_CONFIRMATION_INSUFFICIENT", event_id=event_id
        )

    blockers: list[str] = []
    evidence: list[dict[str, Any]] = []
    for source in usable:
        found = _source_blockers(
            source, event_id=event_id, canonical=canonical, now=now
        )
        blockers.extend(found)
        evidence.append(
            {
                "provider": source.get("provider"),
                "source_kind": source.get("source_kind"),
                "source_snapshot_id": source.get("source_snapshot_id"),
                "source_payload_sha256": str(
                    source.get("source_payload_sha256") or ""
                ).lower(),
                "observed_at": source.get("observed_at"),
            }
        )
    if blockers:
        return _held(
            "NHL_GOALIE_CONFIRMATION_EVIDENCE_INCOMPLETE",
            blockers,
            event_id=event_id,
        )

    providers = {str(row["provider"]) for row in usable}
    receipts = {
        (str(row["source_snapshot_id"]), str(row["source_payload_sha256"]).lower())
        for row in evidence
    }
    if len(providers) < MIN_INDEPENDENT_SOURCES or len(receipts) < MIN_INDEPENDENT_SOURCES:
        return _held(
            "NHL_GOALIE_SOURCES_NOT_INDEPENDENT",
            ["NHL_GOALIE_SOURCES_NOT_INDEPENDENT"],
            event_id=event_id,
        )

    home_ids = {str(row.get("home_goalie_id") or "").strip() for row in usable}
    away_ids = {str(row.get("away_goalie_id") or "").strip() for row in usable}
    if len(home_ids) != 1 or len(away_ids) != 1:
        return _held(
            "NHL_STARTING_GOALIE_CONFLICT",
            ["NHL_STARTING_GOALIE_CONFLICT"],
            event_id=event_id,
        )

    # The agreement is only as fresh as its oldest source: the gate's freshness
    # window must cover every source, not just the most recently observed one.
    observed = min(_time(row.get("observed_at")) for row in usable)  # type: ignore[type-var]
    binding = {
        "official_event_id": event_id,
        "event_start_time_utc": canonical["event_start_time"].isoformat(),
        "home_team": canonical["home_team"],
        "away_team": canonical["away_team"],
        "home_goalie_id": next(iter(home_ids)),
        "away_goalie_id": next(iter(away_ids)),
        "sources": sorted(
            (
                {
                    "provider": row["provider"],
                    "source_kind": row["source_kind"],
                    "source_snapshot_id": row["source_snapshot_id"],
                    "source_payload_sha256": row["source_payload_sha256"],
                }
                for row in evidence
            ),
            key=lambda row: (str(row["provider"]), str(row["source_snapshot_id"])),
        ),
    }
    snapshot_id = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "STARTER_GOALIE_CONFIRMED_RESEARCH_ONLY",
        "code": "NHL_STARTER_GOALIE_INDEPENDENTLY_CONFIRMED",
        "blockers": [],
        "sport": "NHL",
        "market_scope": "FULL_GAME_WIN_INCLUDING_OT_SO",
        "official_event_id": event_id,
        "home_team": canonical["home_team"],
        "away_team": canonical["away_team"],
        "home_goalie_id": binding["home_goalie_id"],
        "away_goalie_id": binding["away_goalie_id"],
        "goalie_status": "CONFIRMED",
        "goalie_status_observed_at": observed.isoformat(),
        "goalie_identity_snapshot_id": snapshot_id,
        "goalie_confirmation_status": "INDEPENDENT_SOURCES_AGREE",
        "confirmation_binding_sha256": snapshot_id,
        "confirmation_sources": evidence,
        "independent_source_count": len(providers),
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CONFIRMING_SOURCE_KINDS",
    "MAX_CONFIRMATION_AGE_MINUTES",
    "MIN_INDEPENDENT_SOURCES",
    "NHLGoalieConfirmationError",
    "NON_CONFIRMING_SOURCE_KINDS",
    "OFFICIAL_CONFIRMATION_PROVIDERS",
    "confirm_nhl_starter_goalie",
]
