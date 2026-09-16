-- V17 Fantasy Score candidate research activation.
-- This is deliberately separate from active/promoted production authority.
-- A frozen candidate may be selected for immutable forward evidence without
-- becoming certified, publishable, rank eligible, or executable.

alter table public.wow_prop_fitted_model_artifacts
  add column if not exists candidate_research_active boolean not null default false;

alter table public.wow_prop_fitted_model_artifacts
  drop constraint if exists chk_wow_prop_candidate_research_lifecycle;
alter table public.wow_prop_fitted_model_artifacts
  add constraint chk_wow_prop_candidate_research_lifecycle
  check (
    candidate_research_active = false
    or (
      lifecycle_state in ('CANDIDATE','SHADOW')
      and promoted = false
      and active = false
      and probability_publishable = false
      and can_execute = false
    )
  );

create unique index if not exists uq_wow_prop_candidate_research_route
  on public.wow_prop_fitted_model_artifacts (
    upper(sport), upper(stat_type), feature_schema_version
  )
  where candidate_research_active = true;

comment on column public.wow_prop_fitted_model_artifacts.candidate_research_active is
  'Explicit evidence-only candidate selector. Never implies certified production active/promoted authority.';
