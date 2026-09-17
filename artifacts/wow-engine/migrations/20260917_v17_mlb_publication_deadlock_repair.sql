-- Durable Supabase migration mirror for the V17 MLB publication-chain repair.
-- Canonical repair contract: v17/sql/20260917_v17_mlb_publication_deadlock_repair.sql
-- Idempotent by design because the production database was hot-fixed first.

do $repair$
declare
  fn_oid oid;
  ddl text;
  needle text := 'or not coalesce((deployment->>''probability_publishable'')::boolean,false)';
begin
  fn_oid := to_regprocedure(
    'public.wow_v17_hydrate_mlb_event_governance_evidence(uuid,uuid,jsonb,text)'
  )::oid;
  if fn_oid is null then
    raise exception 'V17_REPAIR_TARGET_MISSING: wow_v17_hydrate_mlb_event_governance_evidence';
  end if;

  ddl := pg_get_functiondef(fn_oid);
  if position(needle in ddl) > 0 then
    ddl := replace(ddl, needle, '');
    ddl := replace(
      ddl,
      'MLB_GOVERNED_DEPLOYMENT_NOT_PUBLISHABLE',
      'MLB_GOVERNED_DEPLOYMENT_CAPABILITY_UNAVAILABLE'
    );
    execute ddl;
  elsif position('MLB_GOVERNED_DEPLOYMENT_CAPABILITY_UNAVAILABLE' in ddl) > 0 then
    null;
  else
    raise exception 'V17_REPAIR_SIGNATURE_MISMATCH: expected publication deadlock clause not found';
  end if;
end
$repair$;

do $verify$
declare
  ddl text;
begin
  ddl := pg_get_functiondef(
    'public.wow_v17_hydrate_mlb_event_governance_evidence(uuid,uuid,jsonb,text)'::regprocedure
  );
  if position(
    'or not coalesce((deployment->>''probability_publishable'')::boolean,false)'
    in ddl
  ) > 0 then
    raise exception 'V17_REPAIR_VERIFY_FAILED: global publication dependency still present';
  end if;
  if position('governed_probability_capability' in ddl) = 0 then
    raise exception 'V17_REPAIR_VERIFY_FAILED: capability gate missing';
  end if;
  if position('can_execute' in ddl) = 0 then
    raise exception 'V17_REPAIR_VERIFY_FAILED: execution invariant missing';
  end if;
end
$verify$;
