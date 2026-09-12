"""Link structured source snapshots into Scout observations after identity verification.

Only verified provider team mappings can attach evidence. No fuzzy linking and no
probability or execution authority are introduced.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg

try:
    from v17.scout_brain_persistence import database_url
except ModuleNotFoundError:
    from scout_brain_persistence import database_url


def _checksum(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _candidate_provider_team_ids(cur, candidate_id: str, provider: str) -> tuple[str, set[str]]:
    cur.execute("select sport_key,home_team,away_team from wow_scout.candidates where candidate_id=%s", (candidate_id,))
    row = cur.fetchone()
    if not row:
        return "", set()
    sport_key, home, away = row
    cur.execute(
        """
        select provider_entity_id
        from wow_scout.provider_entity_map
        where provider=%s and sport_key=%s and entity_type='TEAM' and verified=true
          and canonical_name = any(%s)
        """,
        (provider, sport_key, [x for x in (home, away) if x]),
    )
    return str(sport_key), {str(r[0]) for r in cur.fetchall()}


def _row_team_ids(row: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("TeamID", "HomeTeamID", "AwayTeamID", "GlobalTeamID", "HomeGlobalTeamID", "AwayGlobalTeamID"):
        value = row.get(key)
        if value is not None and str(value).strip():
            ids.add(str(value))
    return ids


def link_snapshot_to_candidate(candidate_id: str, snapshot_id: str) -> dict[str, Any]:
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select provider,sport_key,capability,source_class,observed_at,source_status,payload
                from wow_scout.source_snapshots where snapshot_id=%s
                """,
                (snapshot_id,),
            )
            snapshot = cur.fetchone()
            if not snapshot:
                return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"status":"SOURCE_SNAPSHOT_MISSING","linked_rows":0,"can_execute":False}
            provider, snapshot_sport, capability, source_class, observed_at, source_status, payload = snapshot
            sport_key, team_ids = _candidate_provider_team_ids(cur, candidate_id, str(provider))
            if not sport_key:
                return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"status":"CANDIDATE_MISSING","linked_rows":0,"can_execute":False}
            if sport_key != str(snapshot_sport):
                return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"status":"NOT_RELEVANT_SPORT","linked_rows":0,"can_execute":False}
            if not team_ids:
                cur.execute(
                    """
                    insert into wow_scout.candidate_source_links
                    (candidate_id,snapshot_id,link_status,link_reason,provider_entities,prediction_authority,can_execute)
                    values (%s,%s,'IDENTITY_UNRESOLVED','VERIFIED_CANDIDATE_TEAM_MAPPING_MISSING','{}'::jsonb,false,false)
                    on conflict (candidate_id,snapshot_id) do update set link_status='IDENTITY_UNRESOLVED',
                      link_reason='VERIFIED_CANDIDATE_TEAM_MAPPING_MISSING',linked_at=now(),prediction_authority=false,can_execute=false
                    """,
                    (candidate_id,snapshot_id),
                )
                conn.commit()
                return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"status":"IDENTITY_UNRESOLVED","linked_rows":0,"can_execute":False}
            if source_status != "OK":
                return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"status":"SOURCE_BLOCKED","linked_rows":0,"can_execute":False}

            rows = payload if isinstance(payload, list) else ([payload] if isinstance(payload, dict) else [])
            matched = [r for r in rows if isinstance(r, dict) and (_row_team_ids(r) & team_ids)]
            if not matched:
                return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"status":"NOT_RELEVANT","linked_rows":0,"can_execute":False}

            provider_entities = {"verified_team_ids": sorted(team_ids), "matched_row_count": len(matched)}
            cur.execute(
                """
                insert into wow_scout.candidate_source_links
                (candidate_id,snapshot_id,link_status,link_reason,provider_entities,prediction_authority,can_execute)
                values (%s,%s,'LINKED','VERIFIED_TEAM_ID_MATCH',%s::jsonb,false,false)
                on conflict (candidate_id,snapshot_id) do update set link_status='LINKED',link_reason='VERIFIED_TEAM_ID_MATCH',
                  provider_entities=excluded.provider_entities,linked_at=now(),prediction_authority=false,can_execute=false
                """,
                (candidate_id,snapshot_id,json.dumps(provider_entities)),
            )
            for evidence in matched:
                rendered = json.dumps(evidence, sort_keys=True, default=str)
                cur.execute(
                    """
                    insert into wow_scout.observations
                    (candidate_id,agent_id,stage,source_type,source_name,observed_at,confidence,evidence_type,evidence_text,evidence_value,
                     is_contradictory,is_stale,checksum)
                    values (%s,'wow.participant-status-researcher','STRUCTURED_SOURCE_REFRESH',%s,%s,%s,'HIGH',%s,%s,%s::jsonb,false,false,%s)
                    on conflict (candidate_id,agent_id,checksum) do nothing
                    """,
                    (candidate_id,str(provider),str(capability),observed_at,f"STRUCTURED_{str(capability).upper()}",rendered,rendered,_checksum(evidence)),
                )
        conn.commit()
    return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"status":"LINKED","linked_rows":len(matched),"prediction_authority":False,"can_execute":False}


def link_recent_sources(*, hours_ahead: int = 168) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select candidate_id,sport_key from wow_scout.candidates
                where commence_time is not null and commence_time between now() and now() + (%s || ' hours')::interval
                  and research_status <> 'NO_INTEREST'
                """,
                (hours_ahead,),
            )
            candidates = cur.fetchall()
            cur.execute(
                """
                select distinct on (provider,sport_key,capability) snapshot_id,provider,sport_key,capability
                from wow_scout.source_snapshots
                where observed_at >= now() - interval '24 hours'
                order by provider,sport_key,capability,observed_at desc
                """
            )
            snapshots = cur.fetchall()
    by_sport: dict[str, list[str]] = {}
    for sid, _, sport, _ in snapshots:
        by_sport.setdefault(str(sport), []).append(str(sid))
    for cid, sport in candidates:
        for sid in by_sport.get(str(sport), []):
            result = link_snapshot_to_candidate(str(cid), sid)
            if result.get("status") != "NOT_RELEVANT":
                results.append(result)
    return {
        "status":"COMPLETE",
        "results":results,
        "linked":sum(1 for r in results if r.get("status") == "LINKED"),
        "identity_unresolved":sum(1 for r in results if r.get("status") == "IDENTITY_UNRESOLVED"),
        "prediction_authority":False,
        "can_execute":False,
    }


__all__ = ["link_snapshot_to_candidate", "link_recent_sources"]
