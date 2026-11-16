-- NHL source re-ingest: database-owned atomic lease and fenced persistence.
-- Depends on 20261110_nhl_season_timeline_receipts.sql.
-- Protected research ingestion only; cannot certify/promote/publish/execute.
-- No request-controlled timestamps: PostgreSQL clock_timestamp is authoritative.
-- All functions run as definer with empty search_path and service_role-only EXECUTE.

create or replace function public.wow_nhl_claim_reingest_lease(
    p_season text, p_token text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_now timestamptz := pg_catalog.clock_timestamp();
    v_expires timestamptz;
begin
    if p_season is null or p_season !~ '^[0-9]{8}$'
        or p_token is null or length(p_token) < 16
        or length(p_token) > 160 then
        return pg_catalog.jsonb_build_object(
            'status', 'BLOCKED', 'code', 'NHL_SOURCE_REINGEST_LEASE_INVALID',
            'can_execute', false);
    end if;
    insert into public.wow_nhl_source_reingest_leases
        (season, lease_token, acquired_at, expires_at, can_execute)
    values (p_season, p_token, v_now, v_now + interval '15 minutes', false)
    on conflict (season) do update
       set lease_token = excluded.lease_token,
           acquired_at = excluded.acquired_at,
           expires_at = excluded.expires_at,
           can_execute = false
       where public.wow_nhl_source_reingest_leases.expires_at <= v_now
    returning expires_at into v_expires;
    if v_expires is null then
        return pg_catalog.jsonb_build_object(
            'status', 'BLOCKED', 'code', 'NHL_SOURCE_REINGEST_IN_PROGRESS',
            'retryable', true, 'can_execute', false);
    end if;
    return pg_catalog.jsonb_build_object(
        'status', 'NHL_SOURCE_REINGEST_LEASE_ACQUIRED',
        'season', p_season, 'lease_token', p_token,
        'expires_at', v_expires, 'can_execute', false);
end;
$$;

create or replace function public.wow_nhl_release_reingest_lease(
    p_season text, p_token text
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_deleted integer;
begin
    delete from public.wow_nhl_source_reingest_leases
     where season = p_season and lease_token = p_token;
    get diagnostics v_deleted = row_count;
    return pg_catalog.jsonb_build_object(
        'status', case when v_deleted = 1
            then 'NHL_SOURCE_REINGEST_LEASE_RELEASED'
            else 'NHL_SOURCE_REINGEST_LEASE_RELEASE_SKIPPED' end,
        'season', p_season, 'can_execute', false);
end;
$$;

-- Rows are append-only, idempotent and fenced inside the same transaction as
-- the season lease row lock. A superseded or expired token writes zero rows.
create or replace function public.wow_nhl_append_reingest_source_batch(
    p_season text, p_token text, p_rows jsonb
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_holder text;
    v_expiry timestamptz;
    v_n integer := 0;
begin
    if pg_catalog.jsonb_typeof(p_rows) is distinct from 'array'
        or pg_catalog.jsonb_array_length(p_rows) > 250
        or pg_catalog.jsonb_array_length(p_rows) = 0 then
        return pg_catalog.jsonb_build_object(
            'status','BLOCKED','code','NHL_SOURCE_REINGEST_BATCH_INVALID',
            'can_execute',false);
    end if;
    select lease_token, expires_at into v_holder, v_expiry
      from public.wow_nhl_source_reingest_leases
     where season = p_season for update;
    if v_holder is null or v_holder is distinct from p_token
        or v_expiry <= pg_catalog.clock_timestamp() then
        return pg_catalog.jsonb_build_object(
            'status','BLOCKED','code','NHL_SOURCE_REINGEST_LEASE_STALE',
            'can_execute',false);
    end if;
    if exists (
        select 1 from pg_catalog.jsonb_array_elements(p_rows) as row_item(value)
         where pg_catalog.jsonb_typeof(value) <> 'object'
            or value->>'sport' is distinct from 'NHL'
            or value->>'league' is distinct from 'NHL'
            or value->>'season' is distinct from p_season
            or nullif(value->>'official_event_id','') is null
            or coalesce(value->>'source_payload_sha256','') !~ '^[0-9a-f]{64}$'
            or value->>'can_execute' is distinct from 'false'
            or pg_catalog.jsonb_typeof(value->'outcome_json') <> 'object'
            or coalesce(value->>'source_provider','') <> 'NHL_PUBLIC_WEB_API'
    ) then
        return pg_catalog.jsonb_build_object(
            'status','BLOCKED','code','NHL_SOURCE_REINGEST_BATCH_INVALID',
            'can_execute',false);
    end if;
    if exists (
        select 1 from pg_catalog.jsonb_to_recordset(p_rows)
            as r(official_event_id text, source_payload_sha256 text)
         group by r.official_event_id
        having count(distinct r.source_payload_sha256) > 1
    ) then
        return pg_catalog.jsonb_build_object(
            'status','BLOCKED','code','NHL_SOURCE_IDENTITY_CONFLICT',
            'can_execute',false);
    end if;
    if exists (
        select 1
        from pg_catalog.jsonb_to_recordset(p_rows)
          as r(official_event_id text, source_payload_sha256 text)
        join public.wow_d1_source_events e
          on e.sport = 'NHL' and e.official_event_id = r.official_event_id
        where e.source_payload_sha256 is distinct from r.source_payload_sha256
    ) then
        return pg_catalog.jsonb_build_object(
            'status','BLOCKED','code','NHL_SOURCE_IDENTITY_CONFLICT',
            'can_execute',false);
    end if;
    insert into public.wow_d1_source_events
        (sport,league,official_event_id,season,event_start_time,
         home_participant,away_participant,outcome_json,source_provider,
         source_uri,source_retrieved_at,source_payload_sha256,
         historical_reconstruction,can_execute)
    select sport,league,official_event_id,season,event_start_time,
         home_participant,away_participant,outcome_json,source_provider,
         source_uri,source_retrieved_at,source_payload_sha256,
         historical_reconstruction,false
      from pg_catalog.jsonb_to_recordset(p_rows) as r(
        sport text,league text,official_event_id text,season text,
        event_start_time timestamptz,home_participant text,
        away_participant text,outcome_json jsonb,source_provider text,
        source_uri text,source_retrieved_at timestamptz,
        source_payload_sha256 text,historical_reconstruction boolean,
        can_execute boolean
      )
    on conflict (sport,official_event_id,source_payload_sha256) do nothing;
    get diagnostics v_n = row_count;
    return pg_catalog.jsonb_build_object(
        'status','NHL_SOURCE_REINGEST_BATCH_PERSISTED',
        'written',v_n,'can_execute',false);
end;
$$;

-- A stale writer cannot overwrite a newer, independently covered timeline.
create or replace function public.wow_nhl_persist_reingest_timeline(
    p_season text, p_token text, p_receipt jsonb
) returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
    v_holder text;
    v_expiry timestamptz;
begin
    select lease_token, expires_at into v_holder, v_expiry
      from public.wow_nhl_source_reingest_leases
     where season = p_season for update;
    if v_holder is null or v_holder is distinct from p_token
        or v_expiry <= pg_catalog.clock_timestamp() then
        return pg_catalog.jsonb_build_object(
            'status','BLOCKED','code','NHL_SOURCE_REINGEST_LEASE_STALE',
            'can_execute',false);
    end if;
    if p_receipt->>'season' is distinct from p_season
        or p_receipt->>'season_scope' is distinct from 'FULL_SEASON'
        or p_receipt->>'provenance_complete' is distinct from 'true'
        or coalesce(p_receipt->>'schedule_digest','') !~ '^[0-9a-f]{64}$'
        or pg_catalog.jsonb_typeof(p_receipt->'settled_events') <> 'array'
        or pg_catalog.jsonb_typeof(p_receipt->'scheduled_events') <> 'array'
        or coalesce(p_receipt->>'ingest_code_sha','') !~ '^[0-9a-f]{7,64}$'
        or p_receipt->>'can_execute' is distinct from 'false' then
        return pg_catalog.jsonb_build_object(
            'status','BLOCKED','code','NHL_SOURCE_TIMELINE_INCOMPLETE',
            'can_execute',false);
    end if;
    insert into public.wow_nhl_season_timeline_receipts
        (season,provenance_complete,season_scope,settled_events,scheduled_events,
         schedule_digest,schedule_retrieved_at,verified_at,ingest_code_sha,source,can_execute)
    values (p_season,true,'FULL_SEASON',p_receipt->'settled_events',
         p_receipt->'scheduled_events',p_receipt->>'schedule_digest',
         (p_receipt->>'schedule_retrieved_at')::timestamptz,
         (p_receipt->>'verified_at')::timestamptz,
         p_receipt->>'ingest_code_sha',p_receipt->>'source',false)
    on conflict (season) do update
       set provenance_complete=excluded.provenance_complete,
           season_scope=excluded.season_scope,
           settled_events=excluded.settled_events,
           scheduled_events=excluded.scheduled_events,
           schedule_digest=excluded.schedule_digest,
           schedule_retrieved_at=excluded.schedule_retrieved_at,
           verified_at=excluded.verified_at,
           ingest_code_sha=excluded.ingest_code_sha,
           source=excluded.source,
           can_execute=false;
    return pg_catalog.jsonb_build_object(
        'status','NHL_SEASON_TIMELINE_RECEIPT_PERSISTED',
        'season',p_season,'written',1,'can_execute',false);
end;
$$;

revoke all on function public.wow_nhl_claim_reingest_lease(text,text)
    from public, anon, authenticated;
revoke all on function public.wow_nhl_release_reingest_lease(text,text)
    from public, anon, authenticated;
revoke all on function public.wow_nhl_append_reingest_source_batch(text,text,jsonb)
    from public, anon, authenticated;
revoke all on function public.wow_nhl_persist_reingest_timeline(text,text,jsonb)
    from public, anon, authenticated;
grant execute on function public.wow_nhl_claim_reingest_lease(text,text)
    to service_role;
grant execute on function public.wow_nhl_release_reingest_lease(text,text)
    to service_role;
grant execute on function public.wow_nhl_append_reingest_source_batch(text,text,jsonb)
    to service_role;
grant execute on function public.wow_nhl_persist_reingest_timeline(text,text,jsonb)
    to service_role;

-- Timeline remains readable by scoring's service role, but direct writes
-- must use the token-fenced SECURITY DEFINER RPC above.
revoke insert, update on table public.wow_nhl_season_timeline_receipts
    from service_role;
