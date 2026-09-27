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

create or replace function public.wow_record_action_invocation_receipt_recovery(
    p_receipt jsonb,
    p_attempt_count integer,
    p_error_type text,
    p_state text
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_invocation_id uuid;
    v_state text := pg_catalog.upper(pg_catalog.btrim(coalesce(p_state, '')));
    v_durable_state text;
begin
    v_invocation_id := nullif(p_receipt ->> 'invocation_id', '')::uuid;
    if v_invocation_id is null then
        raise exception 'invocation_id is required' using errcode = '22023';
    end if;
    if p_attempt_count is null or p_attempt_count < 1 then
        raise exception 'attempt_count must be positive' using errcode = '22023';
    end if;
    if v_state not in ('PENDING', 'DEAD_LETTER') then
        raise exception 'invalid recovery state' using errcode = '22023';
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(v_invocation_id::text, 0)
    );

    if exists (
        select 1
        from public.wow_action_invocation_receipts
        where invocation_id = v_invocation_id
    ) then
        delete from public.wow_action_invocation_receipt_recovery
        where invocation_id = v_invocation_id;
        return pg_catalog.jsonb_build_object(
            'status', 'RECONCILED',
            'invocation_id', v_invocation_id,
            'can_execute', false
        );
    end if;

    insert into public.wow_action_invocation_receipt_recovery (
        invocation_id,
        enqueued_at,
        updated_at,
        attempt_count,
        state,
        last_error_type,
        receipt,
        can_execute
    ) values (
        v_invocation_id,
        pg_catalog.clock_timestamp(),
        pg_catalog.clock_timestamp(),
        p_attempt_count,
        v_state,
        pg_catalog.left(coalesce(p_error_type, 'UNKNOWN'), 128),
        p_receipt,
        false
    )
    on conflict (invocation_id) do update set
        updated_at = pg_catalog.clock_timestamp(),
        attempt_count = greatest(
            public.wow_action_invocation_receipt_recovery.attempt_count,
            excluded.attempt_count
        ),
        state = case
            when public.wow_action_invocation_receipt_recovery.state = 'DEAD_LETTER'
              or excluded.state = 'DEAD_LETTER'
            then 'DEAD_LETTER'
            else 'PENDING'
        end,
        last_error_type = case
            when excluded.attempt_count >= public.wow_action_invocation_receipt_recovery.attempt_count
            then excluded.last_error_type
            else public.wow_action_invocation_receipt_recovery.last_error_type
        end,
        can_execute = false;

    select recovery.state
    into v_durable_state
    from public.wow_action_invocation_receipt_recovery as recovery
    where recovery.invocation_id = v_invocation_id;

    return pg_catalog.jsonb_build_object(
        'status', v_durable_state,
        'invocation_id', v_invocation_id,
        'can_execute', false
    );
end;
$$;

create or replace function public.wow_reconcile_action_invocation_receipt_recovery(
    p_limit integer default 100
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
    v_invocation_id uuid;
    v_limit integer := least(greatest(coalesce(p_limit, 100), 1), 1000);
    v_reconciled integer := 0;
begin
    for v_invocation_id in
        select recovery.invocation_id
        from public.wow_action_invocation_receipt_recovery as recovery
        where exists (
            select 1
            from public.wow_action_invocation_receipts as receipt
            where receipt.invocation_id = recovery.invocation_id
        )
        order by recovery.updated_at asc, recovery.invocation_id asc
        limit v_limit
    loop
        perform pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(v_invocation_id::text, 0)
        );
        delete from public.wow_action_invocation_receipt_recovery as recovery
        where recovery.invocation_id = v_invocation_id
          and exists (
              select 1
              from public.wow_action_invocation_receipts as receipt
              where receipt.invocation_id = v_invocation_id
          );
        v_reconciled := v_reconciled + case when found then 1 else 0 end;
    end loop;

    return pg_catalog.jsonb_build_object(
        'status', 'RECONCILED',
        'rows_deleted', v_reconciled,
        'limit', v_limit,
        'can_execute', false
    );
end;
$$;

revoke all on function public.wow_record_action_invocation_receipt(jsonb)
    from public, anon, authenticated;
revoke all on function public.wow_record_action_invocation_receipt_recovery(jsonb, integer, text, text)
    from public, anon, authenticated;
revoke all on function public.wow_reconcile_action_invocation_receipt_recovery(integer)
    from public, anon, authenticated;

grant execute on function public.wow_record_action_invocation_receipt(jsonb)
    to service_role;
grant execute on function public.wow_record_action_invocation_receipt_recovery(jsonb, integer, text, text)
    to service_role;
grant execute on function public.wow_reconcile_action_invocation_receipt_recovery(integer)
    to service_role;

comment on function public.wow_record_action_invocation_receipt(jsonb) is
    'Service-role-only atomic immutable Action receipt insert and recovery cleanup; can_execute=false.';
comment on function public.wow_record_action_invocation_receipt_recovery(jsonb, integer, text, text) is
    'Service-role-only monotonic Action receipt recovery persistence with primary-receipt-wins semantics; can_execute=false.';
comment on function public.wow_reconcile_action_invocation_receipt_recovery(integer) is
    'Service-role-only bounded idempotent cleanup of recovery rows already represented by immutable primary receipts; can_execute=false.';
