-- WOW V17 postmortem / retrospective ledger.
-- Observability-first: preserve successes, isolate targeted wrinkles, and never
-- backfill post-event probabilities into governed calibration ledgers.

create table if not exists public.wow_postmortem_runs (
    postmortem_run_id uuid primary key,
    created_at timestamptz not null default now(),
    research_run_id text not null unique,
    slate_date date not null,
    source_type text not null,
    source_ref text,
    payload_hash text not null check (payload_hash ~ '^[0-9a-f]{64}$'),
    total_legs integer not null check (total_legs >= 1),
    wins integer not null check (wins >= 0),
    losses integer not null check (losses >= 0),
    pushes integer not null default 0 check (pushes >= 0),
    voids integer not null default 0 check (voids >= 0),
    positions_count integer not null check (positions_count >= 1),
    profitable_positions integer not null default 0 check (profitable_positions >= 0),
    non_losing_positions integer not null default 0 check (non_losing_positions >= 0),
    total_entry numeric not null default 0 check (total_entry >= 0),
    total_return numeric not null default 0 check (total_return >= 0),
    net_profit numeric not null default 0,
    roi numeric,
    matched_pregame_count integer not null default 0 check (matched_pregame_count >= 0),
    retrospective_count integer not null default 0 check (retrospective_count >= 0),
    calibration_eligible_count integer not null default 0 check (calibration_eligible_count >= 0),
    preserve_items jsonb not null default '[]'::jsonb check (jsonb_typeof(preserve_items) = 'array'),
    refine_items jsonb not null default '[]'::jsonb check (jsonb_typeof(refine_items) = 'array'),
    regression_checks jsonb not null default '[]'::jsonb check (jsonb_typeof(regression_checks) = 'array'),
    summary jsonb not null default '{}'::jsonb check (jsonb_typeof(summary) = 'object'),
    process_status text not null default 'COMPLETED'
        check (process_status in ('COMPLETED','COMPLETED_WITH_BLOCKERS')),
    can_execute boolean not null default false check (can_execute = false),
    constraint postmortem_row_reconciliation check (
        total_legs = wins + losses + pushes + voids
        and matched_pregame_count + retrospective_count = total_legs
        and calibration_eligible_count <= matched_pregame_count
    )
);

create table if not exists public.wow_postmortem_legs (
    postmortem_leg_id uuid primary key,
    postmortem_run_id uuid not null references public.wow_postmortem_runs(postmortem_run_id),
    created_at timestamptz not null default now(),
    row_key text not null,
    position_reference text not null,
    recommendation_record_id uuid references public.wow_recommendation_records(recommendation_record_id),
    sport text not null,
    league text,
    event_id text,
    participant text not null,
    opponent text,
    market text not null,
    side text not null,
    selection text not null,
    exact_line numeric,
    actual_stat numeric,
    official_result text,
    settled_result text not null check (settled_result in ('WIN','LOSS','PUSH','VOID')),
    hit boolean,
    margin_to_line numeric,
    margin_class text not null check (margin_class in (
        'BINARY_WINNER','BINARY_LOSS','NEAR_BOUNDARY','NARROW_CLEAR',
        'COMFORTABLE_CLEAR','MISS','LARGE_MISS','PUSH','VOID','NOT_APPLICABLE'
    )),
    observed_path text,
    observed_bf integer check (observed_bf is null or observed_bf >= 0),
    outs_after_top3 integer check (outs_after_top3 is null or outs_after_top3 between 0 and 3),
    top_order_reach_events integer check (top_order_reach_events is null or top_order_reach_events >= 0),
    bf_ge_5 boolean,
    bf_ge_6 boolean,
    pregame_bf_ge_5_probability numeric check (pregame_bf_ge_5_probability is null or (pregame_bf_ge_5_probability >= 0 and pregame_bf_ge_5_probability <= 1)),
    pregame_bf_ge_6_probability numeric check (pregame_bf_ge_6_probability is null or (pregame_bf_ge_6_probability >= 0 and pregame_bf_ge_6_probability <= 1)),
    tail_diagnostics jsonb not null default '{}'::jsonb check (jsonb_typeof(tail_diagnostics) = 'object'),
    governed_prediction_table text,
    governed_prediction_id uuid,
    raw_probability numeric check (raw_probability is null or (raw_probability > 0 and raw_probability < 1)),
    calibrated_probability numeric check (calibrated_probability is null or (calibrated_probability > 0 and calibrated_probability < 1)),
    lower_bound numeric check (lower_bound is null or (lower_bound > 0 and lower_bound < 1)),
    upper_bound numeric check (upper_bound is null or (upper_bound > 0 and upper_bound < 1)),
    failure_path_score numeric check (failure_path_score is null or (failure_path_score >= 0 and failure_path_score <= 1)),
    prediction_record_status text not null check (prediction_record_status in (
        'MATCHED_IMMUTABLE_PREGAME_RECORD',
        'NO_MATCHED_IMMUTABLE_PREGAME_RECORD_FOUND',
        'RETROSPECTIVE_RECOMMENDATION_RECORD'
    )),
    process_classification text not null,
    capture_timing text not null check (capture_timing in ('PREGAME','POST_EVENT_RETROACTIVE')),
    calibration_eligible boolean not null default false,
    excluded_from_calibration boolean not null default true,
    settlement_source text not null,
    settlement_evidence_ref text,
    can_execute boolean not null default false check (can_execute = false),
    unique (postmortem_run_id, row_key),
    constraint postmortem_leg_calibration_provenance check (
        calibration_eligible = false or (
            prediction_record_status = 'MATCHED_IMMUTABLE_PREGAME_RECORD'
            and capture_timing = 'PREGAME'
            and recommendation_record_id is not null
            and governed_prediction_id is not null
            and excluded_from_calibration = false
        )
    ),
    constraint postmortem_retro_probability_isolation check (
        capture_timing <> 'POST_EVENT_RETROACTIVE' or (
            calibration_eligible = false
            and excluded_from_calibration = true
            and raw_probability is null
            and calibrated_probability is null
            and lower_bound is null
            and upper_bound is null
            and failure_path_score is null
            and pregame_bf_ge_5_probability is null
            and pregame_bf_ge_6_probability is null
        )
    )
);

create index if not exists idx_wow_postmortem_legs_run_market
    on public.wow_postmortem_legs (postmortem_run_id, sport, market);
create index if not exists idx_wow_postmortem_legs_position
    on public.wow_postmortem_legs (postmortem_run_id, position_reference);
create index if not exists idx_wow_postmortem_legs_recommendation
    on public.wow_postmortem_legs (recommendation_record_id);

create table if not exists public.wow_postmortem_positions (
    postmortem_position_id uuid primary key,
    postmortem_run_id uuid not null references public.wow_postmortem_runs(postmortem_run_id),
    created_at timestamptz not null default now(),
    position_reference text not null,
    position_structure text not null,
    underlying_market_count integer not null check (underlying_market_count >= 1),
    all_or_nothing boolean not null default false,
    entry_cost numeric not null check (entry_cost >= 0),
    gross_return numeric not null check (gross_return >= 0),
    net_profit numeric not null,
    roi numeric,
    gross_multiplier numeric check (gross_multiplier is null or gross_multiplier >= 0),
    break_even_joint_probability numeric check (break_even_joint_probability is null or (break_even_joint_probability >= 0 and break_even_joint_probability <= 1)),
    leg_wins integer not null check (leg_wins >= 0),
    leg_losses integer not null check (leg_losses >= 0),
    leg_pushes integer not null default 0 check (leg_pushes >= 0),
    leg_voids integer not null default 0 check (leg_voids >= 0),
    leg_hit_rate numeric check (leg_hit_rate is null or (leg_hit_rate >= 0 and leg_hit_rate <= 1)),
    all_legs_hit boolean not null default false,
    capital_share numeric check (capital_share is null or (capital_share >= 0 and capital_share <= 1)),
    profit_contribution_share numeric,
    economics_status text not null default 'DIAGNOSTIC_ONLY' check (economics_status = 'DIAGNOSTIC_ONLY'),
    attribution_status text not null check (attribution_status in ('MATCHED_PREGAME_RECORD','RETROSPECTIVE_UNVERIFIED','MIXED')),
    excluded_from_calibration boolean not null default true,
    can_execute boolean not null default false check (can_execute = false),
    unique (postmortem_run_id, position_reference),
    constraint postmortem_position_leg_reconciliation check (
        underlying_market_count = leg_wins + leg_losses + leg_pushes + leg_voids
    ),
    constraint postmortem_position_break_even_scope check (
        (all_or_nothing = true and (break_even_joint_probability is null or gross_return > 0))
        or (all_or_nothing = false and break_even_joint_probability is null)
    )
);

create table if not exists public.wow_postmortem_patch_candidates (
    patch_candidate_id uuid primary key,
    postmortem_run_id uuid not null references public.wow_postmortem_runs(postmortem_run_id),
    created_at timestamptz not null default now(),
    patch_key text not null,
    scope text not null,
    change_class text not null check (change_class in ('DIAGNOSTIC_ONLY','TARGETED_MODEL','TARGETED_ECONOMICS','TARGETED_STRUCTURE')),
    proposed_change text not null,
    preserve_targets jsonb not null default '[]'::jsonb check (jsonb_typeof(preserve_targets) = 'array'),
    regression_checks jsonb not null default '[]'::jsonb check (jsonb_typeof(regression_checks) = 'array'),
    broad_tightening boolean not null default false,
    probability_change_allowed boolean not null default false,
    qualification_floor_change_allowed boolean not null default false,
    status text not null default 'PROPOSED' check (status in ('PROPOSED','REQUIRES_EXPLICIT_GOVERNANCE','IMPLEMENTED','REJECTED')),
    can_execute boolean not null default false check (can_execute = false),
    unique (postmortem_run_id, patch_key),
    constraint postmortem_broad_change_guard check (
        broad_tightening = false or status = 'REQUIRES_EXPLICIT_GOVERNANCE'
    )
);

create or replace function public.wow_block_postmortem_mutation()
returns trigger language plpgsql set search_path = '' as $$
begin
    raise exception 'WOW postmortem ledger rows are immutable';
end;
$$;

drop trigger if exists trg_wow_postmortem_runs_no_update on public.wow_postmortem_runs;
create trigger trg_wow_postmortem_runs_no_update before update on public.wow_postmortem_runs for each row execute function public.wow_block_postmortem_mutation();
drop trigger if exists trg_wow_postmortem_runs_no_delete on public.wow_postmortem_runs;
create trigger trg_wow_postmortem_runs_no_delete before delete on public.wow_postmortem_runs for each row execute function public.wow_block_postmortem_mutation();
drop trigger if exists trg_wow_postmortem_legs_no_update on public.wow_postmortem_legs;
create trigger trg_wow_postmortem_legs_no_update before update on public.wow_postmortem_legs for each row execute function public.wow_block_postmortem_mutation();
drop trigger if exists trg_wow_postmortem_legs_no_delete on public.wow_postmortem_legs;
create trigger trg_wow_postmortem_legs_no_delete before delete on public.wow_postmortem_legs for each row execute function public.wow_block_postmortem_mutation();
drop trigger if exists trg_wow_postmortem_positions_no_update on public.wow_postmortem_positions;
create trigger trg_wow_postmortem_positions_no_update before update on public.wow_postmortem_positions for each row execute function public.wow_block_postmortem_mutation();
drop trigger if exists trg_wow_postmortem_positions_no_delete on public.wow_postmortem_positions;
create trigger trg_wow_postmortem_positions_no_delete before delete on public.wow_postmortem_positions for each row execute function public.wow_block_postmortem_mutation();
drop trigger if exists trg_wow_postmortem_patch_candidates_no_update on public.wow_postmortem_patch_candidates;
create trigger trg_wow_postmortem_patch_candidates_no_update before update on public.wow_postmortem_patch_candidates for each row execute function public.wow_block_postmortem_mutation();
drop trigger if exists trg_wow_postmortem_patch_candidates_no_delete on public.wow_postmortem_patch_candidates;
create trigger trg_wow_postmortem_patch_candidates_no_delete before delete on public.wow_postmortem_patch_candidates for each row execute function public.wow_block_postmortem_mutation();

alter table public.wow_postmortem_runs enable row level security;
alter table public.wow_postmortem_legs enable row level security;
alter table public.wow_postmortem_positions enable row level security;
alter table public.wow_postmortem_patch_candidates enable row level security;
revoke all on public.wow_postmortem_runs from anon, authenticated;
revoke all on public.wow_postmortem_legs from anon, authenticated;
revoke all on public.wow_postmortem_positions from anon, authenticated;
revoke all on public.wow_postmortem_patch_candidates from anon, authenticated;

comment on table public.wow_postmortem_runs is 'Immutable V17 postmortem run ledger. Preserves successes and targeted refinements without changing governed probability.';
comment on table public.wow_postmortem_legs is 'Exact settled-leg retrospective diagnostics. Post-event rows cannot backfill governed probability or enter calibration.';
comment on table public.wow_postmortem_positions is 'Retrospective slip/card economics diagnostics. Sporting selection quality remains separate from payout efficiency.';
comment on table public.wow_postmortem_patch_candidates is 'Preserve-refine-regression-check proposals. Broad tightening requires explicit separate governance.';

create or replace function public.wow_record_postmortem_run(
    p_run jsonb,
    p_legs jsonb,
    p_positions jsonb,
    p_patches jsonb default '[]'::jsonb
)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    v_run_id uuid := (p_run->>'postmortem_run_id')::uuid;
    v_research_run_id text := p_run->>'research_run_id';
    v_payload_hash text := p_run->>'payload_hash';
    v_existing_hash text;
    v_legs integer := jsonb_array_length(p_legs);
    v_positions integer := jsonb_array_length(p_positions);
    v_patches integer := jsonb_array_length(p_patches);
    v_leg_count integer;
    v_position_count integer;
    v_patch_count integer;
begin
    if v_legs < 1 or v_legs > 200 then raise exception 'postmortem must contain 1..200 legs'; end if;
    if v_positions < 1 or v_positions > 100 then raise exception 'postmortem must contain 1..100 positions'; end if;

    select payload_hash into v_existing_hash from public.wow_postmortem_runs where research_run_id = v_research_run_id;
    if v_existing_hash is not null and v_existing_hash <> v_payload_hash then raise exception 'POSTMORTEM_IDEMPOTENCY_CONFLICT'; end if;

    insert into public.wow_postmortem_runs (
        postmortem_run_id,research_run_id,slate_date,source_type,source_ref,payload_hash,
        total_legs,wins,losses,pushes,voids,positions_count,profitable_positions,
        non_losing_positions,total_entry,total_return,net_profit,roi,matched_pregame_count,
        retrospective_count,calibration_eligible_count,preserve_items,refine_items,
        regression_checks,summary,process_status,can_execute
    ) values (
        v_run_id,v_research_run_id,(p_run->>'slate_date')::date,p_run->>'source_type',p_run->>'source_ref',v_payload_hash,
        (p_run->>'total_legs')::integer,(p_run->>'wins')::integer,(p_run->>'losses')::integer,
        coalesce((p_run->>'pushes')::integer,0),coalesce((p_run->>'voids')::integer,0),
        (p_run->>'positions_count')::integer,coalesce((p_run->>'profitable_positions')::integer,0),
        coalesce((p_run->>'non_losing_positions')::integer,0),coalesce((p_run->>'total_entry')::numeric,0),
        coalesce((p_run->>'total_return')::numeric,0),coalesce((p_run->>'net_profit')::numeric,0),
        nullif(p_run->>'roi','')::numeric,coalesce((p_run->>'matched_pregame_count')::integer,0),
        coalesce((p_run->>'retrospective_count')::integer,0),coalesce((p_run->>'calibration_eligible_count')::integer,0),
        coalesce(p_run->'preserve_items','[]'::jsonb),coalesce(p_run->'refine_items','[]'::jsonb),
        coalesce(p_run->'regression_checks','[]'::jsonb),coalesce(p_run->'summary','{}'::jsonb),
        coalesce(p_run->>'process_status','COMPLETED'),false
    ) on conflict (research_run_id) do nothing;

    insert into public.wow_postmortem_legs (
        postmortem_leg_id,postmortem_run_id,row_key,position_reference,recommendation_record_id,
        sport,league,event_id,participant,opponent,market,side,selection,exact_line,actual_stat,
        official_result,settled_result,hit,margin_to_line,margin_class,observed_path,observed_bf,
        outs_after_top3,top_order_reach_events,bf_ge_5,bf_ge_6,pregame_bf_ge_5_probability,
        pregame_bf_ge_6_probability,tail_diagnostics,governed_prediction_table,governed_prediction_id,
        raw_probability,calibrated_probability,lower_bound,upper_bound,failure_path_score,
        prediction_record_status,process_classification,capture_timing,calibration_eligible,
        excluded_from_calibration,settlement_source,settlement_evidence_ref,can_execute
    ) select
        (item->>'postmortem_leg_id')::uuid,v_run_id,item->>'row_key',item->>'position_reference',nullif(item->>'recommendation_record_id','')::uuid,
        item->>'sport',item->>'league',item->>'event_id',item->>'participant',item->>'opponent',item->>'market',item->>'side',item->>'selection',
        nullif(item->>'exact_line','')::numeric,nullif(item->>'actual_stat','')::numeric,item->>'official_result',item->>'settled_result',
        nullif(item->>'hit','')::boolean,nullif(item->>'margin_to_line','')::numeric,item->>'margin_class',item->>'observed_path',
        nullif(item->>'observed_bf','')::integer,nullif(item->>'outs_after_top3','')::integer,nullif(item->>'top_order_reach_events','')::integer,
        nullif(item->>'bf_ge_5','')::boolean,nullif(item->>'bf_ge_6','')::boolean,nullif(item->>'pregame_bf_ge_5_probability','')::numeric,
        nullif(item->>'pregame_bf_ge_6_probability','')::numeric,coalesce(item->'tail_diagnostics','{}'::jsonb),item->>'governed_prediction_table',
        nullif(item->>'governed_prediction_id','')::uuid,nullif(item->>'raw_probability','')::numeric,nullif(item->>'calibrated_probability','')::numeric,
        nullif(item->>'lower_bound','')::numeric,nullif(item->>'upper_bound','')::numeric,nullif(item->>'failure_path_score','')::numeric,
        item->>'prediction_record_status',item->>'process_classification',item->>'capture_timing',coalesce((item->>'calibration_eligible')::boolean,false),
        coalesce((item->>'excluded_from_calibration')::boolean,true),item->>'settlement_source',item->>'settlement_evidence_ref',false
    from jsonb_array_elements(p_legs) item on conflict (postmortem_run_id,row_key) do nothing;

    insert into public.wow_postmortem_positions (
        postmortem_position_id,postmortem_run_id,position_reference,position_structure,
        underlying_market_count,all_or_nothing,entry_cost,gross_return,net_profit,roi,gross_multiplier,
        break_even_joint_probability,leg_wins,leg_losses,leg_pushes,leg_voids,leg_hit_rate,all_legs_hit,
        capital_share,profit_contribution_share,economics_status,attribution_status,excluded_from_calibration,can_execute
    ) select
        (item->>'postmortem_position_id')::uuid,v_run_id,item->>'position_reference',item->>'position_structure',
        (item->>'underlying_market_count')::integer,coalesce((item->>'all_or_nothing')::boolean,false),(item->>'entry_cost')::numeric,
        (item->>'gross_return')::numeric,(item->>'net_profit')::numeric,nullif(item->>'roi','')::numeric,nullif(item->>'gross_multiplier','')::numeric,
        nullif(item->>'break_even_joint_probability','')::numeric,(item->>'leg_wins')::integer,(item->>'leg_losses')::integer,
        coalesce((item->>'leg_pushes')::integer,0),coalesce((item->>'leg_voids')::integer,0),nullif(item->>'leg_hit_rate','')::numeric,
        coalesce((item->>'all_legs_hit')::boolean,false),nullif(item->>'capital_share','')::numeric,nullif(item->>'profit_contribution_share','')::numeric,
        coalesce(item->>'economics_status','DIAGNOSTIC_ONLY'),item->>'attribution_status',coalesce((item->>'excluded_from_calibration')::boolean,true),false
    from jsonb_array_elements(p_positions) item on conflict (postmortem_run_id,position_reference) do nothing;

    insert into public.wow_postmortem_patch_candidates (
        patch_candidate_id,postmortem_run_id,patch_key,scope,change_class,proposed_change,preserve_targets,
        regression_checks,broad_tightening,probability_change_allowed,qualification_floor_change_allowed,status,can_execute
    ) select
        (item->>'patch_candidate_id')::uuid,v_run_id,item->>'patch_key',item->>'scope',item->>'change_class',item->>'proposed_change',
        coalesce(item->'preserve_targets','[]'::jsonb),coalesce(item->'regression_checks','[]'::jsonb),
        coalesce((item->>'broad_tightening')::boolean,false),coalesce((item->>'probability_change_allowed')::boolean,false),
        coalesce((item->>'qualification_floor_change_allowed')::boolean,false),coalesce(item->>'status','PROPOSED'),false
    from jsonb_array_elements(p_patches) item on conflict (postmortem_run_id,patch_key) do nothing;

    select count(*) into v_leg_count from public.wow_postmortem_legs where postmortem_run_id = v_run_id;
    select count(*) into v_position_count from public.wow_postmortem_positions where postmortem_run_id = v_run_id;
    select count(*) into v_patch_count from public.wow_postmortem_patch_candidates where postmortem_run_id = v_run_id;
    if v_leg_count <> v_legs or v_position_count <> v_positions or v_patch_count <> v_patches then raise exception 'POSTMORTEM_RECONCILIATION_FAILED'; end if;

    return jsonb_build_object(
        'postmortem_run_id',v_run_id,'research_run_id',v_research_run_id,
        'rows_in',v_legs,'rows_persisted',v_leg_count,
        'positions_in',v_positions,'positions_persisted',v_position_count,
        'patches_in',v_patches,'patches_persisted',v_patch_count,
        'reconciliation_pass',true,'can_execute',false
    );
end;
$$;

revoke all on function public.wow_record_postmortem_run(jsonb,jsonb,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.wow_record_postmortem_run(jsonb,jsonb,jsonb,jsonb) to service_role;
