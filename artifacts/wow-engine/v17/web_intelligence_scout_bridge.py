"""Normalize provenance-bearing Scout observations into sport-specific research.

Only transforms already-acquired evidence. In particular no web content is
fetched and no source/claim, research impact or model result is invented.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from v17.web_intelligence_fallback import research_fallback
from v17.web_intelligence_sport_profiles import DOMAIN_FACTORS, normalize_sport

CAN_EXECUTE = False
# Human-reviewed directional research rubric; not a win probability.
DIRECTION_RESEARCH_SCALE = {"FAVORABLE": .75, "NEUTRAL": .50, "UNFAVORABLE": .25}


def from_scout_candidate(
    candidate: Mapping[str, Any], *,
    model_status: str, market_type: str, selection: str,
    line: float | None = None, market_quotes: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    sport = normalize_sport(candidate.get("sport_key") or candidate.get("sport"))
    event_id = str(candidate.get("official_event_id") or "").strip()
    domain_map = DOMAIN_FACTORS.get(sport, {})
    raw = candidate.get("research_domain_evidence")
    observations: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for domain, values in raw.items():
            factor = domain_map.get(str(domain).strip().lower())
            if factor is None:
                continue
            rows = values if isinstance(values, list) else [values]
            for source in rows[:50]:
                if len(observations) >= 100:
                    rejected.append({"domain": str(domain), "reason": "MAX_EVIDENCE_REACHED"})
                    break
                if not isinstance(source, Mapping):
                    rejected.append({"domain": str(domain), "reason": "SOURCE_ROW_INVALID"})
                    continue
                if not event_id or str(source.get("official_event_id") or "").strip() != event_id:
                    rejected.append({"domain": str(domain), "reason": "SOURCE_EVENT_IDENTITY_MISMATCH"})
                    continue
                if str(source.get("selection") or "").strip().casefold() != str(selection).strip().casefold():
                    rejected.append({"domain": str(domain), "reason": "SOURCE_SELECTION_IDENTITY_MISMATCH"})
                    continue
                value = source.get("favorability")
                if value is None:
                    value = DIRECTION_RESEARCH_SCALE.get(str(source.get("directional_impact") or "").upper())
                if value is None:
                    rejected.append({"domain": str(domain), "reason": "RESEARCH_DIRECTION_MISSING"})
                    continue
                url = source.get("source_url")
                provider = source.get("source_provider") or source.get("publisher")
                claim = source.get("claim")
                observed = source.get("captured_at") or source.get("effective_at")
                if not all(isinstance(x, str) and x.strip() for x in (url, provider, claim, observed)):
                    rejected.append({"domain": str(domain), "reason": "SOURCE_PROVENANCE_INCOMPLETE"})
                    continue
                observations.append({
                    "factor": factor, "favorability": value,
                    "source_group": provider, "source_class": source.get("source_class"),
                    "url": url, "claim": claim, "observed_at": observed,
                    "confirmed": source.get("confirmed") is True,
                    "evidence_type": source.get("evidence_type") or str(domain).upper(),
                })

    request = {
        "sport": sport,
        "official_event_id": event_id,
        "event_start_time_utc": candidate.get("commence_time") or candidate.get("event_start_time_utc"),
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "model_status": model_status,
        "evidence": observations,
        "market_quotes": market_quotes or [],
    }
    result = research_fallback(request, now=now)
    result.update({
        "scout_bridge_schema_version": "WOW_V17_SCOUT_WEB_INTELLIGENCE_BRIDGE_V1",
        "source_run_id": candidate.get("source_run_id"),
        "research_run_id": candidate.get("research_run_id"),
        "scout_source_rows_normalized": len(observations),
        "scout_source_rows_rejected": rejected,
        "scout_directional_scale": dict(DIRECTION_RESEARCH_SCALE),
        "source_acquisition_in_this_route": False,
        "prediction_authority": False,
        "can_execute": False,
    })
    if candidate.get("contradictory_evidence") or candidate.get("red_team_flags"):
        result["status"] = "RESEARCH_EVIDENCE_CONFLICT"
        result["research_score"] = None
        result["evidence_quality_factor"] = None
        result["scout_contradictions_present"] = True
    else:
        result["scout_contradictions_present"] = False
    return result
