-- WOW V17 MCP OAuth authorization claim support.
-- Class B integration infrastructure only. This does not change sporting-model
-- probability behavior, terminal reduction, or execution permissions.
--
-- IMPORTANT: creating this function does not enable the Supabase Auth hook.
-- Promotion still requires explicit Auth -> Hooks configuration and an approved
-- OAuth client row. With no approved row, the hook emits an empty permission set
-- and leaves the ordinary Supabase audience unchanged.

create schema if not exists wow_private;

revoke all on schema wow_private from public;
revoke all on schema wow_private from anon;
revoke all on schema wow_private from authenticated;

grant usage on schema wow_private to supabase_auth_admin;

create table if not exists wow_private.wow_mcp_oauth_client_permissions (
    client_id text primary key,
    resource_audience text,
    permissions text[] not null default array[]::text[],
    enabled boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint chk_wow_mcp_oauth_permissions_nonempty_when_enabled
        check (not enabled or cardinality(permissions) > 0),
    constraint chk_wow_mcp_oauth_resource_audience_when_enabled
        check (
            not enabled
            or (
                resource_audience is not null
                and resource_audience ~ '^https://[^[:space:]]+$'
            )
        ),
    constraint chk_wow_mcp_oauth_permissions_allowlist
        check (
            permissions <@ array[
                'wow.runtime.read',
                'wow.governance.read',
                'wow.evidence.read',
                'wow.predictions.read',
                'wow.predictions.score',
                'wow.daily.run',
                'wow.recommendations.write',
                'wow.settlements.write'
            ]::text[]
        )
);

alter table wow_private.wow_mcp_oauth_client_permissions enable row level security;

revoke all on table wow_private.wow_mcp_oauth_client_permissions from public;
revoke all on table wow_private.wow_mcp_oauth_client_permissions from anon;
revoke all on table wow_private.wow_mcp_oauth_client_permissions from authenticated;
grant select on table wow_private.wow_mcp_oauth_client_permissions to supabase_auth_admin;

drop policy if exists wow_mcp_auth_admin_read on wow_private.wow_mcp_oauth_client_permissions;
create policy wow_mcp_auth_admin_read
    on wow_private.wow_mcp_oauth_client_permissions
    for select
    to supabase_auth_admin
    using (true);

create or replace function wow_private.wow_mcp_custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
security invoker
set search_path = ''
as $$
declare
    claims jsonb;
    app_metadata jsonb;
    oauth_client_id text;
    allowed_permissions text[];
    protected_resource_audience text;
begin
    claims := coalesce(event->'claims', '{}'::jsonb);
    app_metadata := coalesce(claims->'app_metadata', '{}'::jsonb);
    oauth_client_id := coalesce(event->>'client_id', claims->>'client_id');

    select p.permissions, p.resource_audience
      into allowed_permissions, protected_resource_audience
      from wow_private.wow_mcp_oauth_client_permissions as p
     where p.client_id = oauth_client_id
       and p.enabled is true;

    -- Fail closed: an unknown, disabled, or non-OAuth client receives no WOW
    -- operation permissions. app_metadata is server-controlled in Supabase JWTs.
    app_metadata := jsonb_set(
        app_metadata,
        '{wow_permissions}',
        to_jsonb(coalesce(allowed_permissions, array[]::text[])),
        true
    );
    claims := jsonb_set(claims, '{app_metadata}', app_metadata, true);

    -- An approved MCP client receives a resource-restricted access token. This
    -- prevents a generic Supabase `aud=authenticated` token from being replayed
    -- against WOW MCP. Unknown clients keep the ordinary audience unchanged.
    if protected_resource_audience is not null then
        claims := jsonb_set(claims, '{aud}', to_jsonb(protected_resource_audience), true);
    end if;

    return jsonb_set(event, '{claims}', claims, true);
end;
$$;

revoke execute on function wow_private.wow_mcp_custom_access_token_hook(jsonb) from public;
revoke execute on function wow_private.wow_mcp_custom_access_token_hook(jsonb) from anon;
revoke execute on function wow_private.wow_mcp_custom_access_token_hook(jsonb) from authenticated;
grant execute on function wow_private.wow_mcp_custom_access_token_hook(jsonb) to supabase_auth_admin;

comment on table wow_private.wow_mcp_oauth_client_permissions is
    'Server-controlled allowlist mapping OAuth client_id to the exact MCP resource audience and least-privilege WOW V17 operation permissions.';
comment on function wow_private.wow_mcp_custom_access_token_hook(jsonb) is
    'Supabase Custom Access Token Hook candidate. Approved clients receive resource-restricted aud plus app_metadata.wow_permissions; unknown clients fail closed with no WOW permissions.';
