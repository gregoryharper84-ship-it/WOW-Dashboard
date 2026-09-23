-- WOW V17 Class B reliability repair: canonicalize external pregame status aliases
-- before the strict wow_event_predictions event_status check constraint runs.
--
-- Root cause reproduced 2026-09-22: MLB forward-shadow evidence can carry
-- PRE-GAME while the governed prediction ledger intentionally accepts only the
-- canonical status vocabulary (SCHEDULED, DELAYED, POSTPONED, CANCELED, STARTED,
-- FINAL). The governance bridge therefore failed before terminal governance was
-- persisted. Keep the constraint strict and normalize only equivalent pregame
-- aliases at the existing governance-sync boundary.
--
-- No sporting-model math, probability, calibration, terminal-authority, or
-- execution semantics are changed. can_execute remains false downstream.

create or replace function public.wow_sync_event_governance_fields()
returns trigger
language plpgsql
set search_path to ''
as $function$
declare
  v_specialist text;
  v_capability text;
begin
  if upper(coalesce(new.event_status,'')) in ('PRE-GAME','PREGAME') then
    new.event_status := 'SCHEDULED';
  end if;

  select controlling_specialist into v_specialist
  from public.wow_specialist_registry
  where sport=new.sport and market_family=new.market_family and active=true;
  if v_specialist is null then
    raise exception 'no active controlling specialist registered for %/%',new.sport,new.market_family;
  end if;

  select capability_status into v_capability
  from public.wow_runtime_capabilities
  where capability_key='MLB_EVENT_PROBABILITY';
  if v_capability is null then v_capability := 'UNAVAILABLE'; end if;

  new.controlling_specialist := v_specialist;
  new.governed_probability_capability := v_capability;
  if v_capability <> 'AVAILABLE' then
    new.rank_eligible := false;
    if new.rank_eligibility_status='PASS' then new.rank_eligibility_status := 'FAIL'; end if;
    new.probability_publishable := false;
  end if;
  return new;
end;
$function$;
