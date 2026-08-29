-- WOW v16 Clean Core — governed player-prop runtime contract.
-- Safe/fail-closed: creates evidence plumbing and an explicitly UNAVAILABLE
-- PROP_PROBABILITY lane. It does not enable probability publication or execution.

create table if not exists public.wow_prop_evidence_snapshots (
    source_snapshot_id uuid primary key default gen_random_uuid(),
    captured_at timestamptz not null default now(),
    event_id text not null,
    event_start_time timestamptz not null,
    sport text not null,
    player text not null,
    stat_type text not null,
    line numeric not null,
    game_log jsonb not null,
    box_score_log jsonb not null,
    role_status jsonb not null,
    role_timestamp timestamptz not null,
    opportunity_ledger jsonb not null,
    source_timestamps jsonb not null default '{}'::jsonb,
    hydration_status text not null,
    blockers text[] not null default '{}'::text[],
    evidence_version text not null default 'PROP_EVIDENCE_V1',
    can_execute boolean not null default false,
    constraint wow_prop_evidence_game_log_array check (jsonb_typeof(game_log) = 'array'),
    constraint wow_prop_evidence_box_log_array check (jsonb_typeof(box_score_log) = 'array'),
    constraint wow_prop_evidence_role_object check (jsonb_typeof(role_status) = 'object'),
    constraint wow_prop_evidence_opportunity_object check (jsonb_typeof(opportunity_ledger) = 'object'),
    constraint wow_prop_evidence_source_ts_object check (jsonb_typeof(source_timestamps) = 'object'),
    constraint wow_prop_evidence_hydration_status check (hydration_status in ('PASS','INCOMPLETE','FAILED')),
    constraint wow_prop_evidence_never_execute check (can_execute = false)
);

create index if not exists wow_prop_evidence_lookup_idx
    on public.wow_prop_evidence_snapshots (sport, event_id, player, stat_type, captured_at desc);

-- The evidence ledger is backend-only. Render uses the Supabase service-role
-- credential, which bypasses ordinary RLS. anon/authenticated clients must not
-- be able to read, insert, mutate, or delete governed evidence snapshots.
alter table public.wow_prop_evidence_snapshots enable row level security;
revoke all on table public.wow_prop_evidence_snapshots from anon, authenticated;
grant all on table public.wow_prop_evidence_snapshots to service_role;

create or replace function public.wow_prop_evidence_snapshot(
    p_source_snapshot_id uuid,
    p_event_id text,
    p_sport text,
    p_player text,
    p_stat_type text,
    p_line numeric
) returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
    v_row public.wow_prop_evidence_snapshots%rowtype;
    v_game_count integer;
    v_box_count integer;
    v_game_numeric boolean;
    v_box_objects boolean;
    v_role_status text;
    v_opportunity_status text;
begin
    select * into v_row
    from public.wow_prop_evidence_snapshots
    where source_snapshot_id = p_source_snapshot_id;

    if not found then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_EVIDENCE_SNAPSHOT_NOT_FOUND',
            'hydration_status', 'FAILED',
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    if v_row.event_id <> p_event_id
       or upper(v_row.sport) <> upper(p_sport)
       or v_row.player <> p_player
       or upper(v_row.stat_type) <> upper(p_stat_type)
       or v_row.line <> p_line then
        return jsonb_build_object(
            'ok', false,
            'code', 'PROP_EVIDENCE_IDENTITY_MISMATCH',
            'hydration_status', 'FAILED',
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    select count(*) into v_game_count from jsonb_array_elements(v_row.game_log);
    select count(*) into v_box_count from jsonb_array_elements(v_row.box_score_log);

    select coalesce(bool_and(jsonb_typeof(value) = 'number'), false)
      into v_game_numeric
      from jsonb_array_elements(v_row.game_log);

    select coalesce(bool_and(jsonb_typeof(value) = 'object'), false)
      into v_box_objects
      from jsonb_array_elements(v_row.box_score_log);

    v_role_status := coalesce(v_row.role_status->>'status', v_row.role_status->>'role', '');
    v_opportunity_status := coalesce(v_row.opportunity_ledger->>'status', v_row.opportunity_ledger->>'gate_label', '');

    if v_row.hydration_status <> 'PASS'
       or v_game_count < 10
       or v_box_count < 10
       or not v_game_numeric
       or not v_box_objects
       or v_row.role_timestamp is null
       or v_role_status = ''
       or upper(v_opportunity_status) not in ('PASS','COMPLETE','READY')
       or cardinality(v_row.blockers) > 0 then
        return jsonb_build_object(
            'ok', false,
            'code', 'RUN_INVALID_ACQUISITION_INCOMPLETE',
            'hydration_status', v_row.hydration_status,
            'game_log_count', v_game_count,
            'box_score_log_count', v_box_count,
            'game_log_numeric', v_game_numeric,
            'box_score_log_objects', v_box_objects,
            'role_timestamp', v_row.role_timestamp,
            'blockers', to_jsonb(v_row.blockers),
            'probability_publishable', false,
            'can_execute', false
        );
    end if;

    return jsonb_build_object(
        'ok', true,
        'code', 'PROP_EVIDENCE_READY',
        'source_snapshot_id', v_row.source_snapshot_id,
        'captured_at', v_row.captured_at,
        'event_id', v_row.event_id,
        'event_start_time', v_row.event_start_time,
        'sport', v_row.sport,
        'player', v_row.player,
        'stat_type', v_row.stat_type,
        'line', v_row.line,
        'game_log', v_row.game_log,
        'box_score_log', v_row.box_score_log,
        'role_status', v_row.role_status,
        'role_timestamp', v_row.role_timestamp,
        'opportunity_ledger', v_row.opportunity_ledger,
        'source_timestamps', v_row.source_timestamps,
        'evidence_version', v_row.evidence_version,
        'hydration_status', v_row.hydration_status,
        'probability_publishable', false,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_prop_evidence_snapshot(uuid,text,text,text,text,numeric) from public, anon, authenticated;
grant execute on function public.wow_prop_evidence_snapshot(uuid,text,text,text,text,numeric) to service_role;

insert into public.wow_runtime_capabilities (
    capability_key, capability_status, evidence, can_execute, updated_at
) values (
    'PROP_PROBABILITY',
    'UNAVAILABLE',
    jsonb_build_object(
        'reason', 'GENERIC_PROP_FITTED_PROVIDER_UNAVAILABLE',
        'architecture', 'WOW_BETTING_ENGINE->RENDER->SUPABASE->CONTROLLING_SPECIALIST->WOW_PREDICTIONS',
        'llp_player_props_allowed', false,
        'evidence_snapshot_contract', 'PROP_EVIDENCE_V1'
    ),
    false,
    now()
)
on conflict (capability_key) do update set
    capability_status = 'UNAVAILABLE',
    evidence = excluded.evidence,
    can_execute = false,
    updated_at = now();
