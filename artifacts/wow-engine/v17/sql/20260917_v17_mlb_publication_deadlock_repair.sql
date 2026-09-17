-- WOW V17 MLB publication-chain deadlock repair — 2026-09-17
--
-- The event-evidence hydrator previously required the deployment-level
-- probability_publishable flag to already be true before it would hydrate the
-- canonical evidence consumed by probability audit / event governor.  That is
-- circular: row publication is intentionally a downstream result of those gates.
--
-- Preserve the capability gate and every row-level fail-closed gate. Remove only
-- the deployment publication prerequisite from the evidence-hydration stage.
-- can_execute remains false throughout.

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
    null; -- idempotent: already repaired
  else
    raise exception 'V17_REPAIR_SIGNATURE_MISMATCH: expected publication deadlock clause not found';
  end if;
end
$repair$;

-- Migration-level verification: fail closed if the circular dependency survived
-- or if the underlying model-capability / execution invariants disappeared.
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
