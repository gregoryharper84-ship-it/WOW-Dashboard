-- Harden the internal NBA/WNBA certified-model registry lookup.
-- This function is service-internal model infrastructure, not a public API.
-- It does not promote or certify any model and cannot enable execution.

create or replace function public.wow_basketball_certified_model_artifact(p_sport text)
returns table(
  sport text,
  model_family text,
  model_artifact_version text,
  feature_schema_version text,
  artifact_format text,
  artifact_payload jsonb,
  artifact_checksum text
)
language sql
stable
security definer
set search_path=''
as $$
  select
    a.sport,
    a.model_family,
    a.model_artifact_version,
    a.feature_schema_version,
    a.artifact_format,
    a.artifact_payload,
    a.artifact_checksum
  from public.wow_basketball_team_event_model_artifacts a
  join public.wow_basketball_team_event_certification c
    on c.sport = a.sport
   and c.model_artifact_version = a.model_artifact_version
  where a.sport = upper(p_sport)
    and a.promoted
    and a.active
    and c.capability_status = 'CERTIFIED'
  order by a.certified_at desc nulls last, a.created_at desc
  limit 1;
$$;

revoke all on function public.wow_basketball_certified_model_artifact(text) from public;
revoke all on function public.wow_basketball_certified_model_artifact(text) from anon;
revoke all on function public.wow_basketball_certified_model_artifact(text) from authenticated;
grant execute on function public.wow_basketball_certified_model_artifact(text) to service_role;
