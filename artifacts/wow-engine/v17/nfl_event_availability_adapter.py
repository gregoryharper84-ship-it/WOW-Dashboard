"""V17 event-specific NFL player-availability adapter (shadow mode).

Implements governed, multi-source game-specific NFL participation evidence
following the authorized V17 reporting framework:

Permitted source interpretations:
- Complete, authenticated, pregame official report:
    Eligible for governed availability evaluation (listed designations applied;
    unlisted players on complete reports cleared as FRESH_OFFICIAL_REPORT_UNLISTED).
- Official game-day inactive report:
    Evidence of players ruled inactive (designation INACTIVE -> PLAYER_RULED_OUT);
    does not grant clearance to unlisted players.
- Dated ESPN per-game injury entry:
    Supporting evidence of a stated designation; does NOT prove complete clearance
    for unlisted players.
- Undated ESPN athlete profile:
    Not sufficient to establish current-game availability (fails closed).
- nflverse injury archive:
    Historical/retrospective evidence; not live participation clearance.

Mandatory fail-closed invariant:
A player missing from an incomplete, unauthenticated, or supporting-only report
must remain PLAYER_AVAILABILITY_UNVERIFIED.
CAN_EXECUTE = False; PROBABILITY_PUBLISHABLE = False.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence
import unicodedata

CAN_EXECUTE: bool = False
PROBABILITY_PUBLISHABLE: bool = False
ADAPTER_MODE: str = "SHADOW_ONLY"

# Canonical source IDs
SOURCE_NFL_OFFICIAL_GAME_STATUS = "NFL_OFFICIAL_GAME_STATUS_REPORT"
SOURCE_NFL_OFFICIAL_INACTIVES = "NFL_OFFICIAL_GAMEDAY_INACTIVES"
SOURCE_ESPN_EVENT_INJURIES = "ESPN_NFL_EVENT_SUMMARY_INJURIES"
SOURCE_ESPN_ATHLETE_PROFILE = "ESPN_NFL_ATHLETE_PROFILE"
SOURCE_NFLVERSE_ARCHIVE = "NFLVERSE_INJURIES_ARCHIVE"

# Policy Note: Production freshness thresholds must be formally governed.
# The default shadow baseline uses 2 hours (matching WNBA official report standards).
# Configurable via max_report_age_seconds parameters; no unreviewed production policy assumed.
DEFAULT_MAX_OFFICIAL_REPORT_AGE_SECONDS = 2 * 3600
DEFAULT_MAX_GAMEDAY_INACTIVES_AGE_SECONDS = 2 * 3600

# Canonical designations
DESIGNATION_OUT = "OUT"
DESIGNATION_DOUBTFUL = "DOUBTFUL"
DESIGNATION_QUESTIONABLE = "QUESTIONABLE"
DESIGNATION_INACTIVE = "INACTIVE"
DESIGNATION_IR = "IR"
DESIGNATION_AVAILABLE = "AVAILABLE"

UNAVAILABLE_SET = frozenset(
    {
        "OUT",
        "INACTIVE",
        "IR",
        "INJURED_RESERVE",
        "RESERVE_INJURED",
        "PUP",
        "DID_NOT_PARTICIPATE",
        "DNP",
        "SUSPENDED",
    }
)

PLAYABLE_RISK_SET = frozenset({"QUESTIONABLE", "DOUBTFUL", "GAME_TIME_DECISION"})


class NFLEventAvailabilityError(RuntimeError):
    """Raised when an NFL availability contract is violated."""

    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _normalize_token(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().upper()
    return " ".join(text.split())


def normalize_nfl_team_code(raw: Any) -> str:
    token = _normalize_token(raw)
    aliases: dict[str, str] = {
        "ARIZONA CARDINALS": "ARI", "CARDINALS": "ARI", "ARI": "ARI",
        "ATLANTA FALCONS": "ATL", "FALCONS": "ATL", "ATL": "ATL",
        "BALTIMORE RAVENS": "BAL", "RAVENS": "BAL", "BAL": "BAL",
        "BUFFALO BILLS": "BUF", "BILLS": "BUF", "BUF": "BUF",
        "CAROLINA PANTHERS": "CAR", "PANTHERS": "CAR", "CAR": "CAR",
        "CHICAGO BEARS": "CHI", "BEARS": "CHI", "CHI": "CHI",
        "CINCINNATI BENGALS": "CIN", "BENGALS": "CIN", "CIN": "CIN",
        "CLEVELAND BROWNS": "CLE", "BROWNS": "CLE", "CLE": "CLE",
        "DALLAS COWBOYS": "DAL", "COWBOYS": "DAL", "DAL": "DAL",
        "DENVER BRONCOS": "DEN", "BRONCOS": "DEN", "DEN": "DEN",
        "DETROIT LIONS": "DET", "LIONS": "DET", "DET": "DET",
        "GREEN BAY PACKERS": "GB", "PACKERS": "GB", "GB": "GB",
        "HOUSTON TEXANS": "HOU", "TEXANS": "HOU", "HOU": "HOU",
        "INDIANAPOLIS COLTS": "IND", "COLTS": "IND", "IND": "IND",
        "JACKSONVILLE JAGUARS": "JAX", "JAGUARS": "JAX", "JAX": "JAX", "JAC": "JAX",
        "KANSAS CITY CHIEFS": "KC", "CHIEFS": "KC", "KC": "KC",
        "LAS VEGAS RAIDERS": "LV", "RAIDERS": "LV", "LV": "LV", "LVR": "LV",
        "LOS ANGELES CHARGERS": "LAC", "CHARGERS": "LAC", "LAC": "LAC",
        "LOS ANGELES RAMS": "LAR", "RAMS": "LAR", "LAR": "LAR", "LA": "LAR",
        "MIAMI DOLPHINS": "MIA", "DOLPHINS": "MIA", "MIA": "MIA",
        "MINNESOTA VIKINGS": "MIN", "VIKINGS": "MIN", "MIN": "MIN",
        "NEW ENGLAND PATRIOTS": "NE", "PATRIOTS": "NE", "NE": "NE",
        "NEW ORLEANS SAINTS": "NO", "SAINTS": "NO", "NO": "NO",
        "NEW YORK GIANTS": "NYG", "GIANTS": "NYG", "NYG": "NYG",
        "NEW YORK JETS": "NYJ", "JETS": "NYJ", "NYJ": "NYJ",
        "PHILADELPHIA EAGLES": "PHI", "EAGLES": "PHI", "PHI": "PHI",
        "PITTSBURGH STEELERS": "PIT", "STEELERS": "PIT", "PIT": "PIT",
        "SAN FRANCISCO 49ERS": "SF", "49ERS": "SF", "SF": "SF",
        "SEATTLE SEAHAWKS": "SEA", "SEAHAWKS": "SEA", "SEA": "SEA",
        "TAMPA BAY BUCCANEERS": "TB", "BUCCANEERS": "TB", "TB": "TB",
        "TENNESSEE TITANS": "TEN", "TITANS": "TEN", "TEN": "TEN",
        "WASHINGTON COMMANDERS": "WAS", "COMMANDERS": "WAS", "WAS": "WAS", "WSH": "WAS",
    }
    return aliases.get(token, token)


def normalize_nfl_designation(raw: Any) -> str | None:
    token = _normalize_token(raw)
    if not token:
        return None
    if token in ("OUT", "O", "RULED OUT"):
        return DESIGNATION_OUT
    if token in ("INACTIVE", "INACTIVES", "DECLARED INACTIVE"):
        return DESIGNATION_INACTIVE
    if token in ("IR", "INJURED RESERVE", "RESERVE INJURED"):
        return DESIGNATION_IR
    if token in ("DOUBTFUL", "D"):
        return DESIGNATION_DOUBTFUL
    if token in ("QUESTIONABLE", "Q", "GTD", "GAME TIME DECISION"):
        return DESIGNATION_QUESTIONABLE
    if token in ("AVAILABLE", "ACTIVE", "CLEARED", "FULL PARTICIPATION", "PROBABLE", "P"):
        return DESIGNATION_AVAILABLE
    if token in ("DNP", "DID NOT PARTICIPATE"):
        return "DID_NOT_PARTICIPATE"
    return token


def _parse_iso_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        clean = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


@dataclass(frozen=True)
class NFLPlayerAvailabilityEvidence:
    """Standardized NFL player availability evidence container."""

    athlete_id: str
    athlete_name: str
    team: str
    event_id: str
    source: str
    captured_at: str
    evidence_status: str
    designation: str | None
    basis: str | None
    fresh: bool
    report_complete: bool
    risk: bool
    is_inactive: bool = False
    supporting_sources: tuple[dict[str, Any], ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_availability_dict(self) -> dict[str, Any]:
        """Convert to the availability sub-dictionary."""
        return {
            "source": self.source,
            "captured_at": self.captured_at,
            "evidence_status": self.evidence_status,
            "designation": self.designation,
            "basis": self.basis,
            "fresh": self.fresh,
            "report_complete": self.report_complete,
            "athlete_id": self.athlete_id,
            "athlete_name": self.athlete_name,
            "event_id": self.event_id,
            "team": self.team,
            "is_inactive": self.is_inactive,
            "supporting_sources": list(self.supporting_sources),
            "provenance": dict(self.provenance),
        }

    def to_role_availability(self) -> dict[str, Any]:
        """Convert to the role_status structure expected by evaluate_player_availability."""
        return {
            "status": "ACTIVE_CURRENT_ROSTER",
            "availability": self.to_availability_dict(),
        }


def parse_nfl_official_game_status_report(
    report_data: Mapping[str, Any],
    *,
    athlete_id: str,
    athlete_name: str,
    team: str,
    event_id: str,
    event_start: datetime,
    captured: datetime,
    max_report_age_seconds: int = DEFAULT_MAX_OFFICIAL_REPORT_AGE_SECONDS,
) -> NFLPlayerAvailabilityEvidence:
    """Parse authenticated complete pregame official NFL game-status report."""
    is_auth = bool(report_data.get("authenticated", False))
    is_complete = bool(report_data.get("report_complete", False))
    report_ts = _parse_iso_ts(report_data.get("report_timestamp"))

    fresh = False
    if report_ts is not None:
        age_seconds = (captured - report_ts).total_seconds()
        # Fresh if captured after report publication and not older than max age
        if -300 <= age_seconds <= max_report_age_seconds:
            fresh = True

    # Look for the athlete in the team's listed entries
    team_norm = normalize_nfl_team_code(team)
    listed_entries = report_data.get("injuries") or report_data.get("entries") or []
    target_entry = None
    target_name_norm = _normalize_token(athlete_name)

    for entry in listed_entries:
        if not isinstance(entry, Mapping):
            continue
        entry_team = normalize_nfl_team_code(entry.get("team"))
        if entry_team and entry_team != team_norm:
            continue
        entry_id = str(entry.get("athlete_id") or "").strip()
        entry_name = _normalize_token(entry.get("athlete_name") or entry.get("player_name"))
        if (entry_id and entry_id == str(athlete_id)) or (entry_name and entry_name == target_name_norm):
            target_entry = entry
            break

    if target_entry is not None:
        raw_desig = target_entry.get("game_status") or target_entry.get("designation") or target_entry.get("status")
        desig = normalize_nfl_designation(raw_desig)
        risk = desig in PLAYABLE_RISK_SET
        return NFLPlayerAvailabilityEvidence(
            athlete_id=str(athlete_id),
            athlete_name=athlete_name,
            team=team,
            event_id=str(event_id),
            source=SOURCE_NFL_OFFICIAL_GAME_STATUS,
            captured_at=captured.isoformat(),
            evidence_status="RETRIEVED",
            designation=desig,
            basis=None,
            fresh=fresh,
            report_complete=is_auth and is_complete,
            risk=risk,
            provenance={
                "report_timestamp": report_ts.isoformat() if report_ts else None,
                "authenticated": is_auth,
                "listed": True,
            },
        )

    # Athlete is NOT listed in the report
    if is_auth and is_complete and fresh:
        return NFLPlayerAvailabilityEvidence(
            athlete_id=str(athlete_id),
            athlete_name=athlete_name,
            team=team,
            event_id=str(event_id),
            source=SOURCE_NFL_OFFICIAL_GAME_STATUS,
            captured_at=captured.isoformat(),
            evidence_status="RETRIEVED",
            designation=None,
            basis="FRESH_OFFICIAL_REPORT_UNLISTED",
            fresh=True,
            report_complete=True,
            risk=False,
            provenance={
                "report_timestamp": report_ts.isoformat() if report_ts else None,
                "authenticated": True,
                "listed": False,
                "basis": "FRESH_OFFICIAL_REPORT_UNLISTED",
            },
        )

    # Unlisted but report is not authenticated or not complete or stale -> fail closed
    reason = "AVAILABILITY_REPORT_INCOMPLETE" if not (is_auth and is_complete) else "AVAILABILITY_EVIDENCE_STALE"
    return NFLPlayerAvailabilityEvidence(
        athlete_id=str(athlete_id),
        athlete_name=athlete_name,
        team=team,
        event_id=str(event_id),
        source=SOURCE_NFL_OFFICIAL_GAME_STATUS,
        captured_at=captured.isoformat(),
        evidence_status="REPORT_INCOMPLETE_OR_UNVERIFIED",
        designation=None,
        basis=None,
        fresh=fresh,
        report_complete=False,
        risk=False,
        provenance={"reason": reason, "authenticated": is_auth, "report_complete": is_complete},
    )


def parse_nfl_gameday_inactives(
    inactives_data: Mapping[str, Any],
    *,
    athlete_id: str,
    athlete_name: str,
    team: str,
    event_id: str,
    captured: datetime,
    max_report_age_seconds: int = DEFAULT_MAX_GAMEDAY_INACTIVES_AGE_SECONDS,
) -> NFLPlayerAvailabilityEvidence:
    """Parse official game-day inactive report (~90m before kickoff).

    Notice: Inactives only identify players ruled out. An inactive report
    never proves complete clearance for players not listed on it.
    """
    is_auth = bool(inactives_data.get("authenticated", False))
    report_ts = _parse_iso_ts(inactives_data.get("report_timestamp"))
    fresh = False
    if report_ts is not None:
        age_seconds = (captured - report_ts).total_seconds()
        if -300 <= age_seconds <= max_report_age_seconds:
            fresh = True

    inactives_list = inactives_data.get("inactives") or []
    team_norm = normalize_nfl_team_code(team)
    target_name_norm = _normalize_token(athlete_name)
    is_inactive = False

    for item in inactives_list:
        if isinstance(item, Mapping):
            item_team = normalize_nfl_team_code(item.get("team"))
            if item_team and item_team != team_norm:
                continue
            item_id = str(item.get("athlete_id") or "").strip()
            item_name = _normalize_token(item.get("athlete_name") or item.get("player_name"))
            if (item_id and item_id == str(athlete_id)) or (item_name and item_name == target_name_norm):
                is_inactive = True
                break
        elif isinstance(item, str):
            if _normalize_token(item) == target_name_norm:
                is_inactive = True
                break

    if is_inactive:
        return NFLPlayerAvailabilityEvidence(
            athlete_id=str(athlete_id),
            athlete_name=athlete_name,
            team=team,
            event_id=str(event_id),
            source=SOURCE_NFL_OFFICIAL_INACTIVES,
            captured_at=captured.isoformat(),
            evidence_status="RETRIEVED",
            designation=DESIGNATION_INACTIVE,
            basis=None,
            fresh=fresh,
            report_complete=is_auth,
            risk=False,
            is_inactive=True,
            provenance={"listed_inactive": True, "report_timestamp": report_ts.isoformat() if report_ts else None},
        )

    # Missing from inactives list alone is NOT clearance
    return NFLPlayerAvailabilityEvidence(
        athlete_id=str(athlete_id),
        athlete_name=athlete_name,
        team=team,
        event_id=str(event_id),
        source=SOURCE_NFL_OFFICIAL_INACTIVES,
        captured_at=captured.isoformat(),
        evidence_status="INACTIVES_ONLY_CLEARANCE_UNPROVEN",
        designation=None,
        basis=None,
        fresh=fresh,
        report_complete=False,
        risk=False,
        is_inactive=False,
        provenance={"listed_inactive": False, "note": "unlisted_on_inactives_does_not_clear"},
    )


def parse_espn_event_summary_injuries(
    event_summary: Mapping[str, Any],
    *,
    athlete_id: str,
    athlete_name: str,
    team: str,
    event_id: str,
    captured: datetime,
) -> NFLPlayerAvailabilityEvidence:
    """Parse dated ESPN per-game injury entries as supporting evidence.

    Stated designations are recorded. Absence from this feed does NOT grant
    clearance because the feed is not certified complete.
    """
    injuries_groups = event_summary.get("injuries") or []
    team_norm = normalize_nfl_team_code(team)
    target_name_norm = _normalize_token(athlete_name)
    found_entry: Mapping[str, Any] | None = None

    for grp in injuries_groups:
        if not isinstance(grp, Mapping):
            continue
        grp_team = normalize_nfl_team_code(grp.get("team", {}).get("abbreviation") or grp.get("team", {}).get("displayName"))
        if grp_team and grp_team != team_norm:
            continue
        for inj in grp.get("injuries") or []:
            if not isinstance(inj, Mapping):
                continue
            ath = inj.get("athlete") or {}
            ath_id = str(ath.get("id") or "").strip()
            ath_name = _normalize_token(ath.get("displayName") or ath.get("name"))
            if (ath_id and ath_id == str(athlete_id)) or (ath_name and ath_name == target_name_norm):
                found_entry = inj
                break
        if found_entry is not None:
            break

    if found_entry is not None:
        raw_status = found_entry.get("status")
        desig = normalize_nfl_designation(raw_status)
        entry_date = _parse_iso_ts(found_entry.get("date"))
        fresh = entry_date is not None
        risk = desig in PLAYABLE_RISK_SET
        return NFLPlayerAvailabilityEvidence(
            athlete_id=str(athlete_id),
            athlete_name=athlete_name,
            team=team,
            event_id=str(event_id),
            source=SOURCE_ESPN_EVENT_INJURIES,
            captured_at=captured.isoformat(),
            evidence_status="RETRIEVED",
            designation=desig,
            basis=None,
            fresh=fresh,
            report_complete=False,  # Supporting evidence only, not certified complete report
            risk=risk,
            provenance={"entry_date": entry_date.isoformat() if entry_date else None, "raw_status": raw_status},
        )

    # Not listed in ESPN per-game injury feed -> does not prove clearance
    return NFLPlayerAvailabilityEvidence(
        athlete_id=str(athlete_id),
        athlete_name=athlete_name,
        team=team,
        event_id=str(event_id),
        source=SOURCE_ESPN_EVENT_INJURIES,
        captured_at=captured.isoformat(),
        evidence_status="SUPPORTING_FEED_CLEARANCE_NOT_PROVEN",
        designation=None,
        basis=None,
        fresh=False,
        report_complete=False,
        risk=False,
        provenance={"listed": False},
    )


def compose_nfl_event_availability(
    *,
    athlete_id: str,
    athlete_name: str,
    team: str,
    event_id: str,
    event_start: datetime,
    captured: datetime,
    official_game_status: Mapping[str, Any] | None = None,
    gameday_inactives: Mapping[str, Any] | None = None,
    espn_event_summary: Mapping[str, Any] | None = None,
    espn_athlete_profile: Mapping[str, Any] | None = None,
    nflverse_row: Mapping[str, Any] | None = None,
    max_official_report_age_seconds: int = DEFAULT_MAX_OFFICIAL_REPORT_AGE_SECONDS,
    max_gameday_inactives_age_seconds: int = DEFAULT_MAX_GAMEDAY_INACTIVES_AGE_SECONDS,
) -> NFLPlayerAvailabilityEvidence:
    """Compose game-specific availability following the governed interpretation order.

    1. Gameday inactive report: if listed inactive -> ruled out.
    2. Complete official pregame report: authoritative for stated designation or unlisted clearance.
    3. Supporting dated ESPN per-game injury entries: records stated designations, never clears unlisted.
    4. Undated profile / nflverse archive: rejected as insufficient for current live clearance.
    """
    supporting: list[dict[str, Any]] = []

    # Check 1: Inactives
    if gameday_inactives is not None:
        inact = parse_nfl_gameday_inactives(
            gameday_inactives,
            athlete_id=athlete_id,
            athlete_name=athlete_name,
            team=team,
            event_id=event_id,
            captured=captured,
            max_report_age_seconds=max_gameday_inactives_age_seconds,
        )
        if inact.is_inactive:
            return inact
        supporting.append({"source": SOURCE_NFL_OFFICIAL_INACTIVES, "listed_inactive": False})

    # Check 2: Official complete pregame game-status report
    if official_game_status is not None:
        off = parse_nfl_official_game_status_report(
            official_game_status,
            athlete_id=athlete_id,
            athlete_name=athlete_name,
            team=team,
            event_id=event_id,
            event_start=event_start,
            captured=captured,
            max_report_age_seconds=max_official_report_age_seconds,
        )
        # If report was complete & fresh, its decision is authoritative
        if off.report_complete and off.fresh:
            return off
        supporting.append({"source": SOURCE_NFL_OFFICIAL_GAME_STATUS, "status": off.evidence_status})

    # Check 3: ESPN event summary supporting injuries
    if espn_event_summary is not None:
        espn_ev = parse_espn_event_summary_injuries(
            espn_event_summary,
            athlete_id=athlete_id,
            athlete_name=athlete_name,
            team=team,
            event_id=event_id,
            captured=captured,
        )
        # If player has an explicit stated designation from dated event summary, return it as supporting evidence
        if espn_ev.designation is not None:
            return espn_ev
        supporting.append({"source": SOURCE_ESPN_EVENT_INJURIES, "status": espn_ev.evidence_status})

    # If undated athlete profile was provided, log that it is insufficient
    if espn_athlete_profile is not None:
        supporting.append({"source": SOURCE_ESPN_ATHLETE_PROFILE, "status": "UNDATED_PROFILE_INSUFFICIENT"})

    # If nflverse row was provided, log that it is retrospective
    if nflverse_row is not None:
        supporting.append({"source": SOURCE_NFLVERSE_ARCHIVE, "status": "HISTORICAL_ARCHIVE_INSUFFICIENT"})

    # Default fail-closed: unverified availability
    return NFLPlayerAvailabilityEvidence(
        athlete_id=str(athlete_id),
        athlete_name=athlete_name,
        team=team,
        event_id=str(event_id),
        source="NFL_COMPOSITE_AVAILABILITY_FALLBACK",
        captured_at=captured.isoformat(),
        evidence_status="AVAILABILITY_EVIDENCE_NOT_SUPPLIED_OR_INCOMPLETE",
        designation=None,
        basis=None,
        fresh=False,
        report_complete=False,
        risk=False,
        supporting_sources=tuple(supporting),
        provenance={"reason": "NO_COMPLETE_OFFICIAL_OR_STATED_EVIDENCE"},
    )
