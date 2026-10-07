"""Typed ESPN roster-identity fallback for WNBA prop evidence.

Primary WNBA evidence remains league-owned: WNBA schedule, WNBA Stats numerical
history, and the official WNBA injury report. This helper is identity-only and
is used only when CommonTeamRoster transport is unavailable. It cannot provide
a model probability, numerical stat history, calibration, publication, ranking,
or execution authority.

The returned ESPN athlete ID is deliberately NOT represented as a WNBA Stats
PLAYER_ID. The caller must reconcile the exact player name back to one unique
WNBA LeagueGameLog PLAYER_ID before the row may become model evidence.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

ESPN_WNBA_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
PROVIDER_ID = "ESPN_WNBA_CURRENT_ROSTER_IDENTITY"
CAN_EXECUTE = False


class WNBARosterIdentityFallbackError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def _athletes(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("athletes")
    if not isinstance(raw, list):
        raise WNBARosterIdentityFallbackError("ESPN_WNBA_ROSTER_SHAPE_INVALID")
    out: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        nested = item.get("items")
        if isinstance(nested, list):
            out.extend(row for row in nested if isinstance(row, Mapping))
        else:
            out.append(item)
    return out


def fetch_espn_wnba_team_roster(
    team: Mapping[str, Any],
    *,
    http_get: Callable[..., Any],
    timeout_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    """Return current ESPN WNBA roster identities for one scheduled team.

    The official WNBA schedule's tricode is used to bind this fallback to the
    exact event team. ESPN IDs remain typed as external identity only.
    """
    tricode = str(team.get("teamTricode") or team.get("teamTriCode") or "").strip().upper()
    if not tricode:
        raise WNBARosterIdentityFallbackError("ESPN_WNBA_TEAM_TRICODE_MISSING")

    url = f"{ESPN_WNBA_BASE}/teams/{tricode}/roster"
    try:
        response = http_get(
            url,
            params={},
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "WOW-V17-WNBA-Roster-Identity/1.0",
            },
            timeout=timeout_seconds,
            follow_redirects=True,
        )
    except Exception as exc:
        raise WNBARosterIdentityFallbackError(
            "ESPN_WNBA_ROSTER_TRANSPORT_UNAVAILABLE", type(exc).__name__
        ) from exc

    status = int(getattr(response, "status_code", 200))
    if status >= 400:
        raise WNBARosterIdentityFallbackError(
            "ESPN_WNBA_ROSTER_HTTP_ERROR", f"HTTP_{status}"
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise WNBARosterIdentityFallbackError("ESPN_WNBA_ROSTER_JSON_INVALID") from exc
    if not isinstance(payload, Mapping):
        raise WNBARosterIdentityFallbackError("ESPN_WNBA_ROSTER_JSON_INVALID")

    rows: list[dict[str, Any]] = []
    for athlete in _athletes(payload):
        name = " ".join(
            str(athlete.get("fullName") or athlete.get("displayName") or "").split()
        )
        if not name:
            continue
        position = athlete.get("position")
        pos = (
            str(position.get("abbreviation") or "").strip()
            if isinstance(position, Mapping)
            else ""
        )
        espn_id = str(athlete.get("id") or "").strip()
        rows.append(
            {
                "PLAYER": name,
                # Intentionally blank: ESPN athlete IDs are not WNBA Stats IDs.
                "PLAYER_ID": "",
                "POSITION": pos,
                "_WOW_ROSTER_SOURCE": PROVIDER_ID,
                "_WOW_ESPN_PLAYER_ID": espn_id or None,
                "_WOW_IDENTITY_ONLY": True,
                "_WOW_TEAM_TRICODE": tricode,
                "_WOW_SOURCE_URL": url,
            }
        )
    if not rows:
        raise WNBARosterIdentityFallbackError("ESPN_WNBA_ROSTER_EMPTY")
    return rows


__all__ = [
    "CAN_EXECUTE",
    "ESPN_WNBA_BASE",
    "PROVIDER_ID",
    "WNBARosterIdentityFallbackError",
    "fetch_espn_wnba_team_roster",
]
