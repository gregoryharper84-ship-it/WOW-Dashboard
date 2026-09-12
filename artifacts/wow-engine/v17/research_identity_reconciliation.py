"""Provider-to-canonical identity reconciliation for Scout research evidence."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import psycopg
from v17.scout_brain_persistence import database_url

@dataclass(frozen=True)
class IdentityResolution:
    status: str
    provider: str
    sport_key: str
    entity_type: str
    provider_entity_id: str | None
    canonical_entity_id: str | None = None
    canonical_name: str | None = None
    reason_code: str | None = None
    verified: bool = False
    prediction_authority: bool = False
    can_execute: bool = False
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def resolve_provider_entity(provider: str, sport_key: str, entity_type: str, provider_entity_id: str | int | None) -> IdentityResolution:
    if provider_entity_id is None or str(provider_entity_id).strip() == "":
        return IdentityResolution("IDENTITY_UNRESOLVED", provider, sport_key, entity_type, None, reason_code="PROVIDER_ENTITY_ID_MISSING")
    pid = str(provider_entity_id)
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute("""select canonical_entity_id, canonical_name, verified from wow_scout.provider_entity_map where provider=%s and sport_key=%s and entity_type=%s and provider_entity_id=%s""", (provider, sport_key, entity_type, pid))
            rows = cur.fetchall()
    if not rows:
        return IdentityResolution("IDENTITY_UNRESOLVED", provider, sport_key, entity_type, pid, reason_code="VERIFIED_PROVIDER_MAPPING_MISSING")
    if len(rows) != 1:
        return IdentityResolution("IDENTITY_CONFLICT", provider, sport_key, entity_type, pid, reason_code="MULTIPLE_PROVIDER_MAPPINGS")
    canonical_id, name, verified = rows[0]
    if not verified:
        return IdentityResolution("IDENTITY_UNRESOLVED", provider, sport_key, entity_type, pid, str(canonical_id), str(name), "PROVIDER_MAPPING_NOT_VERIFIED", False)
    return IdentityResolution("LINKED", provider, sport_key, entity_type, pid, str(canonical_id), str(name), None, True)

def link_candidate_snapshot(candidate_id: str, snapshot_id: str, resolution: IdentityResolution, *, reason: str | None = None) -> dict[str, Any]:
    status = resolution.status if resolution.status in {"LINKED", "IDENTITY_UNRESOLVED", "IDENTITY_CONFLICT"} else "IDENTITY_UNRESOLVED"
    entities = {"provider":resolution.provider,"sport_key":resolution.sport_key,"entity_type":resolution.entity_type,"provider_entity_id":resolution.provider_entity_id,"canonical_entity_id":resolution.canonical_entity_id,"canonical_name":resolution.canonical_name,"verified":resolution.verified}
    with psycopg.connect(database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute("""insert into wow_scout.candidate_source_links (candidate_id,snapshot_id,link_status,link_reason,provider_entities,prediction_authority,can_execute) values (%s,%s,%s,%s,%s::jsonb,false,false) on conflict (candidate_id,snapshot_id) do update set link_status=excluded.link_status,link_reason=excluded.link_reason,provider_entities=excluded.provider_entities,linked_at=now(), prediction_authority=false, can_execute=false""", (candidate_id, snapshot_id, status, reason or resolution.reason_code, __import__("json").dumps(entities)))
        conn.commit()
    return {"candidate_id":candidate_id,"snapshot_id":snapshot_id,"link_status":status,"prediction_authority":False,"can_execute":False}

__all__ = ["IdentityResolution", "resolve_provider_entity", "link_candidate_snapshot"]
