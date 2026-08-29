-- WOW MLB governed probability publication ratification ledger — 2026-08-28
--
-- Publication is a separate governance decision from model fitting, deployment,
-- and forward-shadow Calibration Health. This migration adds an immutable
-- append-only decision ledger and hardens wow_governed_deployment_state() so no
-- single flag can make the capability AVAILABLE.
--
-- IMPORTANT: this migration creates NO RATIFIED row and does not change the
-- existing MLB_EVENT_PROBABILITY runtime capability. Applying it therefore
-- keeps probability publication unavailable by construction.

create table if not exists public.wow_mlb_publication_ratification (
  ratification_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  spec_id uuid not null references public.wow_mlb_v2d_frozen_spec(spec_id),
  decision text not null check (decision in ('RATIFIED','REVOKED')),
  production_feature_ready boolean not null,
  probability_publishable boolean not null,
  calibration_health_assessed_at timestamptz,
  evidence jsonb not null default '{}'::jsonb,
  evidence_sha256 text not null check (length(evidence_sha256)=64),
  can_execute boolean not null default false check (can_execute=false),
  check (
    (decision='RATIFIED' and production_feature_ready=true and probability_publishable=true and calibration_health_assessed_at is not null)
    or
    (decision='REVOKED' and production_feature_ready=false and probability_publishable=false)
  )
);

alter table public.wow_mlb_publication_ratification enable row level security;

create index if not exists idx_wow_mlb_publication_ratification_spec_time
  on public.wow_mlb_publication_ratification(spec_id,created_at desc);

create or replace function public.wow_mlb_publication_ratification_immutable()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  raise exception 'wow_mlb_publication_ratification is immutable';
end;
$function$;

drop trigger if exists trg_wow_mlb_publication_ratification_immutable_upd
  on public.wow_mlb_publication_ratification;
create trigger trg_wow_mlb_publication_ratification_immutable_upd
  before update on public.wow_mlb_publication_ratification
  for each row execute function public.wow_mlb_publication_ratification_immutable();

drop trigger if exists trg_wow_mlb_publication_ratification_immutable_del
  on public.wow_mlb_publication_ratification;
create trigger trg_wow_mlb_publication_ratification_immutable_del
  before delete on public.wow_mlb_publication_ratification
  for each row execute function public.wow_mlb_publication_ratification_immutable();

create or replace function public.wow_governed_deployment_state()
returns jsonb
language sql
stable
set search_path to ''
as $function$
with gate_summary as (
  select
    count(*) as gate_count,
    count(*) filter (where status='PASS') as pass_count,
    coalesce(
      jsonb_agg(
        jsonb_build_object('id',gate_id,'status',status,'reason',reason)
        order by gate_id
      ),
      '[]'::jsonb
    ) as deployment_gates
  from public.wow_governed_deployment_gates
),
latest_health as (
  select spec_id,assessed_at,calibration_health_status
  from public.wow_mlb_v2d_calibration_health
  order by assessed_at desc
  limit 1
),
active_spec as (
  select fs.spec_id,fs.production_feature_ready as legacy_frozen_spec_production_feature_ready
  from public.wow_mlb_v2d_frozen_spec fs
  where fs.status='RESEARCH_FROZEN'
    and fs.spec_id=(select spec_id from latest_health)
  limit 1
),
runtime_capability as (
  select capability_status,updated_at,evidence
  from public.wow_runtime_capabilities
  where capability_key='MLB_EVENT_PROBABILITY'
  limit 1
),
latest_ratification as (
  select r.*
  from public.wow_mlb_publication_ratification r
  where r.spec_id=(select spec_id from active_spec)
  order by r.created_at desc,r.ratification_id desc
  limit 1
),
state as (
  select
    g.deployment_gates,
    (g.gate_count=11 and g.pass_count=11) as deployment_contract_pass,
    coalesce(h.calibration_health_status,'UNAVAILABLE') as calibration_health_status,
    h.assessed_at as calibration_health_assessed_at,
    coalesce(rc.capability_status,'UNAVAILABLE') as runtime_capability_status,
    rc.updated_at as runtime_capability_updated_at,
    coalesce(lr.decision,'NOT_RATIFIED') as ratification_status,
    lr.ratification_id,
    lr.created_at as ratification_created_at,
    coalesce(lr.production_feature_ready,false) as production_feature_ready,
    coalesce(lr.probability_publishable,false) as ratification_probability_publishable,
    coalesce(s.legacy_frozen_spec_production_feature_ready,false) as legacy_frozen_spec_production_feature_ready,
    (
      g.gate_count=11 and g.pass_count=11
      and coalesce(h.calibration_health_status,'UNAVAILABLE')='PASS'
      and coalesce(rc.capability_status,'UNAVAILABLE')='AVAILABLE'
      and coalesce(lr.decision,'NOT_RATIFIED')='RATIFIED'
      and coalesce(lr.production_feature_ready,false)
      and coalesce(lr.probability_publishable,false)
      and lr.calibration_health_assessed_at=h.assessed_at
    ) as publishable
  from gate_summary g
  left join latest_health h on true
  left join active_spec s on true
  left join runtime_capability rc on true
  left join latest_ratification lr on true
)
select jsonb_build_object(
  'governed_probability_capability',case when publishable then 'AVAILABLE' else 'UNAVAILABLE' end,
  'governed_probability_status',case when publishable then 'READY_FOR_PRODUCTION_GATE_REVIEW' else 'NOT_PRODUCED' end,
  'deployment_contract_status',case when deployment_contract_pass then 'PASS' else 'FAIL' end,
  'deployment_gates',deployment_gates,
  'calibration_health_status',calibration_health_status,
  'calibration_health_assessed_at',calibration_health_assessed_at,
  'runtime_capability_status',runtime_capability_status,
  'runtime_capability_updated_at',runtime_capability_updated_at,
  'ratification_status',ratification_status,
  'ratification_id',ratification_id,
  'ratification_created_at',ratification_created_at,
  'production_feature_ready',production_feature_ready,
  'legacy_frozen_spec_production_feature_ready',legacy_frozen_spec_production_feature_ready,
  'probability_publishable',publishable,
  'can_execute',false
)
from state;
$function$;
