-- WOW V17 MLB 1st-Inning Batters Faced forward-shadow ledger
-- Research-only. No probability publication, ranking, money lane, or execution.

create table if not exists public.wow_mlb_1ip_bf_shadow_predictions (
    prediction_id uuid primary key default gen_random_uuid(),
    official_event_id text not null,
    event_start_time timestamptz not null,
    pitcher_id bigint not null,
    pitcher_name text not null,
    model_family text not null,
    artifact_checksum text not null,
    recent_history_n integer not null check (recent_history_n between 0 and 10),
    p_bf_3 double precision not null check (p_bf_3 between 0 and 1),
    p_bf_4 double precision not null check (p_bf_4 between 0 and 1),
    p_bf_ge_5 double precision not null check (p_bf_ge_5 between 0 and 1),
    p_more_3_5 double precision not null check (p_more_3_5 between 0 and 1),
    p_more_4_5 double precision not null check (p_more_4_5 between 0 and 1),
    prediction_timestamp timestamptz not null,
    source_timestamp timestamptz not null,
    source_provider text not null default 'MLB_STATS_API_OFFICIAL_V1',
    source_snapshot jsonb not null default '{}'::jsonb,
    shadow_status text not null default 'FROZEN_PREGAME',
    probability_publishable boolean not null default false check (probability_publishable = false),
    rank_eligible boolean not null default false check (rank_eligible = false),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    constraint wow_mlb_1ip_bf_shadow_prob_normalized check (
      abs((p_bf_3 + p_bf_4 + p_bf_ge_5) - 1.0) <= 0.000001
    ),
    constraint wow_mlb_1ip_bf_shadow_p35_identity check (
      abs(p_more_3_5 - (p_bf_4 + p_bf_ge_5)) <= 0.000001
    ),
    constraint wow_mlb_1ip_bf_shadow_p45_identity check (
      abs(p_more_4_5 - p_bf_ge_5) <= 0.000001
    ),
    constraint wow_mlb_1ip_bf_shadow_pregame check (prediction_timestamp < event_start_time),
    unique (artifact_checksum, official_event_id, pitcher_id)
);

create index if not exists wow_mlb_1ip_bf_shadow_predictions_start_idx
  on public.wow_mlb_1ip_bf_shadow_predictions(event_start_time);
create index if not exists wow_mlb_1ip_bf_shadow_predictions_artifact_idx
  on public.wow_mlb_1ip_bf_shadow_predictions(artifact_checksum, prediction_timestamp);

create table if not exists public.wow_mlb_1ip_bf_shadow_outcomes (
    outcome_id uuid primary key default gen_random_uuid(),
    prediction_id uuid not null unique references public.wow_mlb_1ip_bf_shadow_predictions(prediction_id),
    disposition text not null check (disposition in ('GRADED','STARTER_CHANGED','CANCELED','POSTPONED','NO_OFFICIAL_BF','UNRESOLVED')),
    actual_bf integer,
    hit_more_3_5 boolean,
    hit_more_4_5 boolean,
    brier_3_5 double precision,
    log_loss_3_5 double precision,
    brier_4_5 double precision,
    log_loss_4_5 double precision,
    settlement_source text not null,
    outcome_timestamp timestamptz not null,
    settlement_payload jsonb not null default '{}'::jsonb,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    constraint wow_mlb_1ip_bf_shadow_graded_shape check (
      (disposition <> 'GRADED') or
      (actual_bf is not null and actual_bf >= 1 and hit_more_3_5 is not null and hit_more_4_5 is not null
       and brier_3_5 is not null and log_loss_3_5 is not null
       and brier_4_5 is not null and log_loss_4_5 is not null)
    )
);

create index if not exists wow_mlb_1ip_bf_shadow_outcomes_disposition_idx
  on public.wow_mlb_1ip_bf_shadow_outcomes(disposition, outcome_timestamp);

create or replace function public.wow_mlb_1ip_bf_shadow_immutable_guard()
returns trigger
language plpgsql
set search_path = ''
as $function$
begin
  raise exception 'WOW_MLB_1IP_BF_SHADOW_IMMUTABLE';
end;
$function$;

drop trigger if exists wow_mlb_1ip_bf_shadow_predictions_immutable
  on public.wow_mlb_1ip_bf_shadow_predictions;
create trigger wow_mlb_1ip_bf_shadow_predictions_immutable
before update or delete on public.wow_mlb_1ip_bf_shadow_predictions
for each row execute function public.wow_mlb_1ip_bf_shadow_immutable_guard();

drop trigger if exists wow_mlb_1ip_bf_shadow_outcomes_immutable
  on public.wow_mlb_1ip_bf_shadow_outcomes;
create trigger wow_mlb_1ip_bf_shadow_outcomes_immutable
before update or delete on public.wow_mlb_1ip_bf_shadow_outcomes
for each row execute function public.wow_mlb_1ip_bf_shadow_immutable_guard();

alter table public.wow_mlb_1ip_bf_shadow_predictions enable row level security;
alter table public.wow_mlb_1ip_bf_shadow_outcomes enable row level security;

-- No anon/authenticated policies are created. Backend service-role access only.

create or replace function public.wow_mlb_1ip_bf_shadow_health(p_artifact_checksum text)
returns jsonb
language sql
stable
set search_path = ''
as $function$
with graded as (
  select
    p.prediction_id,
    p.p_more_3_5,
    p.p_more_4_5,
    case when o.hit_more_3_5 then 1.0 else 0.0 end as y35,
    case when o.hit_more_4_5 then 1.0 else 0.0 end as y45,
    o.brier_3_5,
    o.log_loss_3_5,
    o.brier_4_5,
    o.log_loss_4_5
  from public.wow_mlb_1ip_bf_shadow_predictions p
  join public.wow_mlb_1ip_bf_shadow_outcomes o using (prediction_id)
  where p.artifact_checksum = p_artifact_checksum
    and o.disposition = 'GRADED'
),
bins35 as (
  select
    least(9, floor(p_more_3_5 * 10)::int) as bin,
    count(*)::double precision as n,
    avg(p_more_3_5) as mean_p,
    avg(y35) as mean_y
  from graded group by 1
),
bins45 as (
  select
    least(9, floor(p_more_4_5 * 10)::int) as bin,
    count(*)::double precision as n,
    avg(p_more_4_5) as mean_p,
    avg(y45) as mean_y
  from graded group by 1
),
summary as (
  select
    count(*)::integer as n,
    avg(p_more_3_5) as mean_p35,
    avg(y35) as hit35,
    avg(brier_3_5) as brier35,
    avg(log_loss_3_5) as logloss35,
    avg(p_more_4_5) as mean_p45,
    avg(y45) as hit45,
    avg(brier_4_5) as brier45,
    avg(log_loss_4_5) as logloss45
  from graded
),
ece as (
  select
    coalesce((select sum(b.n / nullif(s.n,0) * abs(b.mean_p - b.mean_y)) from bins35 b), 0) as ece35,
    coalesce((select sum(b.n / nullif(s.n,0) * abs(b.mean_p - b.mean_y)) from bins45 b), 0) as ece45
  from summary s
),
counts as (
  select
    count(*)::integer as predictions,
    count(*) filter (where o.prediction_id is not null)::integer as settled,
    count(*) filter (where o.disposition = 'GRADED')::integer as graded,
    count(*) filter (where o.disposition is not null and o.disposition <> 'GRADED')::integer as excluded
  from public.wow_mlb_1ip_bf_shadow_predictions p
  left join public.wow_mlb_1ip_bf_shadow_outcomes o using (prediction_id)
  where p.artifact_checksum = p_artifact_checksum
)
select jsonb_build_object(
  'artifact_checksum', p_artifact_checksum,
  'predictions', c.predictions,
  'settled', c.settled,
  'graded', c.graded,
  'excluded', c.excluded,
  'minimum_overall_forward_n', 100,
  'forward_sample_complete', (c.graded >= 100),
  'line_3_5_more', jsonb_build_object(
    'n', s.n,
    'mean_probability', s.mean_p35,
    'observed_hit_rate', s.hit35,
    'calibration_bias', s.mean_p35 - s.hit35,
    'brier', s.brier35,
    'log_loss', s.logloss35,
    'ece', e.ece35
  ),
  'line_4_5_more', jsonb_build_object(
    'n', s.n,
    'mean_probability', s.mean_p45,
    'observed_hit_rate', s.hit45,
    'calibration_bias', s.mean_p45 - s.hit45,
    'brier', s.brier45,
    'log_loss', s.logloss45,
    'ece', e.ece45
  ),
  'probability_publishable', false,
  'can_execute', false
)
from summary s cross join ece e cross join counts c;
$function$;
