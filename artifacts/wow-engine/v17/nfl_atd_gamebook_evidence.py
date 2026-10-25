"""Read-only NFL GSIS gamebook scoring evidence verifier. #1942.

Research settlement candidate ONLY: no writes, calibration, certification, or
execution. NFL final-stat and DNP/market rules require separate verification.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import re
from typing import Any, Mapping
from urllib.parse import urlparse

CAN_EXECUTE = False
SOURCE = "NFL_GSIS_GAMEBOOK"
SUPPORTED_MARKET = ("NFL", "ANYTIME_TD")
TEAM_NAMES = {
    "ARI":"Arizona Cardinals","ATL":"Atlanta Falcons","BAL":"Baltimore Ravens",
    "BUF":"Buffalo Bills","CAR":"Carolina Panthers","CHI":"Chicago Bears",
    "CIN":"Cincinnati Bengals","CLE":"Cleveland Browns","DAL":"Dallas Cowboys",
    "DEN":"Denver Broncos","DET":"Detroit Lions","GB":"Green Bay Packers",
    "HOU":"Houston Texans","IND":"Indianapolis Colts","JAX":"Jacksonville Jaguars",
    "KC":"Kansas City Chiefs","LAC":"Los Angeles Chargers","LAR":"Los Angeles Rams",
    "LV":"Las Vegas Raiders","MIA":"Miami Dolphins","MIN":"Minnesota Vikings",
    "NE":"New England Patriots","NO":"New Orleans Saints","NYG":"New York Giants",
    "NYJ":"New York Jets","PHI":"Philadelphia Eagles","PIT":"Pittsburgh Steelers",
    "SEA":"Seattle Seahawks","SF":"San Francisco 49ers","TB":"Tampa Bay Buccaneers",
    "TEN":"Tennessee Titans","WAS":"Washington Commanders",
}

def _hold(reason: str) -> dict[str, Any]:
    return {"status":"HELD","blocker":reason,"can_execute":False}

def _date(value: Any) -> datetime | None:
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except (ValueError,TypeError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None

def _abbrev(fullname: str) -> str | None:
    parts=[p.strip(".,") for p in str(fullname).split()]
    if len(parts)<2 or not parts[0] or not parts[-1]:
        return None
    last=re.sub(r"[^A-Za-z\-']", "", parts[-1])
    return f"{parts[0][0].upper()}.{last}" if last else None

def _official_url(url: str, season: int, week: int) -> bool:
    parsed=urlparse(url)
    if (parsed.scheme!="https" or parsed.query or parsed.fragment
        or parsed.username or parsed.password or parsed.port not in (None, 443)):
        return False
    if parsed.hostname=="www.nflgsis.com":
        return bool(re.fullmatch(rf"/{season}/REG/{week:02d}/\d{{5,6}}/Gamebook\.pdf",parsed.path))
    # NFL.com game center links resolve to this official static PDF host.
    # Cross-check season/week by the PDF's actual date/matchup below.
    if parsed.hostname=="static.www.nfl.com":
        return bool(re.fullmatch(
            r"/image/upload/v\d{8,12}/gamecenter/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.pdf",
            parsed.path,
        ))
    return False

def _extract_page_1(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(pdf_bytes))
    if not reader.pages:
        raise ValueError("GAMEBOOK_EMPTY")
    content=reader.pages[0].extract_text(extraction_mode="layout") or ""
    if len(content)>150000:
        raise ValueError("GAMEBOOK_TEXT_OVERSIZED")
    return content

def _score_events(text: str) -> list[tuple[str,str]]:
    """Return exact scorer, never the passer, from recognized GSIS scoring rows.

    An unrecognized scoring type holds all candidates for the event.
    """
    events=[]
    for raw in text.splitlines():
        line=" ".join(raw.split())
        match=re.match(r"^(\w+)\s+([1-5]|OT)\s+(\d{1,2}:\d{2})\s+([A-Z]\.\S+)\s+",line)
        if not match:
            continue
        description=line[match.end():].lower()
        if re.search(r"\d+\s+yd\.\s+(?:run|pass from|(?:interception|fumble|punt|kickoff) return)",description):
            events.append((match.group(1),match.group(4)))
        elif "touchdown" in description or " td " in f" {description} ":
            raise ValueError("SCORING_PLAY_CLASSIFICATION_UNSUPPORTED")
    return events

def verify_anytime_td_gamebook(
    prediction: Mapping[str,Any], snapshot: Mapping[str,Any], *,
    gamebook_url: str, pdf_bytes: bytes, now: datetime,
) -> dict[str,Any]:
    """Read-only candidate count; caller still needs source authentication and
    independent game-final / stat-final / participation certification."""
    if (str(prediction.get("sport") or "").upper(),str(prediction.get("stat_type") or "").upper())!=SUPPORTED_MARKET:
        return _hold("NFL_ATD_EXACT_ROUTE_REQUIRED")
    if prediction.get("can_execute") is True or snapshot.get("can_execute") is True:
        return _hold("CAN_EXECUTE_MUST_BE_FALSE")
    # This scorer parser does not authorize a different threshold, period, or
    # neighboring touchdown stat. These must each have an exact certified route.
    if str(prediction.get("direction") or "").upper() not in ("MORE", "LESS"):
        return _hold("ATD_DIRECTION_UNSUPPORTED")
    try:
        line=Decimal(str(prediction.get("line")))
    except (InvalidOperation, TypeError, ValueError):
        return _hold("ATD_LINE_INVALID")
    if not line.is_finite() or line!=Decimal("0.5"):
        return _hold("ATD_EXACT_LINE_UNSUPPORTED")
    if prediction.get("period") not in (None, "FULL_GAME"):
        return _hold("ATD_PERIOD_UNSUPPORTED")
    event_id=str(prediction.get("event_id") or "")
    match=re.fullmatch(r"(20\d{2})_(\d{2})_([A-Z]+)_([A-Z]+)",event_id)
    if not match:
        return _hold("CANONICAL_EVENT_ID_INVALID")
    season,week,away,home=int(match.group(1)),int(match.group(2)),match.group(3),match.group(4)
    if away not in TEAM_NAMES or home not in TEAM_NAMES or away==home:
        return _hold("CANONICAL_TEAM_ID_INVALID")
    if not _official_url(gamebook_url,season,week):
        return _hold("NFL_GSIS_URL_NOT_CANONICAL")
    role=snapshot.get("role_status")
    if not isinstance(role,Mapping) or role.get("identity_binding_status")!="PASS":
        return _hold("CERTIFIED_PREGAME_IDENTITY_REQUIRED")
    if str(role.get("verified_canonical_event_id"))!=event_id or str(snapshot.get("event_id"))!=event_id:
        return _hold("EVENT_IDENTITY_MISMATCH")
    team=str(role.get("team") or "").upper()
    if team not in (away,home) or str(role.get("opponent") or "").upper()!=(home if team==away else away):
        return _hold("TEAM_OPPONENT_IDENTITY_MISMATCH")
    if (str(prediction.get("player") or "").casefold()!=str(snapshot.get("player") or "").casefold()
        or str(role.get("player") or "").casefold()!=str(prediction.get("player") or "").casefold()):
        return _hold("PLAYER_IDENTITY_MISMATCH")
    if not str(role.get("espn_athlete_id") or "").isdigit():
        return _hold("IDENTITY_PROVIDER_ID_MISSING")
    created=_date(prediction.get("created_at"))
    modeled=_date(prediction.get("model_timestamp"))
    start=_date(prediction.get("event_start_time"))
    captured=_date(snapshot.get("captured_at"))
    current=_date(now)
    if any(t is None for t in (created,modeled,start,captured,current)):
        return _hold("TIMESTAMP_INCOMPLETE")
    if not (created<start and modeled<start and captured<start and current>start):
        return _hold("PREGAME_OR_FINAL_TIME_INVALID")
    if not isinstance(pdf_bytes,bytes) or not pdf_bytes.startswith(b"%PDF-") or len(pdf_bytes)>3_000_000:
        return _hold("GSIS_PDF_MISSING_OR_OVERSIZED")
    try:
        first=_extract_page_1(pdf_bytes)
    except Exception:
        return _hold("GSIS_PDF_PARSE_FAILED")
    compact=" ".join(first.split())
    if "National Football League Game Summary" not in compact:
        return _hold("GSIS_GAMEBOOK_HEADER_MISSING")
    if f"{TEAM_NAMES[away]} at {TEAM_NAMES[home]}" not in compact:
        return _hold("GSIS_GAMEBOOK_MATCHUP_MISMATCH")
    if not re.search(rf"\b0?{start.month}/0?{start.day}/{start.year}\b",compact):
        return _hold("GSIS_GAMEBOOK_DATE_MISMATCH")
    abbrev=_abbrev(str(prediction["player"]))
    if not abbrev:
        return _hold("PLAYER_NAME_UNPARSEABLE")
    try:
        scorers=_score_events(first)
    except ValueError:
        return _hold("GSIS_SCORING_CLASSIFICATION_UNSUPPORTED")
    short_teams={away:TEAM_NAMES[away].split()[-1],home:TEAM_NAMES[home].split()[-1]}
    if any(side not in set(short_teams.values()) for side,_ in scorers):
        return _hold("GSIS_SCORING_TEAM_AMBIGUOUS")
    scored=sum(1 for side,scorer in scorers if side==short_teams[team] and scorer==abbrev)
    provenance={"candidate_touchdowns":scored,"source":SOURCE,"source_url":gamebook_url,
                "gamebook_sha256":sha256(pdf_bytes).hexdigest(),"can_execute":False}
    if scored==0:
        return {**_hold("PLAYER_PARTICIPATION_AND_FINAL_STATS_REQUIRED"),**provenance}
    return {"status":"CANDIDATE_ONLY",**provenance,"official_event_id":event_id,
            "player":prediction["player"],
            "cannot_settle_without_independent_finalization":True}
