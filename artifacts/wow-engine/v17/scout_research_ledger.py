"""Persistent research ledger for WOW V17 Scout teams.

The ledger stores discovery evidence over time. It is not a prediction store and
cannot publish model probabilities. The highest Scout output is RESEARCH_INTEREST.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sport_scout_registry import scout_team_for

ALLOWED_RESEARCH_STATUSES = {
    "WATCH",
    "RESEARCH_INTEREST_LOW",
    "RESEARCH_INTEREST_MEDIUM",
    "RESEARCH_INTEREST_HIGH",
    "QUARANTINED",
    "NO_INTEREST",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_candidate_key(row: dict[str, Any], lane: str) -> str:
    event_id = str(row.get("official_event_id") or row.get("event_id") or "UNKNOWN_EVENT")
    sport = str(row.get("sport_key") or row.get("sport") or "UNKNOWN_SPORT")
    market = row.get("market_evidence") if lane == "PROP" else None
    if isinstance(market, dict):
        market_bits = [
            str(market.get("market_key") or ""),
            str(market.get("description") or market.get("outcome_name") or ""),
            str(market.get("point") or ""),
        ]
    else:
        market_bits = ["TEAM_EVENT"]
    raw = "|".join([sport, event_id, lane, *market_bits])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def empty_ledger() -> dict[str, Any]:
    return {
        "schema_version": "wow.v17.scout_research_ledger.v1",
        "updated_at": utc_now_iso(),
        "candidates": {},
        "governance": {
            "research_ceiling": "RESEARCH_INTEREST",
            "scout_probability_authority": False,
            "sportsbook_probability_authority": False,
            "final_probability_requires_controlling_specialist": True,
            "can_execute": False,
        },
        "can_execute": False,
    }


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_ledger()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != "wow.v17.scout_research_ledger.v1":
        raise ValueError("SCOUT_LEDGER_SCHEMA_INVALID")
    data.setdefault("candidates", {})
    data["can_execute"] = False
    return data


def _observation_from_candidate(row: dict[str, Any], lane: str, source_run_id: str | None) -> dict[str, Any]:
    now = utc_now_iso()
    team = scout_team_for(str(row.get("sport_key") or ""))
    return {
        "observed_at": now,
        "source_run_id": source_run_id,
        "lane": lane,
        "discovery_status": row.get("discovery_status", "DISCOVERY_ONLY"),
        "research_ceiling": "RESEARCH_INTEREST",
        "source_event_id": row.get("official_event_id") or row.get("event_id"),
        "market_evidence": row.get("market_evidence"),
        "game_script_hypotheses": list(row.get("game_script_hypotheses") or []),
        "scout_team_id": team.team_id,
        "controlling_specialist_route": team.controlling_prop_route if lane == "PROP" else team.controlling_team_event_route,
        "probability": None,
        "can_execute": False,
    }


def merge_handoff(ledger: dict[str, Any], handoff: dict[str, Any]) -> dict[str, Any]:
    if handoff.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    model_handoff = handoff.get("model_handoff") or {}
    source_run_id = handoff.get("run_id") or handoff.get("research_run_id")
    candidates = ledger.setdefault("candidates", {})

    for lane, rows_key in (("TEAM_EVENT", "team_event_candidates"), ("PROP", "prop_candidates")):
        for row in model_handoff.get(rows_key, []) or []:
            if not isinstance(row, dict):
                continue
            key = canonical_candidate_key(row, lane)
            team = scout_team_for(str(row.get("sport_key") or ""))
            entry = candidates.setdefault(key, {
                "candidate_id": key,
                "sport_key": row.get("sport_key"),
                "official_event_id": row.get("official_event_id") or row.get("event_id"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "commence_time": row.get("commence_time"),
                "lane": lane,
                "scout_team_id": team.team_id,
                "controlling_specialist_route": team.controlling_prop_route if lane == "PROP" else team.controlling_team_event_route,
                "research_status": "WATCH",
                "edge_classes": [],
                "thesis": [],
                "contradictory_evidence": [],
                "red_team": {"status": "PENDING", "flags": []},
                "observations": [],
                "first_seen_at": utc_now_iso(),
                "last_seen_at": utc_now_iso(),
                "can_execute": False,
            })
            entry["last_seen_at"] = utc_now_iso()
            entry["observations"].append(_observation_from_candidate(row, lane, source_run_id))
            entry["observations"] = entry["observations"][-50:]
            entry["can_execute"] = False

    ledger["updated_at"] = utc_now_iso()
    ledger["can_execute"] = False
    return ledger


def apply_research_update(ledger: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(update.get("candidate_id") or "")
    if candidate_id not in ledger.get("candidates", {}):
        raise ValueError("SCOUT_CANDIDATE_NOT_FOUND")
    entry = ledger["candidates"][candidate_id]
    status = str(update.get("research_status") or entry.get("research_status") or "WATCH")
    if status not in ALLOWED_RESEARCH_STATUSES:
        raise ValueError("SCOUT_RESEARCH_STATUS_INVALID")
    entry["research_status"] = status
    for field in ("edge_classes", "thesis", "contradictory_evidence"):
        if field in update:
            values = update[field]
            if not isinstance(values, list):
                raise ValueError(f"SCOUT_{field.upper()}_MUST_BE_LIST")
            entry[field] = [str(v) for v in values]
    if "red_team" in update:
        red = update["red_team"]
        if not isinstance(red, dict):
            raise ValueError("SCOUT_RED_TEAM_INVALID")
        entry["red_team"] = {
            "status": str(red.get("status") or "PENDING"),
            "flags": [str(v) for v in (red.get("flags") or [])],
        }
    entry["probability"] = None
    entry["can_execute"] = False
    entry["last_research_update_at"] = utc_now_iso()
    ledger["updated_at"] = utc_now_iso()
    return ledger


def priority_board(ledger: dict[str, Any]) -> dict[str, Any]:
    rank = {
        "RESEARCH_INTEREST_HIGH": 0,
        "RESEARCH_INTEREST_MEDIUM": 1,
        "RESEARCH_INTEREST_LOW": 2,
        "WATCH": 3,
        "QUARANTINED": 4,
        "NO_INTEREST": 5,
    }
    rows = list(ledger.get("candidates", {}).values())
    rows.sort(key=lambda x: (rank.get(str(x.get("research_status")), 99), str(x.get("commence_time") or ""), str(x.get("candidate_id") or "")))
    return {
        "schema_version": "wow.v17.scout_priority_board.v1",
        "generated_at": utc_now_iso(),
        "candidates": rows,
        "governance": ledger.get("governance"),
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--handoff")
    parser.add_argument("--update")
    parser.add_argument("--board")
    args = parser.parse_args()
    path = Path(args.ledger)
    ledger = load_ledger(path)
    if args.handoff:
        ledger = merge_handoff(ledger, json.loads(Path(args.handoff).read_text(encoding="utf-8")))
    if args.update:
        payload = json.loads(Path(args.update).read_text(encoding="utf-8"))
        updates = payload if isinstance(payload, list) else [payload]
        for update in updates:
            ledger = apply_research_update(ledger, update)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.board:
        Path(args.board).write_text(json.dumps(priority_board(ledger), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SCOUT_LEDGER_UPDATED", "candidate_count": len(ledger.get("candidates", {})), "can_execute": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
