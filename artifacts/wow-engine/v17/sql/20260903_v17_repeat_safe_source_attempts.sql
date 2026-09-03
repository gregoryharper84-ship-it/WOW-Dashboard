-- WOW V17 repeat-safe source-attempt hardening.
--
-- Live acceptance proved both the shared environmental provider and canonical
-- MLB event hydrator could collide with uq_wow_event_source_attempt_order by
-- hard-coding attempt_order=1. Preserve immutable history by allocating the next
-- per-event/per-evidence-kind attempt number. No evidence is overwritten and
-- can_execute remains false.

do $do$
declare d text;
begin
  select pg_get_functiondef('public.wow_v17_hydrate_shared_environmental_evidence(uuid,uuid)'::regprocedure) into d;
  d:=replace(
    d,
    $q$1,v_now,'ERROR',v_url,false$q$,
    $q$(select coalesce(max(a.attempt_order),0)+1 from public.wow_event_source_attempts a where a.event_prediction_id=p_event_prediction_id and a.evidence_kind='WEATHER_STATUS'),v_now,'ERROR',v_url,false$q$
  );
  d:=replace(
    d,
    $q$1,v_now,'UNAVAILABLE',v_url,false$q$,
    $q$(select coalesce(max(a.attempt_order),0)+1 from public.wow_event_source_attempts a where a.event_prediction_id=p_event_prediction_id and a.evidence_kind='WEATHER_STATUS'),v_now,'UNAVAILABLE',v_url,false$q$
  );
  d:=replace(
    d,
    $q$1,v_now,'SUCCESS',v_url,false$q$,
    $q$(select coalesce(max(a.attempt_order),0)+1 from public.wow_event_source_attempts a where a.event_prediction_id=p_event_prediction_id and a.evidence_kind='WEATHER_STATUS'),v_now,'SUCCESS',v_url,false$q$
  );
  execute d;
end;$do$;

do $do$
declare d text;
begin
  select pg_get_functiondef('public.wow_v17_hydrate_mlb_event_governance_evidence(uuid,uuid,jsonb,text)'::regprocedure) into d;
  d:=replace(
    d,
    $q$source_name,1,fetched_at,'SUCCESS',$q$,
    $q$source_name,(select coalesce(max(a.attempt_order),0)+1 from public.wow_event_source_attempts a where a.event_prediction_id=p_event_prediction_id and a.evidence_kind=k),fetched_at,'SUCCESS',$q$
  );
  execute d;
end;$do$;
