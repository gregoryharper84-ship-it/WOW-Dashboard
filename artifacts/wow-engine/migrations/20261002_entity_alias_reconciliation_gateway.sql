-- V17 deterministic Entity/Alias Reconciliation Gateway.
-- Class B identity infrastructure only. Exact-match runtime resolution; no
-- fuzzy auto-binding, sporting probability, calibration, ranking, or execution authority.

create table if not exists public.wow_canonical_players (
    canonical_player_id uuid primary key default gen_random_uuid(),
    sport text not null,
    canonical_name text not null,
    current_team_key text,
    active boolean not null default true,
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists wow_canonical_players_sport_name_team_uq
on public.wow_canonical_players (
    upper(sport),
    lower(btrim(canonical_name)),
    coalesce(upper(btrim(current_team_key)), '')
);

create table if not exists public.wow_player_aliases (
    alias_id uuid primary key default gen_random_uuid(),
    source_feed text not null,
    sport text not null,
    raw_alias text not null,
    normalized_alias text generated always as (lower(btrim(raw_alias))) stored,
    provider_player_id text,
    canonical_player_id uuid not null
        references public.wow_canonical_players(canonical_player_id) on delete restrict,
    verification_status text not null default 'VERIFIED'
        check (verification_status in ('VERIFIED','REVOKED')),
    verified_by text not null,
    verified_at timestamptz not null default now(),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

create unique index if not exists wow_player_aliases_exact_lookup_uq
on public.wow_player_aliases (lower(source_feed), upper(sport), normalized_alias)
where verification_status = 'VERIFIED';

create unique index if not exists wow_player_aliases_provider_id_uq
on public.wow_player_aliases (lower(source_feed), upper(sport), provider_player_id)
where provider_player_id is not null and verification_status = 'VERIFIED';

create table if not exists public.wow_entity_alias_gateway_control (
    sport text primary key,
    mode text not null default 'SHADOW' check (mode in ('SHADOW','ENFORCED')),
    minimum_verified_aliases integer not null default 25 check (minimum_verified_aliases >= 0),
    minimum_resolution_rate double precision not null default 0.95
        check (minimum_resolution_rate >= 0 and minimum_resolution_rate <= 1),
    can_execute boolean not null default false check (can_execute = false),
    updated_at timestamptz not null default now()
);

create table if not exists public.wow_unresolved_entity_aliases (
    unresolved_id uuid primary key default gen_random_uuid(),
    source_feed text not null,
    sport text not null,
    entity_type text not null check (entity_type in ('PLAYER','EVENT','TEAM','STAT_TYPE')),
    raw_value text not null,
    normalized_value text generated always as (lower(btrim(raw_value))) stored,
    run_id text,
    row_key text,
    context_payload jsonb not null default '{}'::jsonb,
    occurrence_count integer not null default 1 check (occurrence_count >= 1),
    status text not null default 'PENDING'
        check (status in ('PENDING','RESOLVED','IGNORED')),
    resolved_canonical_id text,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    can_execute boolean not null default false check (can_execute = false),
    check (jsonb_typeof(context_payload) = 'object')
);

create unique index if not exists wow_unresolved_entity_aliases_identity_uq
on public.wow_unresolved_entity_aliases (
    lower(source_feed), upper(sport), entity_type, normalized_value
)
where status = 'PENDING';

create index if not exists wow_unresolved_entity_aliases_priority_idx
on public.wow_unresolved_entity_aliases (
    status, occurrence_count desc, last_seen_at desc
);

alter table public.wow_canonical_players enable row level security;
alter table public.wow_player_aliases enable row level security;
alter table public.wow_unresolved_entity_aliases enable row level security;
alter table public.wow_entity_alias_gateway_control enable row level security;

revoke all on table public.wow_canonical_players from public, anon, authenticated;
revoke all on table public.wow_player_aliases from public, anon, authenticated;
revoke all on table public.wow_unresolved_entity_aliases from public, anon, authenticated;
revoke all on table public.wow_entity_alias_gateway_control from public, anon, authenticated;

grant select, insert, update on table public.wow_canonical_players to service_role;
grant select, insert, update on table public.wow_player_aliases to service_role;
grant select, insert, update on table public.wow_unresolved_entity_aliases to service_role;
grant select, insert, update on table public.wow_entity_alias_gateway_control to service_role;

insert into public.wow_entity_alias_gateway_control (sport, mode, can_execute)
values
    ('MLB','SHADOW',false),
    ('NFL','SHADOW',false),
    ('NBA','SHADOW',false),
    ('WNBA','SHADOW',false),
    ('NCAAF','SHADOW',false),
    ('NCAAB','SHADOW',false),
    ('NHL','SHADOW',false),
    ('SOCCER','SHADOW',false),
    ('TENNIS','SHADOW',false),
    ('MMA','SHADOW',false)
on conflict (sport) do nothing;

create or replace function public.wow_resolve_player_alias(
    p_source_feed text,
    p_sport text,
    p_raw_alias text,
    p_run_id text default null,
    p_row_key text default null,
    p_context_payload jsonb default '{}'::jsonb
)
returns table (
    canonical_player_id uuid,
    canonical_name text,
    current_team_key text,
    provider_player_id text,
    is_resolved boolean,
    gateway_mode text,
    can_execute boolean
)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_alias record;
    v_mode text := 'SHADOW';
begin
    select mode
      into v_mode
      from public.wow_entity_alias_gateway_control
     where upper(sport) = upper(btrim(p_sport));
    v_mode := coalesce(v_mode, 'SHADOW');
    select
        a.canonical_player_id,
        p.canonical_name,
        p.current_team_key,
        a.provider_player_id
    into v_alias
    from public.wow_player_aliases a
    join public.wow_canonical_players p
      on p.canonical_player_id = a.canonical_player_id
    where lower(a.source_feed) = lower(btrim(p_source_feed))
      and upper(a.sport) = upper(btrim(p_sport))
      and a.normalized_alias = lower(btrim(p_raw_alias))
      and a.verification_status = 'VERIFIED'
      and p.active is true
    limit 2;

    if found then
        return query
        select
            v_alias.canonical_player_id::uuid,
            v_alias.canonical_name::text,
            v_alias.current_team_key::text,
            v_alias.provider_player_id::text,
            true,
            v_mode,
            false;
        return;
    end if;

    insert into public.wow_unresolved_entity_aliases (
        source_feed,
        sport,
        entity_type,
        raw_value,
        run_id,
        row_key,
        context_payload,
        occurrence_count,
        last_seen_at,
        can_execute
    )
    values (
        btrim(p_source_feed),
        upper(btrim(p_sport)),
        'PLAYER',
        btrim(p_raw_alias),
        nullif(btrim(p_run_id), ''),
        nullif(btrim(p_row_key), ''),
        coalesce(p_context_payload, '{}'::jsonb),
        1,
        now(),
        false
    )
    on conflict (lower(source_feed), upper(sport), entity_type, normalized_value)
    where status = 'PENDING'
    do update set
        occurrence_count = public.wow_unresolved_entity_aliases.occurrence_count + 1,
        last_seen_at = now(),
        run_id = coalesce(excluded.run_id, public.wow_unresolved_entity_aliases.run_id),
        row_key = coalesce(excluded.row_key, public.wow_unresolved_entity_aliases.row_key),
        context_payload = excluded.context_payload,
        can_execute = false;

    return query
    select
        null::uuid,
        null::text,
        null::text,
        null::text,
        false,
        v_mode,
        false;
end;
$$;

revoke all on function public.wow_resolve_player_alias(text,text,text,text,text,jsonb)
from public, anon, authenticated;
grant execute on function public.wow_resolve_player_alias(text,text,text,text,text,jsonb)
to service_role;

comment on function public.wow_resolve_player_alias(text,text,text,text,text,jsonb) is
    'Exact-match V17 player alias resolver. Misses are durably audited. SHADOW is default until verified coverage passes; ENFORCED may then fail closed. No fuzzy match or probability authority.';
