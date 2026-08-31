-- WOW v16 Clean Core — outcome attribution semantics hardening
-- Research/validation only. This migration cannot execute wagers or market orders.
--
-- A pregame primary_failure_path is a model diagnostic hypothesis, not observed
-- postgame causation. Official settlement may establish HIT/MISS/PUSH/VOID, but a
-- MISS alone cannot prove which pregame failure mechanism caused the result.
-- Keep the diagnostic hypothesis on wow_predictions and store only an observed
-- settlement category in wow_outcomes.

create or replace function public.wow_normalize_prop_outcome_failure_semantics()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if upper(coalesce(new.official_result,'')) = 'MISS' then
    new.failure_category := 'MODEL_MISS';
  elsif upper(coalesce(new.official_result,'')) in ('HIT','PUSH') then
    new.failure_category := null;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_wow_outcomes_failure_semantics on public.wow_outcomes;
create trigger trg_wow_outcomes_failure_semantics
before insert on public.wow_outcomes
for each row execute function public.wow_normalize_prop_outcome_failure_semantics();

revoke all on function public.wow_normalize_prop_outcome_failure_semantics() from public, anon, authenticated;
grant execute on function public.wow_normalize_prop_outcome_failure_semantics() to service_role;
