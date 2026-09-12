"""Build sport/stage-aware evidence briefs for existing durable research workers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:  # module import when artifacts/wow-engine is on sys.path
    from v17.scout_source_policy import policy_payload
    from v17.sport_scout_registry import scout_team_for
except ModuleNotFoundError:  # direct `python v17/...py` execution
    from scout_source_policy import policy_payload
    from sport_scout_registry import scout_team_for

WORKER_ROLE = {
    "wow.source-provenance-researcher": "SOURCE_PROVENANCE",
    "wow.participant-status-researcher": "PARTICIPANT_STATUS",
    "wow.history-comparables-researcher": "HISTORY_COMPARABLES",
    "wow.matchup-context-researcher": "MATCHUP_CONTEXT",
    "wow.market-settlement-researcher": "MARKET_SETTLEMENT",
}
SUPPORTED_RESEARCH_WORKERS = tuple(WORKER_ROLE.keys())

DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _sport_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("sport_key") or candidate.get("sport") or (candidate.get("event_request") or {}).get("sport_key") or "")


def _event_start(candidate: dict[str, Any]) -> datetime | None:
    raw = candidate.get("commence_time") or candidate.get("event_start_utc") or (candidate.get("event_request") or {}).get("event_start_time_utc")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def resolve_cycle_stage(candidate: dict[str, Any], *, now: datetime | None = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sport = _sport_key(candidate)
    team = scout_team_for(sport)
    day = DAY_NAMES[now.weekday()]
    event_start = _event_start(candidate)
    hours_to_event = (event_start - now).total_seconds() / 3600.0 if event_start else None

    if sport == "americanfootball_ncaaf":
        by_day = {"MON":0,"TUE":1,"WED":2,"THU":3,"FRI":4,"SAT":5,"SUN":0}
        return team.research_cycle[by_day[day]]
    if sport == "americanfootball_nfl":
        by_day = {"TUE":0,"WED":1,"THU":2,"FRI":3,"SAT":4,"SUN":5,"MON":6}
        return team.research_cycle[by_day[day]]
    if sport == "baseball_mlb":
        if hours_to_event is not None and hours_to_event <= 1.5:
            return "PRE_GAME_FINAL_BOARD"
        if hours_to_event is not None and hours_to_event <= 4:
            return "LINEUP_CONFIRMATION"
        return "MORNING_STARTER_AND_BULLPEN" if now.hour < 16 else "MIDDAY_LINEUP_WATCH"
    if sport in {"basketball_nba", "basketball_wnba"}:
        if hours_to_event is not None and hours_to_event <= 1.5:
            return "PRE_GAME_FINAL_BOARD"
        if hours_to_event is not None and hours_to_event <= 4:
            return "STARTER_CONFIRMATION"
        return "MORNING_SLATE" if now.hour < 17 else "INJURY_REPORT_UPDATES"
    return team.research_cycle[-1] if team.research_cycle else "DAILY_DISCOVERY"


def _agent_requirements(team, worker_role: str) -> list[dict[str, Any]]:
    role_tokens = {
        "SOURCE_PROVENANCE": ("NEWS", "MARKET", "SLATE"),
        "PARTICIPANT_STATUS": ("PERSONNEL", "USAGE", "ROTATION", "QB", "STARTING_PITCHER", "LINEUP", "BULLPEN"),
        "HISTORY_COMPARABLES": ("FORM", "QB", "USAGE", "STARTING_PITCHER", "LINEUP_HITTING", "ROTATION_USAGE"),
        "MATCHUP_CONTEXT": ("MATCHUP", "SCHEME", "TRENCHES", "WEATHER", "PARK", "SCHEDULE"),
        "MARKET_SETTLEMENT": ("MARKET", "SLATE"),
    }.get(worker_role, ())
    out = []
    for agent in team.agents:
        if any(token in agent.name for token in role_tokens):
            out.append({
                "agent": agent.name,
                "mission": agent.mission,
                "evidence_domains": list(agent.evidence_domains),
            })
    return out


def build_research_brief(candidate: dict[str, Any], worker_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    sport = _sport_key(candidate)
    team = scout_team_for(sport)
    role = WORKER_ROLE.get(worker_id, "GENERIC_RESEARCH")
    return {
        "schema_version": "wow.v17.sport_research_brief.v1",
        "sport_key": sport,
        "scout_team": team.team_id,
        "worker_id": worker_id,
        "research_role": role,
        "cycle_stage": resolve_cycle_stage(candidate, now=now),
        "agent_requirements": _agent_requirements(team, role),
        "edge_classes_to_test": list(team.edge_classes),
        "required_red_team_checks": list(team.red_team_checks),
        "source_policy": policy_payload(sport),
        "output_requirements": {
            "must_timestamp_evidence": True,
            "must_preserve_source_identity": True,
            "must_flag_stale_evidence": True,
            "must_preserve_conflicts": True,
            "must_separate_observed_fact_from_inference": True,
            "may_raise_research_interest_only": True,
            "may_create_probability": False,
            "may_create_terminal_qualification": False,
            "can_execute": False,
        },
        "research_ceiling": "RESEARCH_INTEREST",
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = ["WORKER_ROLE", "SUPPORTED_RESEARCH_WORKERS", "resolve_cycle_stage", "build_research_brief"]
