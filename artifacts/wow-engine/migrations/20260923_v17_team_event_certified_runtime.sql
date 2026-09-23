-- V17 generic team/event certified-artifact registry.
-- Candidate rows remain immutable CANDIDATE-only evidence. A certified artifact is
-- a separate immutable release receipt; the mutable route pointer is the only
-- activation surface and can never grant execution authority.

create table if not exists public.wow_team_event_certified_artifacts (
  certified_artifact_id uuid primary key default gen_random_uuid(),
  certified_at timestamptz not null default now(),
  sport text not null,
  league text not null,
  market_family text not null default 'OUTRIGHT_WINNER',
  model_family text not null,
  model_artifact_version text not null unique,
  candidate_id uuid not null references public.wow_d1_candidate_artifacts(candidate_id),
  feature_schema_version text not null,
  source_policy_id text not null,
  training_dataset_hash text not null check (training_dataset_hash ~ '^[0-9a-f]{64}$'),
  training_code_sha text not null check (training_code_sha ~ '^[0-9a-f]{7,64}$'),
  artifact_checksum text not null check (artifact_checksum ~ '^[0-9a-f]{64}$'),
  artifact_payload jsonb not null check (jsonb_typeof(artifact_payload) = 'object'),
  calibrator_payload jsonb not null check (jsonb_typeof(calibrator_payload) = 'object'),
  validation_metrics jsonb not null check (jsonb_typeof(validation_metrics) = 'object'),
  source_review_receipt jsonb not null check (jsonb_typeof(source_review_receipt) = 'object'),
  certification_replay_receipt jsonb not null check (jsonb_typeof(certification_replay_receipt) = 'object'),
  certification_code_sha text not null check (certification_code_sha ~ '^[0-9a-f]{7,64}$'),
  lifecycle_state text not null default 'PROSPECTIVE_CERTIFIED'
    check (lifecycle_state in ('PROSPECTIVE_CERTIFIED','CHAMPION')),
  promoted boolean not null default true check (promoted = true),
  probability_publishable boolean not null default true check (probability_publishable = true),
  can_execute boolean not null default false check (can_execute = false),
  unique (sport, league, model_family, model_artifact_version)
);

create table if not exists public.wow_team_event_certified_routes (
  sport text not null,
  league text not null,
  model_family text not null,
  model_artifact_version text not null references public.wow_team_event_certified_artifacts(model_artifact_version),
  activated_at timestamptz not null default now(),
  activation_receipt jsonb not null default '{}'::jsonb check (jsonb_typeof(activation_receipt) = 'object'),
  probability_publishable boolean not null default true check (probability_publishable = true),
  can_execute boolean not null default false check (can_execute = false),
  primary key (sport, league, model_family)
);

create index if not exists wow_team_event_certified_artifacts_lane_idx
  on public.wow_team_event_certified_artifacts (sport, league, model_family, certified_at desc);

alter table public.wow_team_event_certified_artifacts enable row level security;
alter table public.wow_team_event_certified_routes enable row level security;

revoke all on public.wow_team_event_certified_artifacts from public, anon, authenticated;
revoke all on public.wow_team_event_certified_routes from public, anon, authenticated;
grant select, insert on public.wow_team_event_certified_artifacts to service_role;
grant select, insert, update on public.wow_team_event_certified_routes to service_role;

create trigger wow_team_event_certified_artifacts_immutable
before update or delete on public.wow_team_event_certified_artifacts
for each row execute function public.wow_reject_immutable_mutation();

create or replace function public.wow_resolve_team_event_certified_artifact(
  p_sport text,
  p_league text,
  p_model_family text default null
)
returns table (
  sport text,
  league text,
  market_family text,
  model_family text,
  model_artifact_version text,
  feature_schema_version text,
  artifact_payload jsonb,
  calibrator_payload jsonb,
  validation_metrics jsonb,
  source_review_receipt jsonb,
  certification_replay_receipt jsonb,
  lifecycle_state text,
  probability_publishable boolean,
  can_execute boolean
)
language sql
security definer
set search_path = public
as $$
  select a.sport, a.league, a.market_family, a.model_family,
         a.model_artifact_version, a.feature_schema_version,
         a.artifact_payload, a.calibrator_payload, a.validation_metrics,
         a.source_review_receipt, a.certification_replay_receipt,
         a.lifecycle_state, a.probability_publishable, a.can_execute
    from public.wow_team_event_certified_routes r
    join public.wow_team_event_certified_artifacts a
      on a.model_artifact_version = r.model_artifact_version
   where upper(r.sport) = upper(p_sport)
     and upper(r.league) = upper(p_league)
     and (p_model_family is null or upper(r.model_family) = upper(p_model_family))
     and r.probability_publishable = true
     and r.can_execute = false
     and a.probability_publishable = true
     and a.can_execute = false
   order by r.activated_at desc
   limit 1;
$$;

revoke all on function public.wow_resolve_team_event_certified_artifact(text,text,text) from public, anon, authenticated;
grant execute on function public.wow_resolve_team_event_certified_artifact(text,text,text) to service_role;
