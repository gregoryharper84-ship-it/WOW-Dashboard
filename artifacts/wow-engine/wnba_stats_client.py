"""Read-only first-party WNBA Stats acquisition client for offline model research.

The client is narrowly allowlisted to the official WNBA LeagueGameLog endpoint,
uses LeagueID=10, and requests player-level rows only. It cannot write upstream,
register/certify a model, publish a probability, or execute a wager.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import httpx

WNBA_STATS_BASE_URL = "https://stats.wnba.com"
LEAGUE_GAME_LOG_ENDPOINT = "/stats/leaguegamelog"
CAN_EXECUTE = False


class WNBAStatsUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class WNBAStatsResponse:
    season: int
    season_type: str
    retrieved_at: str
    rows: list[dict[str, Any]]
    source_identity: str = "WNBA_STATS_LEAGUE_GAME_LOG"
    can_execute: bool = False


def _result_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise WNBAStatsUnavailable("WNBA_STATS_INVALID_RESPONSE", "Expected a JSON object.")
    result_sets = payload.get("resultSets")
    if not isinstance(result_sets, list):
        single = payload.get("resultSet")
        result_sets = [single] if isinstance(single, Mapping) else None
    if not result_sets:
        raise WNBAStatsUnavailable("WNBA_STATS_RESULT_SET_MISSING", "LeagueGameLog result set is missing.")

    target = None
    for item in result_sets:
        if isinstance(item, Mapping) and str(item.get("name") or "").lower() == "leaguegamelog":
            target = item
            break
    if target is None and len(result_sets) == 1 and isinstance(result_sets[0], Mapping):
        target = result_sets[0]
    if target is None:
        raise WNBAStatsUnavailable("WNBA_STATS_LEAGUE_GAME_LOG_MISSING", "LeagueGameLog result set is missing.")

    headers = target.get("headers")
    row_set = target.get("rowSet")
    if not isinstance(headers, list) or not all(isinstance(h, str) for h in headers):
        raise WNBAStatsUnavailable("WNBA_STATS_HEADERS_INVALID", "LeagueGameLog headers are invalid.")
    if not isinstance(row_set, list):
        raise WNBAStatsUnavailable("WNBA_STATS_ROWS_INVALID", "LeagueGameLog rows are invalid.")

    output: list[dict[str, Any]] = []
    for row in row_set:
        if not isinstance(row, list) or len(row) != len(headers):
            raise WNBAStatsUnavailable("WNBA_STATS_ROW_SHAPE_INVALID", "LeagueGameLog row shape is invalid.")
        output.append(dict(zip(headers, row)))
    return output


class WNBAStatsClient:
    def __init__(self, *, base_url: str = WNBA_STATS_BASE_URL, timeout_seconds: float = 30.0):
        if base_url.rstrip("/") != WNBA_STATS_BASE_URL:
            raise WNBAStatsUnavailable("WNBA_STATS_BASE_URL_NOT_APPROVED", "Only official stats.wnba.com is approved.")
        self.base_url = WNBA_STATS_BASE_URL
        self.timeout_seconds = float(timeout_seconds)

    def player_game_logs(self, *, season: int, season_type: str = "Regular Season") -> WNBAStatsResponse:
        current_year = datetime.now(timezone.utc).year
        if season < 1997 or season > current_year:
            raise WNBAStatsUnavailable("WNBA_STATS_SEASON_OUT_OF_RANGE", "WNBA seasons begin in 1997 and future seasons are rejected.")
        if season_type not in {"Regular Season", "Playoffs"}:
            raise WNBAStatsUnavailable("WNBA_STATS_SEASON_TYPE_UNSUPPORTED", "Only Regular Season and Playoffs are allowlisted.")

        # Keep LeagueID first. The 2026 upstream has been observed to be sensitive
        # to query-string ordering. Python dicts preserve insertion order.
        params = {
            "LeagueID": "10",
            "PlayerOrTeam": "P",
            "Season": str(season),
            "SeasonType": season_type,
            "Counter": "0",
            "DateFrom": "",
            "DateTo": "",
            "Direction": "ASC",
            "Sorter": "DATE",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; WOW-Research/1.0)",
            "Referer": "https://stats.wnba.com/",
            "Accept": "application/json, text/plain, */*",
        }
        try:
            response = httpx.get(
                f"{self.base_url}{LEAGUE_GAME_LOG_ENDPOINT}",
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
                follow_redirects=True,
            )
        except Exception as exc:
            raise WNBAStatsUnavailable("WNBA_STATS_REQUEST_FAILED", "WNBA Stats read-only request failed.") from exc
        if response.status_code != 200:
            raise WNBAStatsUnavailable("WNBA_STATS_HTTP_ERROR", f"WNBA Stats returned HTTP {response.status_code}.")
        try:
            payload = response.json()
        except Exception as exc:
            raise WNBAStatsUnavailable("WNBA_STATS_INVALID_JSON", "WNBA Stats response was not valid JSON.") from exc
        rows = _result_rows(payload)
        return WNBAStatsResponse(
            season=season,
            season_type=season_type,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            rows=rows,
        )
