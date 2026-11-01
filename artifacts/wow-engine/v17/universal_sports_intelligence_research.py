"""Shared 13-sport V17 point-in-time intelligence *research* contract.

Pure validation and evidence inventory, with no network, data-provider entitlement,
feature-hydrator registration, model authority, probability or production writes.
It does not promote PropsMadness/source research into sporting probabilities.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "WOW_V17_UNIVERSAL_SPORT_INTELLIGENCE_RESEARCH_V1"
UNIVERSAL_DOMAINS = (
    "quality", "recent_form", "strength_of_schedule", "venue_splits",
    "matchup", "availability", "news", "game_scripts", "coaching",
    "rest_travel", "environment", "head_to_head", "advanced_metrics",
    "volatility", "incentives", "personnel_chemistry", "data_quality",
    "scenario_uncertainty", "model_validation",
)
SPORT_DOMAINS = {
    "NFL": ("quarterback", "passing_offense", "rushing_offense", "pass_defense", "run_defense", "trenches", "situational", "special_teams", "pace"),
    "NCAAF": ("program_power", "recruiting_transfers", "quarterback", "scheme", "havoc", "depth", "tempo", "finishing_drives"),
    "NCAAB": ("adjusted_efficiency", "tempo", "guard_play", "interior_play", "three_point_profile", "turnovers", "rebounding", "rotation"),
    "NBA": ("adjusted_net_rating", "five_man_lineups", "star_impact", "shot_quality", "pick_and_roll", "rotation", "pace", "late_game"),
    "WNBA": ("adjusted_net_rating", "star_impact", "rotation_depth", "paint", "perimeter", "transition", "foul_trouble", "international_absences"),
    "MLB": ("starting_pitcher", "pitcher_workload", "lineup", "platoon", "bullpen", "defense", "park", "run_distribution"),
    "NHL": ("starting_goalie", "expected_goals", "shot_quality", "slot_defense", "special_teams", "line_pairings", "overtime_shootout", "empty_net"),
    "SOCCER": ("expected_goals", "pressing", "tactical_system", "goalkeeping", "confirmed_xi", "set_pieces", "red_cards", "three_way_outcomes"),
    "TENNIS": ("surface", "serve", "return", "style_matchup", "fitness", "tiebreak", "format", "retirement"),
    "PGA": ("strokes_gained", "ball_striking", "short_game", "driving", "course_fit", "weather_wave", "cut_line", "field_simulation"),
    "MMA": ("striking", "grappling", "stance", "conditioning", "durability", "training", "weight_cut", "finish_methods"),
    "BOXING": ("punching", "defense", "stance", "conditioning", "durability", "judging", "training", "finish_methods"),
    "CRICKET": ("batting", "bowling", "powerplay", "middle_overs", "death_overs", "pitch", "toss", "chase", "format_settlement"),
}


class IntelligenceResearchHold(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise IntelligenceResearchHold(code)


def _time(value: Any) -> datetime:
    _require(isinstance(value, str), "INTEL_TIMESTAMP_INVALID")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise IntelligenceResearchHold("INTEL_TIMESTAMP_INVALID") from exc
    _require(dt.utcoffset() is not None, "INTEL_TIMESTAMP_INVALID")
    return dt.astimezone(timezone.utc)


def sport_intelligence_inventory() -> dict[str, Any]:
    return {"schema": SCHEMA, "sport_count": len(SPORT_DOMAINS),
            "universal_domains": list(UNIVERSAL_DOMAINS),
            "sport_domains": {k: list(v) for k, v in sorted(SPORT_DOMAINS.items())},
            "registered_fitted_models": [], "production_capability_attested": False,
            "can_execute": False}


def compile_intelligence_research_snapshot(
    *, sport: str, official_event_id: str, event_start_utc: str,
    as_of_utc: str, facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reject leakage, unsupported categories, missing sources and probability injection.

    Passing a source citation or rights receipt does NOT certify the underlying
    information. The output is a replayable research ledger, never model inputs.
    """
    _require(sport in SPORT_DOMAINS, "INTEL_SPORT_UNSUPPORTED")
    _require(isinstance(official_event_id, str) and official_event_id.strip(),
             "INTEL_EVENT_ID_MISSING")
    start, cutoff = _time(event_start_utc), _time(as_of_utc)
    _require(cutoff < start, "INTEL_CUTOFF_NOT_PREGAME")
    _require(isinstance(facts, (list, tuple)) and len(facts) <= 500,
             "INTEL_FACTS_INVALID")
    domains = set(UNIVERSAL_DOMAINS) | set(SPORT_DOMAINS[sport])
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for f in facts:
        _require(isinstance(f, Mapping), "INTEL_FACT_SCHEMA_INVALID")
        _require(not any(k in f for k in (
            "model_probability", "calibrated_probability", "calibrated_lower_bound",
            "scenario_probability", "market_implied_probability", "can_execute",
        )), "INTEL_PROBABILITY_AUTHORITY_FORBIDDEN")
        domain, metric, entity = f.get("domain"), f.get("metric"), f.get("entity_id")
        _require(isinstance(domain, str) and domain in domains,
                 "INTEL_DOMAIN_UNSUPPORTED")
        _require(all(isinstance(v, str) and v.strip() for v in (metric, entity)),
                 "INTEL_FACT_IDENTITY_INVALID")
        source_id, url = f.get("source_record_id"), f.get("source_url")
        _require(isinstance(source_id, str) and source_id.strip()
                 and isinstance(url, str) and urlparse(url).scheme == "https"
                 and bool(urlparse(url).hostname), "INTEL_PROVENANCE_MISSING")
        observed = _time(f.get("observed_at"))
        first_known = _time(f.get("first_available_at"))
        retrieved = _time(f.get("retrieved_at"))
        _require(observed <= first_known <= retrieved <= cutoff,
                 "INTEL_POST_CUTOFF_OR_CHRONOLOGY_CONFLICT")
        value = f.get("value")
        _require((isinstance(value, str) and bool(value.strip()))
                 or (type(value) in (int, float) and math.isfinite(value)),
                 "INTEL_FEATURE_VALUE_INVALID")
        evidence_kind = f.get("evidence_kind")
        _require(evidence_kind in {"SOURCE_OBSERVATION", "CONTEXT_HYPOTHESIS"},
                 "INTEL_EVIDENCE_KIND_INVALID")
        key = (url, source_id)
        _require(key not in seen, "INTEL_DUPLICATE_SOURCE_RECORD")
        seen.add(key)
        source_ref = hashlib.sha256(f"{url}|{source_id}".encode()).hexdigest()
        rows.append({
            "domain": domain, "metric": metric.strip(), "entity_id": entity.strip(),
            "value": value, "evidence_kind": evidence_kind,
            "source_url": url, "source_record_id": source_id,
            "source_reference": source_ref,
            "observed_at": observed.isoformat(),
            "first_available_at": first_known.isoformat(),
            "retrieved_at": retrieved.isoformat(),
            "rights_receipt_id_unverified": f.get("rights_receipt_id"),
            "source_attestation": "UNVERIFIED_RESEARCH",
            "pregame_model_feature_eligible": False,
        })
    seen_domains = {f["domain"] for f in rows}
    return {
        "schema": SCHEMA, "sport": sport, "official_event_id_claimed": official_event_id,
        "event_identity_attested": False, "event_start_utc": start.isoformat(),
        "as_of_utc": cutoff.isoformat(), "research_facts": rows,
        "fact_count": len(rows),
        "domains_with_research": sorted(seen_domains),
        "unavailable_universal_domains": sorted(set(UNIVERSAL_DOMAINS) - seen_domains),
        "unavailable_sport_domains": sorted(set(SPORT_DOMAINS[sport]) - seen_domains),
        "verified_model_features": [], "scenario_probabilities": [],
        "model_consumption_receipts": [], "calibrated_probability": None,
        "calibrated_lower_bound": None, "prediction_authority": False,
        "probability_publishable": False, "can_execute": False,
    }
