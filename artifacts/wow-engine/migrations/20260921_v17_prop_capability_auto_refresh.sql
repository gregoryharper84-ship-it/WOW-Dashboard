-- WOW V17 prop capability ledger auto-refresh — 2026-09-21
--
-- The exact artifact registry is authoritative for sport/stat model capability.
-- A promoted NFL artifact set existed while PROP_PROBABILITY.available_scopes
-- still reflected an earlier MLB+WNBA refresh. Make the summary self-healing so
-- future promotions/demotions cannot leave one sport invisible in diagnostics.
--
-- This trigger never changes an artifact, model probability, calibration,
-- threshold, rank decision, terminal state, or execution authority. It only
-- rebuilds the derived runtime-capability summary from exact active/promoted
-- artifact truth. can_execute=false remains invariant.

create or replace function public.wow_v17_prop_artifact_capability_refresh_trigger()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    perform public.wow_v17_refresh_prop_probability_capability();
    return null;
end;
$$;

revoke all on function public.wow_v17_prop_artifact_capability_refresh_trigger()
from public, anon, authenticated;

-- Statement-level is deliberate: bulk artifact promotion should refresh once,
-- not once per row.
drop trigger if exists wow_v17_prop_artifact_capability_refresh
on public.wow_prop_fitted_model_artifacts;

create trigger wow_v17_prop_artifact_capability_refresh
after insert or update or delete
on public.wow_prop_fitted_model_artifacts
for each statement
execute function public.wow_v17_prop_artifact_capability_refresh_trigger();

select public.wow_v17_refresh_prop_probability_capability();
