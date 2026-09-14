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


def _evidences(row: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = row.get("market_evidence")
    if isinstance(evidence, list):
        return [item for item in evidence if isinstance(item, dict) and item]
    if isinstance(evidence, dict) and evidence:
        return [evidence]
    return []


def stable_candidate_id(row: dict[str, Any]) -> str:
    evidence = row.get("market_evidence") if isinstance(row.get("market_evidence"), dict) else {}
    raw = "|".join(str(x or "") for x in (
        row.get("sport_key"), row.get("official_event_id"), row.get("route"),
        evidence.get("market_key"), evidence.get("description"), evidence.get("outcome_name"), evidence.get("point"),
    ))
    return "scout_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def stable_source_snapshot_id(row: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, str, str]:
    """Return deterministic snapshot id, payload JSON, and payload hash.

    The snapshot identity is based only on the evidence payload plus its provider,
    sport, and capability. It is therefore reusable across candidates that point
    to the same immutable research observation while remaining independent of
    candidate identity or any model probability.
    """
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    provider = str(evidence.get("source_provider") or evidence.get("bookmaker") or evidence.get("bookmaker_title") or "UNKNOWN")
    capability = "MARKET_EVIDENCE"
    raw = "|".join((provider, str(row.get("sport_key") or "UNKNOWN"), capability, payload_hash))
    return "source_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24], payload, payload_hash


def stage_for(row: dict[str, Any]) -> str:
    cycle = row.get("research_cycle") or []
    return str(cycle[-1] if cycle else "DISCOVERY")


def priority_score(row: dict[str, Any]) -> float:
    evidence_count = len(_evidences(row))
    edge_count = len(row.get("edge_classes") or [])
    red_count = len(row.get("required_red_team_checks") or [])
    return min(100.0, 35.0 + min(evidence_count, 20) * 2.0 + min(edge_count, 10) * 2.0 + min(red_count, 10) * 0.5)


def normalize_status(row: dict[str, Any]) -> str:
    status = str(row.get("research_status") or "WATCH")
    return status if status in ALLOWED_STATUSES else "WATCH"


def observation_rows(candidate_id: str, row: dict[str, Any]) -> list[tuple[Any, ...]]:
    items: list[tuple[Any, ...]] = []
    for e in _evidences(row):
        text = json.dumps(e, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        items.append((candidate_id, "MARKET_SCOUT", stage_for(row), "SPORTSBOOK", str(e.get("source_provider") or e.get("bookmaker") or "UNKNOWN"),
                      "MARKET_EVIDENCE", text, json.dumps(e), False, False, checksum))
    return items


def _persist_source_provenance(cur: Any, candidate_id: str, row: dict[str, Any]) -> tuple[int, int]:
    snapshots = 0
    links = 0
    sport = str(row.get("sport_key") or "UNKNOWN")
    for evidence in _evidences(row):
        snapshot_id, payload, payload_hash = stable_source_snapshot_id(row, evidence)
        provider = str(evidence.get("source_provider") or evidence.get("bookmaker") or evidence.get("bookmaker_title") or "UNKNOWN")
        source_class = str(evidence.get("source_class") or "SPORTSBOOK_FEED")
        observed_at = (
            evidence.get("market_last_update")
            or evidence.get("bookmaker_last_update")
            or evidence.get("captured_at")
            or utc_now()
        )
        source_status = str(row.get("market_evidence_status") or "AVAILABLE")
        source_code = evidence.get("source_code") or evidence.get("primary_source_failure")
        source_http_status = evidence.get("source_http_status")
        cur.execute("""
            insert into wow_scout.source_snapshots
            (snapshot_id,provider,sport_key,capability,source_class,observed_at,source_status,source_code,source_http_status,
             payload,payload_hash,prediction_authority,can_execute)
            values (%s,%s,%s,'MARKET_EVIDENCE',%s,%s,%s,%s,%s,%s::jsonb,%s,false,false)
            on conflict (snapshot_id) do update set
              observed_at=excluded.observed_at, source_status=excluded.source_status,
              source_code=excluded.source_code, source_http_status=excluded.source_http_status,
              payload=excluded.payload, payload_hash=excluded.payload_hash,
              prediction_authority=false, can_execute=false
        """, (snapshot_id,provider,sport,source_class,observed_at,source_status,source_code,source_http_status,payload,payload_hash))
        snapshots += 1
        provider_entities = {
            "official_event_id": row.get("official_event_id"),
            "canonical_event_id": row.get("canonical_event_id"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "bookmaker": evidence.get("bookmaker"),
            "market_key": evidence.get("market_key"),
        }
        cur.execute("""
            insert into wow_scout.candidate_source_links
            (candidate_id,snapshot_id,link_status,link_reason,provider_entities,prediction_authority,can_execute)
            values (%s,%s,'LINKED','DIRECT_CANDIDATE_MARKET_EVIDENCE',%s::jsonb,false,false)
            on conflict (candidate_id,snapshot_id) do update set
              link_status='LINKED', link_reason='DIRECT_CANDIDATE_MARKET_EVIDENCE',
              provider_entities=excluded.provider_entities,
              prediction_authority=false, can_execute=false, linked_at=now()
        """, (candidate_id,snapshot_id,json.dumps(provider_entities)))
        links += 1
    return snapshots, links


def persist_handoff(handoff: dict[str, Any], *, research_run_id: str | None = None) -> dict[str, Any]:
    model_handoff = handoff.get("model_handoff") or {}
    candidates = list(model_handoff.get("team_event_candidates") or []) + list(model_handoff.get("prop_candidates") or [])
    run_id = research_run_id or str(handoff.get("research_run_id") or handoff.get("run_id") or f"scout-brain-{int(utc_now().timestamp())}")
    changed = 0
    quarantined = 0
    source_snapshot_count = 0
    candidate_source_link_count = 0
    observation_count = 0
    sport_counts: dict[str, int] = {}

    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            for row in candidates:
                if not isinstance(row, dict):
                    continue
                cid = stable_candidate_id(row)
                sport = str(row.get("sport_key") or "UNKNOWN")
                team_value = row.get("scout_team_id") or row.get("sport_scout_team") or "GENERIC_SCOUT_TEAM"
                team = str(team_value.get("team_id") if isinstance(team_value, dict) else team_value)
                route = str(row.get("controlling_specialist_route") or row.get("controlling_specialist") or row.get("route") or "UNRESOLVED")
                status = normalize_status(row)
                quarantined += int(status == "QUARANTINED")
                sport_counts[sport] = sport_counts.get(sport, 0) + 1
                score = priority_score(row)
                single_evidence = row.get("market_evidence") if isinstance(row.get("market_evidence"), dict) else {}
                market_type = str(single_evidence.get("market_key") or ("TEAM_EVENT" if row.get("route") == "LLP_TEAM_BETTING_ENGINE" else "PROP"))
                selection = str(single_evidence.get("outcome_name") or single_evidence.get("description") or "") or None
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
                observations = observation_rows(cid, row)
                for obs in observations:
                    cur.execute("""
                        insert into wow_scout.observations
                        (candidate_id,agent_id,stage,source_type,source_name,evidence_type,evidence_text,evidence_value,is_contradictory,is_stale,checksum)
                        values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                        on conflict (candidate_id,agent_id,checksum) do nothing
                    """, obs)
                observation_count += len(observations)
                snapshot_delta, link_delta = _persist_source_provenance(cur, cid, row)
                source_snapshot_count += snapshot_delta
                candidate_source_link_count += link_delta
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
                  changed_candidate_count=excluded.changed_candidate_count, quarantined_count=excluded.quarantined_count, can_execute=false,
                  source_snapshot=excluded.source_snapshot
            """, (run_id,json.dumps({
                "source_status": handoff.get("status"),
                "sports": sport_counts,
                "observations_seen": observation_count,
                "source_snapshots_seen": source_snapshot_count,
                "candidate_source_links_seen": candidate_source_link_count,
                "prediction_authority": False,
                "can_execute": False,
            }),len(candidates),changed,quarantined))
        conn.commit()
    return {
        "research_run_id": run_id,
        "candidate_count": len(candidates),
        "changed_candidate_count": changed,
        "quarantined_count": quarantined,
        "observation_count": observation_count,
        "source_snapshot_count": source_snapshot_count,
        "candidate_source_link_count": candidate_source_link_count,
        "sport_counts": sport_counts,
        "can_execute": False,
    }


__all__ = ["persist_handoff", "stable_candidate_id", "stable_source_snapshot_id"]
