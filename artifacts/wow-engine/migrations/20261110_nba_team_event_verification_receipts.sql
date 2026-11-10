-- Read-only schema-ready attestation for the canonical V17 independent
-- verification ledger, used by the Render pre-deploy gate.
--
-- This migration deliberately creates NO receipt table. The canonical ledger is
-- public.wow_independent_verification_receipts, created by
-- 20261110_v17_independent_verification_receipts.sql, and the pre-fit verifier
-- wiring probes exactly that name. A second, differently-named ledger would
-- satisfy neither that probe nor the fit that writes into it, and two ledgers
-- would let a run appear verified against one schema while archiving to
-- another.
--
-- PROPOSED / DO NOT APPLY before independent protected schema review.
-- Evidence only: certification, promotion and execution stay untouched.

create or replace function public.wow_nba_team_event_verification_receipt_schema_ready()
returns jsonb
language plpgsql stable security invoker set search_path = ''
as $$
declare
  v_table oid := pg_catalog.to_regclass('public.wow_independent_verification_receipts');
  v_calibrators oid := pg_catalog.to_regclass('public.wow_basketball_team_event_calibrators');
  v_columns text[] := array[
    'receipt_id','recorded_at','receipt_digest','schema_version','harness',
    'code_sha','environment','sport','league_scope','model_family',
    'model_version','model_artifact_id','model_artifact_sha256',
    'calibration_artifact_id','calibration_sha256','feature_schema_version',
    'input_contract_version','independent_verifier','implementation_owner',
    'verification_owner','independent_verification_status','publishable_scope',
    'certification_eligible','failed_checks','blockers','receipt','can_execute'
  ];
  v_ready boolean := false;
begin
  if v_table is not null and v_calibrators is not null then
    select
      c.relrowsecurity
      -- Exact column contract: the total count and the named subset must agree,
      -- so a renamed or substituted column fails rather than passing on count.
      and (select count(*) from pg_catalog.pg_attribute a
           where a.attrelid = v_table and a.attnum > 0 and not a.attisdropped) = 27
      and (select count(*) from pg_catalog.pg_attribute a
           where a.attrelid = v_table and a.attnum > 0 and not a.attisdropped
             and a.attname = any(v_columns)) = 27
      and (select count(*) from pg_catalog.pg_constraint x
           where x.conrelid = v_table and x.convalidated
             and x.conname = any(array[
               'wow_ivr_owner_independence','wow_ivr_pass_is_clean','wow_ivr_fail_not_eligible'
             ])) = 3
      -- The three wow_ivr_* business-rule checks alone do NOT guarantee the
      -- hard non-execution invariants. Attest the two independent CHECK
      -- expressions, not merely their names, before admitting a deployment.
      and (select count(*) from pg_catalog.pg_constraint x
           where x.conrelid = v_table and x.convalidated and x.contype = 'c'
             and (
               (x.conname = 'wow_independent_verification_receipts_can_execute_check'
                and pg_catalog.regexp_replace(
                  pg_catalog.pg_get_expr(x.conbin, x.conrelid),
                  '[[:space:]()]', '', 'g') = 'can_execute=false')
               or
               (x.conname = 'wow_independent_verification_receipts_publishable_scope_check'
                and pg_catalog.regexp_replace(
                  pg_catalog.pg_get_expr(x.conbin, x.conrelid),
                  '[[:space:]()]', '', 'g') = any(array[
                    'publishable_scope=''NONE''::text',
                    'publishable_scope=''NONE'''
                  ]))
             )) = 2
      and exists (select 1 from pg_catalog.pg_trigger t
                  where t.tgrelid = v_table
                    and t.tgname = 'wow_independent_verification_receipts_immutable'
                    and not t.tgisinternal and t.tgenabled = 'O'
                    and (t.tgtype::int & 27) = 27
                    and t.tgfoid = pg_catalog.to_regprocedure('public.wow_reject_immutable_mutation()'))
      and exists (select 1 from pg_catalog.pg_trigger t
                  where t.tgrelid = v_table
                    and t.tgname = 'wow_independent_verification_receipts_no_truncate'
                    and not t.tgisinternal and t.tgenabled = 'O'
                    -- BEFORE TRUNCATE FOR EACH STATEMENT is tgtype=2|32.
                    -- Matching a function name on another event is insufficient.
                    and t.tgtype::int = 34
                    and t.tgfoid = pg_catalog.to_regprocedure('public.wow_independent_verification_receipts_no_truncate()'))
      and pg_catalog.has_table_privilege('service_role', v_table, 'SELECT')
      and pg_catalog.has_table_privilege('service_role', v_table, 'INSERT')
      and not pg_catalog.has_table_privilege('service_role', v_table, 'UPDATE')
      and not pg_catalog.has_table_privilege('service_role', v_table, 'DELETE')
      and not pg_catalog.has_table_privilege('service_role', v_table, 'TRUNCATE')
      and not pg_catalog.has_table_privilege('anon', v_table, 'SELECT')
      and not pg_catalog.has_table_privilege('authenticated', v_table, 'SELECT')
      -- V7/V10 cannot verify a calibrator it cannot identify, so the additive
      -- evidence columns are part of the same contract.
      and (select count(*) from pg_catalog.pg_attribute a
           where a.attrelid = v_calibrators and a.attnum > 0 and not a.attisdropped
             and a.attname = any(array['calibration_sha256','calibration_fit_end'])) = 2
    into v_ready
    from pg_catalog.pg_class c where c.oid = v_table;
  end if;
  return pg_catalog.jsonb_build_object(
    'contract_version', 'NBA_TEAM_EVENT_VERIFICATION_RECEIPTS_V2',
    'compatible', coalesce(v_ready, false),
    'ledger', 'wow_independent_verification_receipts',
    'probability_publishable', false,
    'can_execute', false
  );
end;
$$;

revoke all on function public.wow_nba_team_event_verification_receipt_schema_ready()
  from public, anon, authenticated, service_role;
grant execute on function public.wow_nba_team_event_verification_receipt_schema_ready()
  to service_role;

comment on function public.wow_nba_team_event_verification_receipt_schema_ready() is
'Read-only attestation of the canonical V17 independent verification ledger and calibrator evidence columns. Never certifies, promotes, publishes or executes.';
