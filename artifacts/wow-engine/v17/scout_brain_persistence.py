"""Durable WOW V17 Scout Brain persistence.

Research/discovery only. This module never creates model probabilities and never
executes wagers. It persists the additive sport-team handoff into the private
wow_scout schema so research can accumulate across days.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg

ALLOWED_STATUSES = {
    "WATCH", "RESEARCH_INTEREST_LOW", "RESEARCH_INTEREST_MEDIUM",
    "RESEARCH_INTEREST_HIGH", "QUARANTINED", "NO_INTEREST",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def database_url() -> str:
    url = os.environ.get("WOW_SCOUT_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("WOW_SCOUT_DATABASE_URL_UNCONFIGURED")
    return url


def stable_candidate_id(row: dict[str, Any]) -> str:
    evidence = row.get("market_evidence") if isinstance(row.get("market_evidence"), dict) else {}
    raw = "|".join(str(x or "") for x in (
        row.get("sport_key"), row.get("official_event_id"), row.get("route"),
        evidence.get("market_key"), evidence.get("description"), evidence.get("outcome_name"), evidence.get("point"),
    ))
    return "scout_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def stage_for(row: dict[str, Any]) -> str:
    cycle = row.get("research_cycle") or []
    return str(cycle[-1] if cycle else "DISCOVERY")


def priority_score(row: dict[str, Any]) -> float:
    evidence = row.get("market_evidence")
    evidence_count = len(evidence) if isinstance(evidence, list) else (1 if isinstance(evidence, dict) and evidence else 0)
    edge_count = len(row.get("edge_classes") or [])
    red_count = len(row.get("required_red_team_checks") or [])
    return min(100.0, 35.0 + min(evidence_count, 20) * 2.0 + min(edge_count, 10) * 2.0 + min(red_count, 10) * 0.5)


def normalize_status(row: dict[str, Any]) -> str:
    status = str(row.get("research_status") or "WATCH")
    return status if status in ALLOWED_STATUSES else "WATCH"


def observation_rows(candidate_id: str, row: dict[str, Any]) -> list[tuple[Any, ...]]:
    items: list[tuple[Any, ...]] = []
    evidence = row.get("market_evidence")
    evidences = evidence if isinstance(evidence, list) else ([evidence] if isinstance(evidence, dict) else [])
    for e in evidences:
        text = json.dumps(e, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        items.append((candidate_id, "MARKET_SCOUT", stage_for(row), "SPORTSBOOK", str(e.get("bookmaker") or "UNKNOWN"),
                      "MARKET_EVIDENCE", text, json.dumps(e), False, False, checksum))
    return items


def persist_handoff(handoff: dict[str, Any], *, research_run_id: str | None = None) -> dict[str, Any]:
    model_handoff = handoff.get("model_handoff") or {}
    candidates = list(model_handoff.get("team_event_candidates") or []) + list(model_handoff.get("prop_candidates") or [])
    run_id = research_run_id or str(handoff.get("research_run_id") or handoff.get("run_id") or f"scout-brain-{int(utc_now().timestamp())}")
    changed = 0
    quarantined = 0
    sport_counts: dict[str, int] = {}

    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                cid = stable_candidate_id(row)
                sport = str(row.get("sport_key") or "UNKNOWN")
                team = str(row.get("sport_scout_team") or row.get("scout_team_id") or "GENERIC_SCOUT_TEAM")
                route = str(row.get("controlling_specialist") or row.get("route") or "UNRESOLVED")
                status = normalize_status(row)
                quarantined += int(status == "QUARANTINED")
                sport_counts[sport] = sport_counts.get(sport, 0) + 1
                score = priority_score(row)
                evidence = row.get("market_evidence") if isinstance(row.get("market_evidence"), dict) else {}
                market_type = str(evidence.get("market_key") or ("TEAM_EVENT" if row.get("route") == "LLP_TEAM_BETTING_ENGINE" else "PROP"))
                selection = str(evidence.get("outcome_name") or evidence.get("description") or "") or None
                edge_classes = [str(x) for x in (row.get("edge_classes") or [])]
                red_flags = [str(x) for x in (row.get("red_team_flags") or [])]
                contradictory = [str(x) for x in (row.get("contradictory_evidence") or [])]
                thesis = row.get("research_thesis") or row.get("thesis")
                cur.execute("""
                    insert into wow_scout.candidates
                    (candidate_id,sport_key,scout_team,market_type,selection,event_id,canonical_event_id,commence_time,home_team,away_team,
                     controlling_specialist,research_status,research_priority_score,thesis,edge_classes,contradictory_evidence,red_team_flags,
                     data_completeness,source_freshness_score,probability,can_execute,updated_at)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,null,false,now())
                    on conflict (candidate_id) do update set
                      scout_team=excluded.scout_team, selection=excluded.selection, commence_time=excluded.commence_time,
                      home_team=excluded.home_team, away_team=excluded.away_team, controlling_specialist=excluded.controlling_specialist,
                      research_status=excluded.research_status, research_priority_score=excluded.research_priority_score,
                      thesis=coalesce(excluded.thesis,wow_scout.candidates.thesis), edge_classes=excluded.edge_classes,
                      contradictory_evidence=excluded.contradictory_evidence, red_team_flags=excluded.red_team_flags,
                      data_completeness=excluded.data_completeness, source_freshness_score=excluded.source_freshness_score,
                      probability=null, can_execute=false, updated_at=now()
                    returning (xmax = 0) as inserted
                """, (cid,sport,team,market_type,selection,row.get("official_event_id"),row.get("canonical_event_id"),row.get("commence_time"),
                       row.get("home_team"),row.get("away_team"),route,status,score,thesis,edge_classes,contradictory,red_flags,
                       row.get("data_completeness"),row.get("source_freshness_score")))
                inserted = bool(cur.fetchone()[0])
                changed += int(inserted)
                for obs in observation_rows(cid, row):
                    cur.execute("""
                        insert into wow_scout.observations
                        (candidate_id,agent_id,stage,source_type,source_name,evidence_type,evidence_text,evidence_value,is_contradictory,is_stale,checksum)
                        values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                        on conflict (candidate_id,agent_id,checksum) do nothing
                    """, obs)
                cur.execute("""
                    insert into wow_scout.candidate_history
                    (candidate_id,research_run_id,research_status,research_priority_score,thesis,edge_classes,contradictory_evidence,red_team_flags,
                     data_completeness,source_freshness_score,probability,snapshot)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,null,%s::jsonb)
                """, (cid,run_id,status,score,thesis,edge_classes,contradictory,red_flags,row.get("data_completeness"),row.get("source_freshness_score"),json.dumps(row)))
            cur.execute("""
                insert into wow_scout.research_runs
                (research_run_id,sport_key,scout_team,stage,started_at,completed_at,status,source_snapshot,candidate_count,changed_candidate_count,quarantined_count,can_execute)
                values (%s,'MULTI','WOW_CHIEF_SCOUT','MULTISPORT_REFRESH',now(),now(),'COMPLETE',%s::jsonb,%s,%s,%s,false)
                on conflict (research_run_id) do update set completed_at=now(), status='COMPLETE', candidate_count=excluded.candidate_count,
                  changed_candidate_count=excluded.changed_candidate_count, quarantined_count=excluded.quarantined_count, can_execute=false
            """, (run_id,json.dumps({"source_status":handoff.get("status"),"sports":sport_counts}),len(candidates),changed,quarantined))
        conn.commit()
    return {"research_run_id": run_id, "candidate_count": len(candidates), "changed_candidate_count": changed,
            "quarantined_count": quarantined, "sport_counts": sport_counts, "can_execute": False}


__all__ = ["persist_handoff", "stable_candidate_id"]
