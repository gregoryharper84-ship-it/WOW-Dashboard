-- PM-2026-11-23-004: attribute callers of internal governed NHL source routes.
--
-- Restorative-only boundary:
-- * internal maintenance routes now persist the same immutable receipt shape as
--   public Action routes, plus the authoritative bearer class, transport peer
--   address and proxy forwarding chain;
-- * no probability, calibration, terminal, catalog or execution semantics change;
-- * the receipt entry point stays SECURITY DEFINER and service_role-only;
-- * receipts remain immutable (INSERT ... ON CONFLICT DO NOTHING).

alter table public.wow_action_invocation_receipts
    add column if not exists auth_bearer_class text,
    add column if not exists client_addr inet,
    add column if not exists forwarded_for text;

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
        auth_bearer_class,
        client_addr,
        forwarded_for,
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
        nullif(p_receipt ->> 'auth_bearer_class', ''),
        nullif(p_receipt ->> 'client_addr', '')::inet,
        nullif(p_receipt ->> 'forwarded_for', ''),
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

revoke all on function public.wow_record_action_invocation_receipt(jsonb)
    from public, anon, authenticated;

grant execute on function public.wow_record_action_invocation_receipt(jsonb)
    to service_role;

comment on function public.wow_record_action_invocation_receipt(jsonb) is
    'Service-role-only atomic immutable Action receipt insert and recovery cleanup; can_execute=false.';

comment on column public.wow_action_invocation_receipts.auth_bearer_class is
    'Authoritative bearer decision observed by the route auth dependency (WOW_ACTION_API_KEY, GITHUB_ACTIONS_OIDC, UNAUTHORIZED); null when unrecorded.';

comment on column public.wow_action_invocation_receipts.client_addr is
    'Transport peer address as seen by the runtime; null when the peer is not an IP literal.';

comment on column public.wow_action_invocation_receipts.forwarded_for is
    'Unverified proxy X-Forwarded-For chain, stored verbatim for attribution only.';
