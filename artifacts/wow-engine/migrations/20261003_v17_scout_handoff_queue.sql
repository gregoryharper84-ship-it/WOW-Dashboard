-- WOW V17 Scout durable specialist handoff queue.
-- Class B orchestration only. Scout/market evidence never becomes a sporting
-- probability here. All rows remain non-executable.

create table if not exists public.wow_scout_handoff_jobs (
    job_id uuid primary key default gen_random_uuid(),
    source_run_id text not null,
    research_run_id text not null,
    candidate_id text not null,
    target_lane text not null check (target_lane in ('WOW_PROP_LANE','LLP_TEAM_BETTING_ENGINE')),
    research_priority text not null default 'UNRANKED'
        check (research_priority in ('HIGH','MEDIUM','LOW','UNRANKED')),
    target_route text not null
        check (target_route in ('/score-pick-request','/score-team-event-request')),
    request_id text not null,
    request_payload jsonb not null default '{}'::jsonb,
    current_state text not null check (current_state in (
        'DISCOVERED','RESEARCH_INTEREST','RED_TEAM_PASSED',
        'SPECIALIST_HANDOFF_QUEUED','SPECIALIST_PROCESSING',
        'MODEL_EVALUATED','V17_QUALIFIED','HANDOFF_BLOCKED'
    )),
    terminal boolean not null default false,
    attempt_count integer not null default 0 check (attempt_count >= 0),
    next_attempt_at timestamptz,
    lease_owner text,
    lease_expires_at timestamptz,
    last_error_code text,
    last_error_detail jsonb,
    specialist_receipt jsonb,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source_run_id, candidate_id, target_lane)
);

create table if not exists public.wow_scout_handoff_state_events (
    event_id bigint generated always as identity primary key,
    job_id uuid not null references public.wow_scout_handoff_jobs(job_id) on delete cascade,
    source_run_id text not null,
    research_run_id text not null,
    candidate_id text not null,
    target_lane text not null,
    state text not null check (state in (
        'DISCOVERED','RESEARCH_INTEREST','RED_TEAM_PASSED',
        'SPECIALIST_HANDOFF_QUEUED','SPECIALIST_PROCESSING',
        'MODEL_EVALUATED','V17_QUALIFIED','HANDOFF_BLOCKED'
    )),
    attempt_count integer not null default 0 check (attempt_count >= 0),
    code text,
    detail jsonb,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

create index if not exists wow_scout_handoff_jobs_claim_idx
    on public.wow_scout_handoff_jobs(current_state, next_attempt_at, lease_expires_at, updated_at)
    where terminal = false;

create index if not exists wow_scout_handoff_jobs_run_idx
    on public.wow_scout_handoff_jobs(source_run_id, research_priority, target_lane, current_state);

create index if not exists wow_scout_handoff_state_events_run_idx
    on public.wow_scout_handoff_state_events(source_run_id, candidate_id, created_at);

alter table public.wow_scout_handoff_jobs enable row level security;
alter table public.wow_scout_handoff_state_events enable row level security;

revoke all on table public.wow_scout_handoff_jobs from public, anon, authenticated;
revoke all on table public.wow_scout_handoff_state_events from public, anon, authenticated;
grant select, insert, update on table public.wow_scout_handoff_jobs to service_role;
grant select, insert on table public.wow_scout_handoff_state_events to service_role;
grant usage, select on sequence public.wow_scout_handoff_state_events_event_id_seq to service_role;

create or replace function public.wow_enqueue_scout_handoff_job(
    p_source_run_id text,
    p_research_run_id text,
    p_candidate_id text,
    p_target_lane text,
    p_research_priority text,
    p_target_route text,
    p_request_id text,
    p_request_payload jsonb,
    p_blocked_code text default null,
    p_blocked_detail jsonb default null
)
returns public.wow_scout_handoff_jobs
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    r public.wow_scout_handoff_jobs%rowtype;
    v_initial_state text;
begin
    if coalesce(trim(p_source_run_id), '') = ''
       or coalesce(trim(p_research_run_id), '') = ''
       or coalesce(trim(p_candidate_id), '') = '' then
        raise exception 'SCOUT_HANDOFF_IDENTITY_REQUIRED';
    end if;
    if p_target_lane not in ('WOW_PROP_LANE','LLP_TEAM_BETTING_ENGINE') then
        raise exception 'SCOUT_HANDOFF_TARGET_LANE_INVALID';
    end if;
    if (p_target_lane = 'WOW_PROP_LANE' and p_target_route <> '/score-pick-request')
       or (p_target_lane = 'LLP_TEAM_BETTING_ENGINE' and p_target_route <> '/score-team-event-request') then
        raise exception 'SCOUT_HANDOFF_ROUTE_LANE_MISMATCH';
    end if;

    if coalesce(p_request_payload, '{}'::jsonb) ?| array[
        'model_probability','specialist_probability','calibrated_probability',
        'calibrated_lower_bound','calibrated_upper_bound','probability_publishable'
    ] then
        raise exception 'SCOUT_PROBABILITY_AUTHORITY_VIOLATION';
    end if;
    if p_blocked_code is null and coalesce(p_request_payload, '{}'::jsonb) = '{}'::jsonb then
        raise exception 'SCOUT_HANDOFF_PAYLOAD_EMPTY';
    end if;

    v_initial_state := case when p_blocked_code is null
        then 'SPECIALIST_HANDOFF_QUEUED' else 'HANDOFF_BLOCKED' end;

    insert into public.wow_scout_handoff_jobs (
        source_run_id, research_run_id, candidate_id, target_lane, research_priority,
        target_route, request_id, request_payload, current_state, terminal,
        last_error_code, last_error_detail, can_execute
    ) values (
        p_source_run_id,
        p_research_run_id,
        p_candidate_id,
        p_target_lane,
        case when p_research_priority in ('HIGH','MEDIUM','LOW') then p_research_priority else 'UNRANKED' end,
        p_target_route,
        p_request_id,
        coalesce(p_request_payload,'{}'::jsonb),
        v_initial_state,
        p_blocked_code is not null,
        p_blocked_code,
        p_blocked_detail,
        false
    )
    on conflict (source_run_id,candidate_id,target_lane) do nothing
    returning * into r;

    if not found then
        select * into r
        from public.wow_scout_handoff_jobs
        where source_run_id=p_source_run_id
          and candidate_id=p_candidate_id
          and target_lane=p_target_lane;
        return r;
    end if;

    insert into public.wow_scout_handoff_state_events
        (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
    values
        (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'DISCOVERED',0,'SCOUT_CANDIDATE_DISCOVERED',null,false),
        (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'RESEARCH_INTEREST',0,'SCOUT_RESEARCH_INTEREST',null,false);

    if p_blocked_code is null then
        insert into public.wow_scout_handoff_state_events
            (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
        values
            (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'RED_TEAM_PASSED',0,'DETERMINISTIC_HANDOFF_HYGIENE_PASS',null,false),
            (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'SPECIALIST_HANDOFF_QUEUED',0,'SPECIALIST_HANDOFF_ENQUEUED',null,false);
    else
        insert into public.wow_scout_handoff_state_events
            (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
        values
            (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'HANDOFF_BLOCKED',0,p_blocked_code,p_blocked_detail,false);
    end if;
    return r;
end;
$$;

create or replace function public.wow_enqueue_scout_handoff_batch(p_jobs jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    item jsonb;
    r public.wow_scout_handoff_jobs%rowtype;
    v_total integer := 0;
    v_queued integer := 0;
    v_blocked integer := 0;
begin
    if jsonb_typeof(p_jobs) <> 'array' then
        raise exception 'SCOUT_HANDOFF_BATCH_ARRAY_REQUIRED';
    end if;

    for item in select value from jsonb_array_elements(p_jobs)
    loop
        if coalesce(item->>'red_team_status','') not in ('RED_TEAM_PASSED','HANDOFF_BLOCKED') then
            raise exception 'SCOUT_HANDOFF_RED_TEAM_STATUS_INVALID';
        end if;
        if item->>'red_team_status' = 'RED_TEAM_PASSED' then
            if coalesce(item->>'red_team_rule_version','') <> 'V17_DETERMINISTIC_HANDOFF_HYGIENE_V1' then
                raise exception 'SCOUT_HANDOFF_RED_TEAM_RULE_UNVERIFIED';
            end if;
            if item ? 'blocked_code' and nullif(item->>'blocked_code','') is not null then
                raise exception 'SCOUT_HANDOFF_RED_TEAM_ENVELOPE_CONTRADICTORY';
            end if;
        elsif nullif(item->>'blocked_code','') is null then
            raise exception 'SCOUT_HANDOFF_BLOCK_CODE_REQUIRED';
        end if;

        r := public.wow_enqueue_scout_handoff_job(
            item->>'source_run_id',
            item->>'research_run_id',
            item->>'candidate_id',
            item->>'target_lane',
            item->>'research_priority',
            item->>'target_route',
            item->>'request_id',
            coalesce(item->'request_payload','{}'::jsonb),
            case when item->>'red_team_status' = 'HANDOFF_BLOCKED'
                 then coalesce(item->>'blocked_code','SCOUT_HANDOFF_BLOCKED')
                 else null end,
            item->'blocked_detail'
        );
        v_total := v_total + 1;
        if r.current_state = 'HANDOFF_BLOCKED' then
            v_blocked := v_blocked + 1;
        else
            v_queued := v_queued + 1;
        end if;
    end loop;

    return jsonb_build_object(
        'candidate_jobs', v_total,
        'queued', v_queued,
        'handoff_blocked', v_blocked,
        'can_execute', false
    );
end;
$$;

create or replace function public.wow_claim_scout_handoff_job(
    p_worker_id text,
    p_lease_seconds integer default 900
)
returns setof public.wow_scout_handoff_jobs
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    r public.wow_scout_handoff_jobs%rowtype;
    v_previous_state text;
    v_previous_owner text;
    v_claim_code text;
    v_retry_safe boolean;
    v_block_code text;
begin
    if coalesce(trim(p_worker_id), '') = '' then
        raise exception 'SCOUT_HANDOFF_WORKER_ID_REQUIRED';
    end if;

    select j.* into r
    from public.wow_scout_handoff_jobs j
    where j.terminal=false
      and (
        (
          j.current_state='SPECIALIST_HANDOFF_QUEUED'
          and (j.next_attempt_at is null or j.next_attempt_at <= now())
          and (j.lease_expires_at is null or j.lease_expires_at <= now())
        )
        or
        (
          j.current_state='SPECIALIST_PROCESSING'
          and j.lease_expires_at is not null
          and j.lease_expires_at <= now()
        )
      )
    order by
      case when j.current_state='SPECIALIST_PROCESSING' then 0 else 1 end,
      j.updated_at asc,
      j.created_at asc
    for update of j skip locked
    limit 1;

    if not found then
        return;
    end if;

    v_previous_state := r.current_state;
    v_previous_owner := r.lease_owner;

    if v_previous_state='SPECIALIST_PROCESSING' then
        v_retry_safe := (
            r.target_lane='WOW_PROP_LANE'
            or (
                r.target_lane='LLP_TEAM_BETTING_ENGINE'
                and upper(coalesce(r.request_payload->>'sport',''))='MLB'
                and upper(coalesce(r.request_payload->>'league',''))='MLB'
                and coalesce(trim(r.request_payload->>'research_run_id'),'') <> ''
                and coalesce(trim(r.request_payload->>'event_key'),'') <> ''
            )
        );
        if not v_retry_safe or r.attempt_count >= 2 then
            v_block_code := case
                when not v_retry_safe then 'SCOUT_HANDOFF_AMBIGUOUS_RETRY_PROHIBITED'
                else 'SCOUT_HANDOFF_RETRY_EXHAUSTED'
            end;
            update public.wow_scout_handoff_jobs j
               set current_state='HANDOFF_BLOCKED',
                   terminal=true,
                   lease_owner=null,
                   lease_expires_at=null,
                   next_attempt_at=null,
                   last_error_code=v_block_code,
                   last_error_detail=jsonb_build_object(
                       'lease_lost',true,
                       'previous_worker_id',v_previous_owner,
                       'retry_safe',v_retry_safe,
                       'attempt_count',r.attempt_count,
                       'lease_reclaim_budget',2
                   ),
                   updated_at=now()
             where j.job_id=r.job_id
            returning j.* into r;

            insert into public.wow_scout_handoff_state_events
                (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
            values
                (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'HANDOFF_BLOCKED',r.attempt_count,v_block_code,
                 r.last_error_detail,false);
            return;
        end if;
    end if;

    v_claim_code := case
        when v_previous_state='SPECIALIST_PROCESSING' then 'SPECIALIST_LEASE_RECLAIMED'
        else 'SPECIALIST_WORKER_CLAIMED'
    end;

    update public.wow_scout_handoff_jobs j
       set current_state='SPECIALIST_PROCESSING',
           attempt_count=j.attempt_count+1,
           lease_owner=p_worker_id,
           lease_expires_at=now()+make_interval(secs => greatest(60,least(coalesce(p_lease_seconds,900),3600))),
           next_attempt_at=null,
           updated_at=now()
     where j.job_id=r.job_id
    returning j.* into r;

    insert into public.wow_scout_handoff_state_events
        (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
    values
        (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'SPECIALIST_PROCESSING',r.attempt_count,v_claim_code,
         jsonb_build_object(
           'worker_id',p_worker_id,
           'previous_state',v_previous_state,
           'previous_worker_id',v_previous_owner,
           'lease_reclaimed',v_previous_state='SPECIALIST_PROCESSING'
         ),false);

    return next r;
end;
$$;

create or replace function public.wow_finish_scout_handoff_job(
    p_job_id uuid,
    p_worker_id text,
    p_specialist_receipt jsonb
)
returns public.wow_scout_handoff_jobs
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    r public.wow_scout_handoff_jobs%rowtype;
    v_outcome jsonb;
    v_status text;
    v_v17_qualified boolean;
begin
    select * into r from public.wow_scout_handoff_jobs where job_id=p_job_id for update;
    if not found then raise exception 'SCOUT_HANDOFF_JOB_NOT_FOUND'; end if;
    if r.current_state <> 'SPECIALIST_PROCESSING' or r.lease_owner is distinct from p_worker_id then
        raise exception 'SCOUT_HANDOFF_LEASE_OWNERSHIP_MISMATCH';
    end if;

    v_outcome := coalesce(
        p_specialist_receipt->'result'->'outcomes'->0,
        p_specialist_receipt->'result'->'rows'->0,
        '{}'::jsonb
    );
    v_status := upper(coalesce(v_outcome->>'terminal_status',v_outcome->>'status',''));
    v_v17_qualified := (
        v_status='COMPLETED'
        and coalesce(v_outcome->>'probability_publishable','false')='true'
        and coalesce(v_outcome->>'rank_eligible','false')='true'
        and coalesce(v_outcome->>'card_admission_eligible','false')='true'
    );

    update public.wow_scout_handoff_jobs
       set current_state=case when v_v17_qualified then 'V17_QUALIFIED' else 'MODEL_EVALUATED' end,
           terminal=true,
           specialist_receipt=coalesce(p_specialist_receipt,'{}'::jsonb),
           lease_owner=null, lease_expires_at=null, next_attempt_at=null,
           last_error_code=null,last_error_detail=null,updated_at=now()
     where job_id=p_job_id
    returning * into r;

    insert into public.wow_scout_handoff_state_events
        (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
    values
        (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'MODEL_EVALUATED',r.attempt_count,'SPECIALIST_MODEL_EVALUATED',p_specialist_receipt,false);

    if v_v17_qualified then
        insert into public.wow_scout_handoff_state_events
            (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
        values
            (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'V17_QUALIFIED',r.attempt_count,'V17_GOVERNED_ADMISSION_PROVEN',null,false);
    end if;
    return r;
end;
$$;

create or replace function public.wow_retry_scout_handoff_job(
    p_job_id uuid,
    p_worker_id text,
    p_delay_seconds integer,
    p_error_code text,
    p_error_detail jsonb default null
)
returns public.wow_scout_handoff_jobs
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    r public.wow_scout_handoff_jobs%rowtype;
begin
    select * into r from public.wow_scout_handoff_jobs where job_id=p_job_id for update;
    if not found then raise exception 'SCOUT_HANDOFF_JOB_NOT_FOUND'; end if;
    if r.current_state <> 'SPECIALIST_PROCESSING' or r.lease_owner is distinct from p_worker_id then
        raise exception 'SCOUT_HANDOFF_LEASE_OWNERSHIP_MISMATCH';
    end if;

    update public.wow_scout_handoff_jobs
       set current_state='SPECIALIST_HANDOFF_QUEUED',terminal=false,
           next_attempt_at=now()+make_interval(secs => greatest(1,least(coalesce(p_delay_seconds,15),600))),
           lease_owner=null,lease_expires_at=null,
           last_error_code=p_error_code,last_error_detail=p_error_detail,updated_at=now()
     where job_id=p_job_id returning * into r;

    insert into public.wow_scout_handoff_state_events
        (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
    values
        (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'SPECIALIST_HANDOFF_QUEUED',r.attempt_count,p_error_code,p_error_detail,false);
    return r;
end;
$$;

create or replace function public.wow_block_scout_handoff_job(
    p_job_id uuid,
    p_worker_id text,
    p_error_code text,
    p_error_detail jsonb default null
)
returns public.wow_scout_handoff_jobs
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    r public.wow_scout_handoff_jobs%rowtype;
begin
    select * into r from public.wow_scout_handoff_jobs where job_id=p_job_id for update;
    if not found then raise exception 'SCOUT_HANDOFF_JOB_NOT_FOUND'; end if;
    if r.current_state <> 'SPECIALIST_PROCESSING' or r.lease_owner is distinct from p_worker_id then
        raise exception 'SCOUT_HANDOFF_LEASE_OWNERSHIP_MISMATCH';
    end if;

    update public.wow_scout_handoff_jobs
       set current_state='HANDOFF_BLOCKED',terminal=true,
           lease_owner=null,lease_expires_at=null,next_attempt_at=null,
           last_error_code=p_error_code,last_error_detail=p_error_detail,updated_at=now()
     where job_id=p_job_id returning * into r;

    insert into public.wow_scout_handoff_state_events
        (job_id,source_run_id,research_run_id,candidate_id,target_lane,state,attempt_count,code,detail,can_execute)
    values
        (r.job_id,r.source_run_id,r.research_run_id,r.candidate_id,r.target_lane,'HANDOFF_BLOCKED',r.attempt_count,p_error_code,p_error_detail,false);
    return r;
end;
$$;

revoke all on function public.wow_enqueue_scout_handoff_job(text,text,text,text,text,text,text,jsonb,text,jsonb) from public, anon, authenticated;
revoke all on function public.wow_enqueue_scout_handoff_batch(jsonb) from public, anon, authenticated;
revoke all on function public.wow_claim_scout_handoff_job(text,integer) from public, anon, authenticated;
revoke all on function public.wow_finish_scout_handoff_job(uuid,text,jsonb) from public, anon, authenticated;
revoke all on function public.wow_retry_scout_handoff_job(uuid,text,integer,text,jsonb) from public, anon, authenticated;
revoke all on function public.wow_block_scout_handoff_job(uuid,text,text,jsonb) from public, anon, authenticated;

grant execute on function public.wow_enqueue_scout_handoff_job(text,text,text,text,text,text,text,jsonb,text,jsonb) to service_role;
grant execute on function public.wow_enqueue_scout_handoff_batch(jsonb) to service_role;
grant execute on function public.wow_claim_scout_handoff_job(text,integer) to service_role;
grant execute on function public.wow_finish_scout_handoff_job(uuid,text,jsonb) to service_role;
grant execute on function public.wow_retry_scout_handoff_job(uuid,text,integer,text,jsonb) to service_role;
grant execute on function public.wow_block_scout_handoff_job(uuid,text,text,jsonb) to service_role;

comment on table public.wow_scout_handoff_jobs is
    'V17 Scout evidence-only durable specialist handoff. Never grants probability or wager authority.';
comment on table public.wow_scout_handoff_state_events is
    'Append-only lifecycle receipts for Scout candidate handoff reconciliation. can_execute is permanently false.';
