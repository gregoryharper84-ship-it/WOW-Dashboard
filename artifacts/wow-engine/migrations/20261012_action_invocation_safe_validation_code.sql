-- Class A: nullable safe validation code; pre-existing receipt rows remain NULL.
-- Preserve immutable primary receipt, advisory lock, recovery clearing, and RLS.
alter table public.wow_action_invocation_receipts
  add column if not exists validation_code text
  check (validation_code is null or validation_code ~ '^[A-Z][A-Z0-9_]{1,79}$');

-- PM-2026-09-27-003: serialize immutable Action receipts and recovery state.
--
-- Restorative-only boundary:
-- * no sporting probability or terminal semantics change;
-- * canonical receipts remain immutable (INSERT ... ON CONFLICT DO NOTHING);
-- * one transaction advisory lock orders primary/recovery writes per invocation;
-- * recovery attempts/states move monotonically and primary receipt always wins;
-- * only service_role can execute these SECURITY DEFINER entry points.

create or replace function public.wow_record_action_invocation_receipt(
    p_receipt jsonb
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_invocation_id uuid;
    v_inserted integer;
begin
    v_invocation_id := nullif(p_receipt ->> 'invocation_id', '')::uuid;
    if v_invocation_id is null then
        raise exception 'invocation_id is required' using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(v_invocation_id::text, 0)
    );

    insert into public.wow_action_invocation_receipts (
        invocation_id,
        occurred_at,
        route,
        action_operation_id,
        http_method,
        http_status,
        validation_code,
        auth_scheme,
        caller_class,
        caller_user_agent,
        request_id,
        rows_in,
        duration_ms,
        can_execute
    ) values (
        v_invocation_id,
        coalesce(nullif(p_receipt ->> 'occurred_at', '')::timestamptz, pg_catalog.clock_timestamp()),
        p_receipt ->> 'route',
        p_receipt ->> 'action_operation_id',
        p_receipt ->> 'http_method',
        (p_receipt ->> 'http_status')::integer,
        case when (p_receipt ->> 'validation_code') ~ '^[A-Z][A-Z0-9_]{1,79}$'
            then p_receipt ->> 'validation_code' else null end,
        coalesce(nullif(p_receipt ->> 'auth_scheme', ''), 'NONE'),
        coalesce(nullif(p_receipt ->> 'caller_class', ''), 'UNKNOWN'),
        nullif(p_receipt ->> 'caller_user_agent', ''),
        nullif(p_receipt ->> 'request_id', ''),
        nullif(p_receipt ->> 'rows_in', '')::integer,
        nullif(p_receipt ->> 'duration_ms', '')::numeric,
        false
    )
    on conflict (invocation_id) do nothing;

    get diagnostics v_inserted = row_count;

    delete from public.wow_action_invocation_receipt_recovery
    where invocation_id = v_invocation_id;

    return pg_catalog.jsonb_build_object(
        'status', case when v_inserted = 1 then 'INSERTED' else 'ALREADY_PRESENT' end,
        'invocation_id', v_invocation_id,
        'recovery_cleared', true,
        'can_execute', false
    );
end;
$$;


-- No table grant changes: existing service-role-only RLS/grants remain intact.
-- Recovery RPC carries the original JSON receipt unchanged; the primary RPC
-- serializes its validation_code when a recovered receipt is retried.
revoke all on function public.wow_record_action_invocation_receipt(jsonb) from public, anon, authenticated;
grant execute on function public.wow_record_action_invocation_receipt(jsonb) to service_role;
