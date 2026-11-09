"""Strict game-specific NFL inactives proof validator, staging-only. P0 #2131.

This module implements the documented NFLMeta confirmed-report contract.
It consumes a provider response ONLY after a separate trusted server fetch and
exact NFL game/athlete identity join. It does not fetch, register, or publish
probabilities. It is NOT yet used as permission to score: key management,
vendor game-id reconciliation and governed review remain prerequisites.

A published full negative list is different from a snapshot of a team's roster:
"no listed inactive" is safe only if both teams' lists are confirmed complete,
fresh, official, identity-resolved and cover the exact scheduled event.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

UNVERIFIED = "PLAYER_AVAILABILITY_UNVERIFIED"
UNAVAILABLE = "PLAYER_UNAVAILABLE"
CANDIDATE_READY = "GAME_INACTIVES_COMPLETE_CANDIDATE_ONLY"
MAX_REPORT_AGE = timedelta(hours=2)
MAX_PUBLISHED_LEAD = timedelta(hours=4)
MAX_KICKOFF_LEAD = timedelta(minutes=100)


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _official_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        port = url.port
    except ValueError:
        return False
    return (
        url.scheme == "https"
        and (host == "nfl.com" or host.endswith(".nfl.com"))
        and not url.username and not url.password
        and port in (None, 443)
        and not url.fragment and not url.query
    )


def _key(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name.casefold()).split())


def verify_confirmed_game_inactives(
    response: Any,
    *,
    provider_game_id: int,
    home_team: str,
    away_team: str,
    player_name: str,
    player_team: str,
    event_start: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Proof-shape validation only: no result permits V17 scoring.

    The game id and identities MUST first be resolved from a server-owned,
    independent matchup/season/week/time binder. Never accept these parameters
    from a caller as trusted evidence. A prospective ready result is deliberately
    named CANDIDATE_ONLY until that binder and source credentials are approved.
    """
    result = {"code": UNVERIFIED, "can_execute": False, "probability_publishable": False}
    if not isinstance(response, Mapping):
        return {**result, "reason": "REPORT_NOT_OBJECT"}
    if not isinstance(provider_game_id, int) or isinstance(provider_game_id, bool) or provider_game_id < 1:
        return {**result, "reason": "INVALID_PROVIDER_GAME_ID"}
    if event_start.tzinfo is None or now.tzinfo is None:
        return {**result, "reason": "CLOCK_NOT_AWARE"}
    event_start, now = event_start.astimezone(timezone.utc), now.astimezone(timezone.utc)
    if not timedelta(0) < event_start - now <= MAX_KICKOFF_LEAD:
        return {**result, "reason": "OUTSIDE_GAME_DAY_ADMISSION_WINDOW"}
    if any(not re.fullmatch(r"[A-Z]{2,3}", t) for t in (home_team, away_team, player_team)):
        return {**result, "reason": "TEAM_IDENTITY_INVALID"}
    if home_team == away_team or player_team not in (home_team, away_team) or not _key(player_name):
        return {**result, "reason": "PLAYER_EVENT_IDENTITY_MISMATCH"}
    meta, data = response.get("meta"), response.get("data")
    if not isinstance(meta, Mapping) or not isinstance(data, Mapping):
        return {**result, "reason": "REPORT_ENVELOPE_INVALID"}
    if meta.get("game_id") != provider_game_id or data.get("game_id") != provider_game_id:
        return {**result, "reason": "PROVIDER_GAME_ID_MISMATCH"}
    teams = data.get("teams")
    if not isinstance(teams, list) or len(teams) != 2:
        return {**result, "reason": "BOTH_TEAMS_REPORTS_REQUIRED"}
    seen_teams: set[str] = set()
    matched_names: set[str] = set()
    source_times: list[str] = []
    for team in teams:
        if not isinstance(team, Mapping):
            return {**result, "reason": "TEAM_REPORT_INVALID"}
        abbr = team.get("team_abbr")
        if abbr not in (home_team, away_team) or abbr in seen_teams:
            return {**result, "reason": "TEAM_REPORT_MATCH_FAILED"}
        seen_teams.add(abbr)
        if team.get("status") != "confirmed":
            return {**result, "reason": "GAME_DAY_INACTIVES_NOT_CONFIRMED"}
        report = team.get("report")
        if not isinstance(report, Mapping):
            return {**result, "reason": "REPORT_PROVENANCE_MISSING"}
        if (
            report.get("game_id") != provider_game_id
            or report.get("team_abbr") != abbr
            or report.get("report_kind") != "inactives"
            or report.get("coverage_status") != "complete"
            or report.get("latest_observed_coverage") != "complete"
            or report.get("last_check_status") != "succeeded"
            or report.get("retained_previous_report") is True
            or report.get("latest_revision") != report.get("revision")
        ):
            return {**result, "reason": "REPORT_COVERAGE_OR_REVISION_UNVERIFIED"}
        url = team.get("source_url")
        if not _official_url(url) or report.get("source_url") != url:
            return {**result, "reason": "OFFICIAL_SOURCE_UNVERIFIED"}
        published = _utc(team.get("published_at"))
        if (
            published is None
            or _utc(report.get("source_published_at")) != published
            or not event_start - MAX_PUBLISHED_LEAD <= published <= now
            or now - published > MAX_REPORT_AGE
        ):
            return {**result, "reason": "PUBLICATION_TIME_UNVERIFIED"}
        last_checked = _utc(report.get("last_checked_at"))
        if last_checked is None or last_checked < published or last_checked > now or now - last_checked > timedelta(minutes=20):
            return {**result, "reason": "SOURCE_CHECK_NOT_FRESH"}
        if not isinstance(report.get("revision"), str) or not report["revision"].strip():
            return {**result, "reason": "SOURCE_REVISION_MISSING"}
        players = team.get("players")
        count = report.get("source_row_count")
        resolved = report.get("identity_resolved_count")
        if (
            not isinstance(players, list)
            or not isinstance(count, int) or isinstance(count, bool)
            or not isinstance(resolved, int) or isinstance(resolved, bool)
            or count < 0 or resolved != count or len(players) != count
        ):
            return {**result, "reason": "INACTIVE_POPULATION_INCOMPLETE"}
        if count < 1:
            return {**result, "reason": "EMPTY_INACTIVE_REPORT_NOT_AUTHORITATIVE"}
        names: set[str] = set()
        for player in players:
            if not isinstance(player, Mapping):
                return {**result, "reason": "INACTIVE_IDENTITY_UNRESOLVED"}
            if player.get("identity_status") != "resolved" or not player.get("player_key") or not player.get("player_id"):
                return {**result, "reason": "INACTIVE_IDENTITY_UNRESOLVED"}
            name = _key(player.get("canonical_name") or player.get("name"))
            if not name or name in names:
                return {**result, "reason": "DUPLICATE_OR_MISSING_PLAYER_IDENTITY"}
            names.add(name)
        if abbr == player_team:
            matched_names = names
        source_times.append(published.isoformat())
    if seen_teams != {home_team, away_team}:
        return {**result, "reason": "BOTH_TEAMS_REPORTS_REQUIRED"}
    if _key(player_name) in matched_names:
        return {**result, "code": UNAVAILABLE, "reason": "OFFICIAL_GAME_DAY_INACTIVE"}
    # Intentionally NOT a score admission: roster eligibility, vendor game-id
    # mapping and approved source trust roots still require governed integration.
    return {
        **result,
        "code": CANDIDATE_READY,
        "reason": "OFFICIAL_INACTIVE_LIST_SHAPE_VALIDATED_NOT_ADOPTED",
        "source_published_at": source_times,
    }
