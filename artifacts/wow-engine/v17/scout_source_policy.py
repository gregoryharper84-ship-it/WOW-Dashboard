"""Source trust/freshness policy for WOW V17 sport-specialist Scouts.

This module governs research evidence only. A trusted source can improve the
quality of a research thesis, but it can never become model probability or
execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

CAN_EXECUTE = False
RESEARCH_CEILING = "RESEARCH_INTEREST"


@dataclass(frozen=True)
class SourceRule:
    source_class: str
    trust_tier: str
    max_age_minutes: int
    requires_confirmation: bool = False
    notes: str = ""


TIER_ORDER = {"TIER_1_OFFICIAL": 1, "TIER_2_PRIMARY_REPORTER": 2, "TIER_3_ESTABLISHED_DATA": 3, "TIER_4_SECONDARY": 4, "TIER_5_UNVERIFIED": 5}

COMMON_SOURCE_RULES = {
    "LEAGUE_OFFICIAL": SourceRule("LEAGUE_OFFICIAL", "TIER_1_OFFICIAL", 720, False, "League transactions, official status and schedule feeds."),
    "TEAM_OFFICIAL": SourceRule("TEAM_OFFICIAL", "TIER_1_OFFICIAL", 360, False, "Team injury, lineup, roster and coach communication."),
    "VENUE_OFFICIAL": SourceRule("VENUE_OFFICIAL", "TIER_1_OFFICIAL", 720, False, "Roof, field and venue operational status."),
    "PRIMARY_BEAT_REPORTER": SourceRule("PRIMARY_BEAT_REPORTER", "TIER_2_PRIMARY_REPORTER", 240, True, "Useful for role/news discovery; important status changes should be confirmed when possible."),
    "ESTABLISHED_STATS_PROVIDER": SourceRule("ESTABLISHED_STATS_PROVIDER", "TIER_3_ESTABLISHED_DATA", 1440, False, "Structured historical/performance evidence."),
    "SPORTSBOOK_FEED": SourceRule("SPORTSBOOK_FEED", "TIER_3_ESTABLISHED_DATA", 15, False, "Market evidence only; never predictive authority."),
    "WEATHER_PROVIDER": SourceRule("WEATHER_PROVIDER", "TIER_3_ESTABLISHED_DATA", 60, False, "Forecast/observed weather evidence; freshness tightens near game time."),
    "SECONDARY_MEDIA": SourceRule("SECONDARY_MEDIA", "TIER_4_SECONDARY", 360, True, "Discovery corroboration; not sufficient alone for high-impact availability changes."),
    "SOCIAL_UNVERIFIED": SourceRule("SOCIAL_UNVERIFIED", "TIER_5_UNVERIFIED", 60, True, "Discovery only; cannot independently advance research status."),
}

SPORT_REQUIREMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "americanfootball_ncaaf": {
        "personnel": ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL", "PRIMARY_BEAT_REPORTER"),
        "matchup": ("ESTABLISHED_STATS_PROVIDER",),
        "market": ("SPORTSBOOK_FEED",),
        "weather": ("WEATHER_PROVIDER",),
        "news": ("TEAM_OFFICIAL", "PRIMARY_BEAT_REPORTER"),
    },
    "americanfootball_nfl": {
        "personnel": ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL", "PRIMARY_BEAT_REPORTER"),
        "practice": ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL"),
        "matchup": ("ESTABLISHED_STATS_PROVIDER",),
        "market": ("SPORTSBOOK_FEED",),
        "weather": ("WEATHER_PROVIDER",),
    },
    "baseball_mlb": {
        "starter": ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL", "ESTABLISHED_STATS_PROVIDER"),
        "lineup": ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL"),
        "bullpen": ("ESTABLISHED_STATS_PROVIDER", "TEAM_OFFICIAL"),
        "market": ("SPORTSBOOK_FEED",),
        "weather": ("WEATHER_PROVIDER", "VENUE_OFFICIAL"),
    },
    "basketball_nba": {
        "availability": ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL", "PRIMARY_BEAT_REPORTER"),
        "rotation": ("ESTABLISHED_STATS_PROVIDER", "PRIMARY_BEAT_REPORTER"),
        "matchup": ("ESTABLISHED_STATS_PROVIDER",),
        "market": ("SPORTSBOOK_FEED",),
    },
    "basketball_wnba": {
        "availability": ("LEAGUE_OFFICIAL", "TEAM_OFFICIAL", "PRIMARY_BEAT_REPORTER"),
        "rotation": ("ESTABLISHED_STATS_PROVIDER", "PRIMARY_BEAT_REPORTER"),
        "matchup": ("ESTABLISHED_STATS_PROVIDER",),
        "market": ("SPORTSBOOK_FEED",),
    },
}

HIGH_IMPACT_EVIDENCE = frozenset({"QB_STATUS", "STARTER_STATUS", "INACTIVE_STATUS", "LINEUP_STATUS", "MINUTES_LIMIT", "PITCH_COUNT_LIMIT", "ROOF_STATUS"})


def source_rule(source_class: str) -> SourceRule:
    return COMMON_SOURCE_RULES.get(str(source_class).upper(), COMMON_SOURCE_RULES["SOCIAL_UNVERIFIED"])


def source_requirements(sport_key: str) -> dict[str, list[str]]:
    req = SPORT_REQUIREMENTS.get(str(sport_key), {})
    return {domain: list(classes) for domain, classes in req.items()}


def evidence_quality(source_class: str, *, age_minutes: float | None, confirmed: bool, evidence_type: str | None = None) -> dict[str, Any]:
    rule = source_rule(source_class)
    stale = age_minutes is None or age_minutes > rule.max_age_minutes
    needs_confirmation = rule.requires_confirmation or str(evidence_type or "").upper() in HIGH_IMPACT_EVIDENCE
    confirmation_ok = confirmed or not needs_confirmation
    usable = not stale and confirmation_ok and rule.trust_tier != "TIER_5_UNVERIFIED"
    return {
        "source_class": rule.source_class,
        "trust_tier": rule.trust_tier,
        "max_age_minutes": rule.max_age_minutes,
        "age_minutes": age_minutes,
        "stale": stale,
        "confirmation_required": needs_confirmation,
        "confirmation_ok": confirmation_ok,
        "research_usable": usable,
        "prediction_authority": False,
        "can_execute": False,
    }


def conflict_policy() -> dict[str, Any]:
    return {
        "official_overrides_unverified": True,
        "same_tier_material_conflict": "QUARANTINE_UNTIL_RECONCILED",
        "high_impact_single_secondary_source": "WATCH_ONLY",
        "stale_evidence": "DO_NOT_ADVANCE_RESEARCH_STATUS",
        "market_conflict": "PRESERVE_ALL_BOOKS_AND_FLAG_DISAGREEMENT",
        "probability_authority": False,
        "can_execute": False,
    }


def policy_payload(sport_key: str) -> dict[str, Any]:
    return {
        "schema_version": "wow.v17.scout_source_policy.v1",
        "sport_key": sport_key,
        "research_ceiling": RESEARCH_CEILING,
        "source_requirements": source_requirements(sport_key),
        "source_rules": {key: asdict(rule) for key, rule in COMMON_SOURCE_RULES.items()},
        "conflict_policy": conflict_policy(),
        "sportsbook_evidence_only": True,
        "prediction_authority": False,
        "can_execute": False,
    }


__all__ = ["SourceRule", "source_rule", "source_requirements", "evidence_quality", "conflict_policy", "policy_payload"]
