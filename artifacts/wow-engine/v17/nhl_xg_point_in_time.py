"""Pure research-only NHL 5v5 xG point-in-time context reconstruction.

No source fetch, external entitlement verification, model fitting, confidence
bounds, sporting predictions, market integration or publication authority.
A caller's source ID and hash prove neither licensing nor authenticity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import re
from typing import Any, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
DEFAULT_WINDOW = 10
DEFAULT_MIN_PRIOR_GAMES = 5


class NHLXGResearchError(ValueError):
    """Typed fail-closed research error, never a scorer result."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NHLXGRecord:
    official_event_id: str
    start_utc: str
    settled_utc: str
    captured_utc: str
    home_team: str
    away_team: str
    home_5v5_xg: float
    away_5v5_xg: float
    source_id: str
    source_sha256: str
    strength: str
    game_type: str


def _time(value: str) -> datetime:
    if not isinstance(value, str):
        raise NHLXGResearchError("NHL_XG_TIME_INVALID")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NHLXGResearchError("NHL_XG_TIME_INVALID") from exc


def _team(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) is not None


def _nonnegative_xg(value: Any) -> bool:
    return type(value) in (int, float) and isfinite(value) and 0 <= value <= 30


def _sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _research_result(status: str, *, features: dict[str, float] | None,
                     failure_code: str | None, home_n: int, away_n: int,
                     digest: str) -> dict[str, Any]:
    return {
        "status": status, "failure_code": failure_code, "features": features,
        "home_prior_games": home_n, "away_prior_games": away_n,
        "lineage_digest": digest,
        "source_rights_independently_verified": False,
        "source_authenticity_independently_verified": False,
        "eligible_for_fitted_training": False,
        "probability_publishable": False, "rank_eligible": False,
        "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY,
    }


def reconstruct_5v5_xg(
    *, target_event_id: str, target_start_utc: str, as_of_utc: str,
    home_team: str, away_team: str, prior_games: Sequence[NHLXGRecord],
    window: int = DEFAULT_WINDOW, min_prior_games: int = DEFAULT_MIN_PRIOR_GAMES,
) -> dict[str, Any]:
    """Reconstruct signed home-minus-away 5v5 context from captured prior games.

    A historically reconstructed sample is *not* an archived PIT receipt. No
    returned statistic may be used for a serving model without separate source
    rights, source authentication, fit, calibration and scientific acceptance.
    """
    if not (isinstance(target_event_id, str) and target_event_id.strip()):
        raise NHLXGResearchError("NHL_XG_TARGET_INVALID")
    if not (_team(home_team) and _team(away_team) and home_team != away_team):
        raise NHLXGResearchError("NHL_XG_INVALID_TEAM_ID")
    if (type(window) is not int or type(min_prior_games) is not int
        or not 1 <= min_prior_games <= window <= 82):
        raise NHLXGResearchError("NHL_XG_WINDOW_INVALID")
    cutoff = _time(as_of_utc)
    target_start = _time(target_start_utc)
    if cutoff >= target_start:
        raise NHLXGResearchError("NHL_XG_CUTOFF_INVALID")
    if not isinstance(prior_games, Sequence) or isinstance(prior_games, (str, bytes, bytearray)):
        raise NHLXGResearchError("NHL_XG_HISTORY_INVALID")

    seen: set[str] = set()
    ordered: list[tuple[datetime, NHLXGRecord]] = []
    for row in prior_games:
        if not isinstance(row, NHLXGRecord):
            raise NHLXGResearchError("NHL_XG_HISTORY_INVALID")
        if not isinstance(row.official_event_id, str) or not row.official_event_id.strip():
            raise NHLXGResearchError("NHL_XG_EVENT_ID_INVALID")
        if row.official_event_id == target_event_id:
            raise NHLXGResearchError("NHL_XG_TARGET_LEAKAGE")
        if row.official_event_id in seen:
            raise NHLXGResearchError("NHL_XG_DUPLICATE_EVENT")
        seen.add(row.official_event_id)
        if not (_team(row.home_team) and _team(row.away_team) and row.home_team != row.away_team):
            raise NHLXGResearchError("NHL_XG_INVALID_TEAM_ID")
        if row.game_type != "REGULAR_SEASON":
            raise NHLXGResearchError("NHL_XG_GAME_TYPE_INVALID")
        if row.strength != "5V5":
            raise NHLXGResearchError("NHL_XG_STRENGTH_INVALID")
        if not (_nonnegative_xg(row.home_5v5_xg) and _nonnegative_xg(row.away_5v5_xg)):
            raise NHLXGResearchError("NHL_XG_VALUE_INVALID")
        if (not isinstance(row.source_id, str) or not row.source_id.strip()
            or not _sha(row.source_sha256)):
            raise NHLXGResearchError("NHL_XG_SOURCE_INVALID")
        start, settled, captured = _time(row.start_utc), _time(row.settled_utc), _time(row.captured_utc)
        if start >= target_start:
            raise NHLXGResearchError("NHL_XG_NOT_PRIOR")
        if not start < settled <= captured:
            raise NHLXGResearchError("NHL_XG_CHRONOLOGY_INVALID")
        if captured > cutoff:
            raise NHLXGResearchError("NHL_XG_POST_CUTOFF")
        ordered.append((start, row))
    ordered.sort(key=lambda item: (item[0], item[1].official_event_id))

    def team_history(team: str) -> list[tuple[float, float, NHLXGRecord]]:
        found = []
        for _, game in ordered:
            if team == game.home_team:
                found.append((float(game.home_5v5_xg), float(game.away_5v5_xg), game))
            elif team == game.away_team:
                found.append((float(game.away_5v5_xg), float(game.home_5v5_xg), game))
        return found[-window:]

    home = team_history(home_team)
    away = team_history(away_team)
    selected = {r.official_event_id: r for _, _, r in home + away}
    lineage = {
        "event": target_event_id, "home": home_team, "away": away_team,
        "start": target_start.isoformat(), "as_of": cutoff.isoformat(),
        "window": window, "min_prior_games": min_prior_games,
        "records": [
            (r.official_event_id, r.start_utc, r.settled_utc, r.captured_utc, r.home_team,
             r.away_team, r.home_5v5_xg, r.away_5v5_xg, r.source_id, r.source_sha256)
            for r in sorted(selected.values(), key=lambda g: g.official_event_id)
        ],
    }
    fingerprint = sha256(json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if len(home) < min_prior_games or len(away) < min_prior_games:
        return _research_result("HELD", features=None,
            failure_code="NHL_XG_HISTORY_INSUFFICIENT", home_n=len(home),
            away_n=len(away), digest=fingerprint)

    def aggregate(history: list[tuple[float, float, NHLXGRecord]]) -> tuple[float, float, float] | None:
        n = len(history)
        for_avg = sum(x for x, _, _ in history) / n
        against_avg = sum(y for _, y, _ in history) / n
        denom = for_avg + against_avg
        return (for_avg, against_avg, for_avg / denom) if denom > 0 else None

    h, a = aggregate(home), aggregate(away)
    if h is None or a is None:
        return _research_result("HELD", features=None,
            failure_code="NHL_XG_SHARE_DENOMINATOR_ZERO", home_n=len(home),
            away_n=len(away), digest=fingerprint)
    features = {
        "home_5v5_xgf_per_game": h[0], "home_5v5_xga_per_game": h[1],
        "away_5v5_xgf_per_game": a[0], "away_5v5_xga_per_game": a[1],
        "xgf_per_game_delta": h[0] - a[0],
        "xga_per_game_delta": h[1] - a[1],
        "xg_diff_per_game_delta": (h[0] - h[1]) - (a[0] - a[1]),
        "xg_share_delta": h[2] - a[2],
    }
    return _research_result("RESEARCH_FEATURES_READY_NOT_CERTIFIED", features=features,
        failure_code=None, home_n=len(home), away_n=len(away), digest=fingerprint)
