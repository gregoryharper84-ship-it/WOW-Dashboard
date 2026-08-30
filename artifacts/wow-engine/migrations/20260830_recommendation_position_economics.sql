-- Normalize portfolio economics to one immutable row per Kalshi position.
-- Outcome rows remain leg-level grading facts and must never be summed for P/L.

create table if not exists public.wow_recommendation_positions (
    position_reference text primary key,
    created_at timestamptz not null default now(),
    settled_at timestamptz not null,
    settlement_source text not null,
    settlement_evidence_ref text,
    position_structure text not null,
    underlying_market_count integer not null check (underlying_market_count > 0),
    recommendation_record_ids uuid[] not null,
    entry_cost numeric check (entry_cost is null or entry_cost >= 0),
    payout numeric check (payout is null or payout >= 0),
    profit_loss numeric,
    displayed_roi numeric,
    attribution_status text not null
        check (attribution_status in ('MATCHED_PREGAME_RECORD','RETROSPECTIVE_UNVERIFIED')),
    excluded_from_calibration boolean not null default false,
    can_execute boolean not null default false check (can_execute = false),
    constraint position_recommendation_count_matches
        check (cardinality(recommendation_record_ids) = underlying_market_count),
    constraint position_retrospective_excluded check (
        attribution_status <> 'RETROSPECTIVE_UNVERIFIED' or excluded_from_calibration = true
    )
);

create index if not exists idx_wow_recommendation_positions_ids
    on public.wow_recommendation_positions using gin (recommendation_record_ids);

create or replace function public.wow_block_recommendation_position_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception 'WOW recommendation positions are immutable';
end;
$$;

drop trigger if exists trg_wow_recommendation_positions_no_update on public.wow_recommendation_positions;
create trigger trg_wow_recommendation_positions_no_update
before update on public.wow_recommendation_positions
for each row execute function public.wow_block_recommendation_position_mutation();

drop trigger if exists trg_wow_recommendation_positions_no_delete on public.wow_recommendation_positions;
create trigger trg_wow_recommendation_positions_no_delete
before delete on public.wow_recommendation_positions
for each row execute function public.wow_block_recommendation_position_mutation();

alter table public.wow_recommendation_positions enable row level security;
revoke all on public.wow_recommendation_positions from anon, authenticated;

comment on table public.wow_recommendation_positions is
'One immutable portfolio/position economics row. Aggregate P/L only from this table, never from leg-level wow_recommendation_outcomes.';
comment on column public.wow_recommendation_outcomes.entry_cost is
'DEPRECATED_NON_AGGREGABLE: retained for compatibility; new writes leave null. Use wow_recommendation_positions.';
comment on column public.wow_recommendation_outcomes.payout is
'DEPRECATED_NON_AGGREGABLE: retained for compatibility; new writes leave null. Use wow_recommendation_positions.';
comment on column public.wow_recommendation_outcomes.profit_loss is
'DEPRECATED_NON_AGGREGABLE: retained for compatibility; new writes leave null. Use wow_recommendation_positions.';
comment on column public.wow_recommendation_outcomes.displayed_roi is
'DEPRECATED_NON_AGGREGABLE: retained for compatibility; new writes leave null. Use wow_recommendation_positions.';

create or replace function public.wow_settle_recommendation_batch(
    p_position jsonb,
    p_outcomes jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_expected integer := jsonb_array_length(p_outcomes);
    v_position_reference text := p_position->>'position_reference';
    v_persisted integer;
begin
    if v_expected < 1 or v_expected > 50 then
        raise exception 'settlement batch must contain 1..50 outcomes';
    end if;

    insert into public.wow_recommendation_positions (
        position_reference, settled_at, settlement_source,
        settlement_evidence_ref, position_structure,
        underlying_market_count, recommendation_record_ids,
        entry_cost, payout, profit_loss, displayed_roi,
        attribution_status, excluded_from_calibration, can_execute
    ) values (
        v_position_reference,
        (p_position->>'settled_at')::timestamptz,
        p_position->>'settlement_source',
        p_position->>'settlement_evidence_ref',
        p_position->>'position_structure',
        (p_position->>'underlying_market_count')::integer,
        array(select jsonb_array_elements_text(p_position->'recommendation_record_ids'))::uuid[],
        nullif(p_position->>'entry_cost','')::numeric,
        nullif(p_position->>'payout','')::numeric,
        nullif(p_position->>'profit_loss','')::numeric,
        nullif(p_position->>'displayed_roi','')::numeric,
        p_position->>'attribution_status',
        coalesce((p_position->>'excluded_from_calibration')::boolean, false),
        false
    )
    on conflict (position_reference) do nothing;

    insert into public.wow_recommendation_outcomes (
        recommendation_record_id, settled_at, settled_result,
        official_result, settlement_source, settlement_evidence_ref,
        position_reference, position_structure, underlying_market_count,
        attribution_status, excluded_from_calibration, can_execute
    )
    select
        (item->>'recommendation_record_id')::uuid,
        (item->>'settled_at')::timestamptz,
        item->>'settled_result',
        item->>'official_result',
        item->>'settlement_source',
        item->>'settlement_evidence_ref',
        v_position_reference,
        p_position->>'position_structure',
        (p_position->>'underlying_market_count')::integer,
        item->>'attribution_status',
        coalesce((item->>'excluded_from_calibration')::boolean, false),
        false
    from jsonb_array_elements(p_outcomes) item
    on conflict (recommendation_record_id) do nothing;

    select count(*) into v_persisted
    from public.wow_recommendation_outcomes
    where recommendation_record_id = any(
        array(select (jsonb_array_elements(p_position->'recommendation_record_ids') #>> '{}')::uuid)
    );

    return jsonb_build_object(
        'position_reference', v_position_reference,
        'rows_in', v_expected,
        'rows_persisted', v_persisted,
        'reconciliation_pass', v_persisted = v_expected,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_settle_recommendation_batch(jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.wow_settle_recommendation_batch(jsonb, jsonb) to service_role;
