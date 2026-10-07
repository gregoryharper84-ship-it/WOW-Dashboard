-- V17 Free-Core Independence (#1452): short-lived provider response cache.
--
-- Operational cache only. This table is mutable/expiring by design and is
-- deliberately separate from immutable prediction/evidence ledgers.
-- It stores only already-normalized research market evidence. It never grants
-- probability, certification, publication, ranking, or execution authority.
create table if not exists public.wow_v17_provider_response_cache (
    cache_key text primary key,
    provider text not null,
    capability text not null,
    sport_key text not null,
    slate_date text not null,
    payload jsonb not null,
    response_meta jsonb not null default '{}'::jsonb,
    captured_at timestamptz not null default now(),
    expires_at timestamptz not null,
    can_execute boolean not null default false check (can_execute = false),
    constraint wow_v17_provider_response_cache_expiry_ck
        check (expires_at > captured_at)
);

create index if not exists wow_v17_provider_response_cache_expiry_idx
    on public.wow_v17_provider_response_cache (expires_at);

alter table public.wow_v17_provider_response_cache enable row level security;
revoke all on table public.wow_v17_provider_response_cache
    from public, anon, authenticated;
grant select, insert, update, delete on table public.wow_v17_provider_response_cache
    to service_role;

comment on table public.wow_v17_provider_response_cache is
'Service-role-only short-TTL normalized provider response cache for V17 Free-Core quota resilience. Mutable operational cache; never probability/certification/publication/execution authority; can_execute=false.';
