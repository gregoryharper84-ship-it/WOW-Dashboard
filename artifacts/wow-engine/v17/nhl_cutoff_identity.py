"""NHL official pre-scoring identity revalidation, read-only and fail closed.

The discovery/odds-provider event is NOT sufficient for a pregame fitted score.
This rechecks the official game *at the scoring boundary* after expensive
offline source/model validation, before producing any numerical probability.
No scheduling, source write, model ownership, odds or wager execution here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable, Mapping

from v17.nhl_event_identity import (
    NHLEventIdentityError,
    resolve_nhl_current_event_identity,
    team_abbreviation,
)

CAN_EXECUTE = False


class NHLCutoffIdentityError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _dt(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def verify_nhl_identity_at_scoring(
    *,
    official_event_id: str,
    home_team: str,
    away_team: str,
    event_start_time: str,
    as_of_utc: datetime,
    resolver: Callable[..., Mapping[str, Any]] = resolve_nhl_current_event_identity,
) -> dict[str, Any]:
    """Require a fresh official FUT/PRE exact-ID/team/start match.

    This is an **in-process boundary check**, not a persisted raw-source
    snapshot, and not proof that a model used a feature numerically.
    """
    if as_of_utc.tzinfo is None:
        raise NHLCutoffIdentityError("NHL_CUTOFF_CLOCK_INVALID")
    now = as_of_utc.astimezone(timezone.utc)
    start = _dt(event_start_time)
    home, away = team_abbreviation(home_team), team_abbreviation(away_team)
    if (
        not isinstance(official_event_id, str)
        or len(official_event_id) != 10
        or not official_event_id.isascii()
        or not official_event_id.isdecimal()
        or start is None or start <= now or not home or not away or home == away
    ):
        raise NHLCutoffIdentityError("NHL_CUTOFF_EVENT_INPUT_INVALID")
    try:
        resolution = resolver(
            event_start_time=start.isoformat(),
            home_team=home,
            away_team=away,
        )
    except NHLEventIdentityError as exc:
        # Source failures must not be flattened to MODEL_UNAVAILABLE.
        raise NHLCutoffIdentityError(exc.code) from exc
    except Exception as exc:
        raise NHLCutoffIdentityError("NHL_CUTOFF_OFFICIAL_SOURCE_UNOBTAINABLE") from exc

    actual_start = _dt(resolution.get("event_start_time"))
    if str(resolution.get("event_id") or "") != official_event_id:
        raise NHLCutoffIdentityError("NHL_CUTOFF_OFFICIAL_EVENT_ID_CHANGED")
    if resolution.get("home_team") != home or resolution.get("away_team") != away:
        raise NHLCutoffIdentityError("NHL_CUTOFF_PARTICIPANTS_CHANGED")
    if actual_start is None or actual_start != start:
        # 30m applies when initially matching disparate provider clocks; at
        # final scoring we already have an official start, so ANY move holds.
        raise NHLCutoffIdentityError("NHL_CUTOFF_OFFICIAL_START_CHANGED")
    if actual_start <= now or resolution.get("game_state") not in {"FUT", "PRE"}:
        raise NHLCutoffIdentityError("NHL_EVENT_NOT_PREGAME")
    if resolution.get("game_type") != 2:
        raise NHLCutoffIdentityError("NHL_CUTOFF_GAME_TYPE_UNVERIFIED")
    source_url = resolution.get("identity_source")
    if (
        resolution.get("identity_provider") != "NHL_PUBLIC_WEB_API"
        or not isinstance(source_url, str)
        or re.fullmatch(
            r"https://api-web\.nhle\.com/v1/club-schedule-season/[A-Z]{3}/[0-9]{8}",
            source_url, flags=re.ASCII
        ) is None
        or resolution.get("market_features_used") is not False
        or resolution.get("prediction_authority") is not False
        or resolution.get("can_execute") is not False
    ):
        raise NHLCutoffIdentityError("NHL_CUTOFF_OFFICIAL_PROVENANCE_INVALID")

    # Never claim the mere existence of a 64-hex string authenticates a
    # fixture. Exact current source matching above is the validity check.
    match_sha = resolution.get("identity_match_sha256")
    if (
        not isinstance(match_sha, str) or len(match_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in match_sha)
    ):
        raise NHLCutoffIdentityError("NHL_CUTOFF_IDENTITY_DIGEST_MISSING")
    verified_at = _dt(resolution.get("identity_verified_at"))
    if verified_at is None or abs((verified_at - now).total_seconds()) > 300:
        raise NHLCutoffIdentityError("NHL_CUTOFF_VERIFICATION_TIME_INVALID")
    return {
        "code": "NHL_CUTOFF_IDENTITY_VERIFIED_RESEARCH_ONLY",
        "official_event_id": official_event_id,
        "home_team": home,
        "away_team": away,
        "official_start_time": actual_start.isoformat(),
        "identity_provider": "NHL_PUBLIC_WEB_API",
        "identity_source": resolution.get("identity_source"),
        "identity_match_sha256": match_sha,
        "identity_verified_at": verified_at.isoformat(),
        "source_snapshot_id": None,
        "source_persistence_status": "NOT_PERSISTED",
        "fitted_model_verified": False,
        "probability_publishable": False,
        "can_execute": False,
    }
