"""Attach sport-specialist Scout team metadata to a V17 Multi-Scout handoff.

This is additive enrichment only. It does not score, calibrate, qualify, approve,
or execute any wager.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sport_scout_registry import registry_payload, scout_team_for


def enrich_candidate(row: dict[str, Any], lane: str) -> dict[str, Any]:
    enriched = dict(row)
    team = scout_team_for(str(row.get("sport_key") or ""))
    enriched["sport_scout_team"] = team.to_handoff()
    enriched["scout_team_id"] = team.team_id
    enriched["edge_classes_available"] = list(team.edge_classes)
    enriched["red_team_checks_required"] = list(team.red_team_checks)
    enriched["research_cycle"] = list(team.research_cycle)
    enriched["research_ceiling"] = "RESEARCH_INTEREST"
    enriched["discovery_status"] = "DISCOVERY_ONLY"
    enriched["probability_authority"] = False
    enriched["controlling_specialist_route"] = team.controlling_prop_route if lane == "PROP" else team.controlling_team_event_route
    enriched["route"] = enriched["controlling_specialist_route"]
    enriched["can_execute"] = False
    return enriched


def enrich_handoff(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    out = dict(payload)
    handoff = dict(out.get("model_handoff") or {})
    handoff["team_event_candidates"] = [enrich_candidate(row, "TEAM_EVENT") for row in handoff.get("team_event_candidates", []) or [] if isinstance(row, dict)]
    handoff["prop_candidates"] = [enrich_candidate(row, "PROP") for row in handoff.get("prop_candidates", []) or [] if isinstance(row, dict)]
    out["model_handoff"] = handoff
    out["sport_scout_registry"] = registry_payload()
    governance = dict(out.get("governance") or {})
    governance.update({
        "sport_scout_probability_authority": False,
        "sportsbook_implied_probability_is_model_probability": False,
        "final_probability_requires_controlling_specialist": True,
        "v17_terminal_reducer_is_terminal_authority": True,
        "can_execute": False,
    })
    out["governance"] = governance
    out["can_execute"] = False
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    target = Path(args.output)
    payload = json.loads(source.read_text(encoding="utf-8"))
    enriched = enrich_handoff(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(enriched, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "SPORT_SCOUT_ENRICHMENT_COMPLETE",
        "team_event_candidates": len(enriched.get("model_handoff", {}).get("team_event_candidates", [])),
        "prop_candidates": len(enriched.get("model_handoff", {}).get("prop_candidates", [])),
        "can_execute": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
