-- WOW V17 market-data backbone for TheRundown / provider-neutral quote history.
-- Class B infrastructure only: no sporting probability, calibration, ranking,
-- staking, or execution authority is introduced by this migration.

create extension if not exists pgcrypto;

create table if not exists public.wow_market_price_observations (
    observation_id uuid primary key default gen_random_uuid(),
    observation_key text not null unique,

    provider text not null,
    provider_event_id text not null,
    sport_key text not null,
    event_start_utc timestamptz,

    market_id text,
    market_name text not null,
    participant_id text,
    participant_name text not null,
    participant_type text,
    selection text not null,
    line_id text,
    line_value numeric,

    affiliate_id text not null,
    sportsbook text not null,
    american_odds numeric not null,
    decimal_odds numeric,

    -- Freshness is based on the provider's quote timestamp. fetched_at is
    -- intentionally separate and must never be substituted for price_updated_at.
    price_updated_at timestamptz,
    fetched_at timestamptz not null default now(),
    snapshot_kind text not null,
    is_live boolean not null default false,
    is_main_line boolean not null default false,

    raw_payload jsonb not null default '{}'::jsonb,
    prediction_authority boolean not null default false,
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),

    constraint wow_market_price_observations_provider_nonempty check (btrim(provider) <> ''),
    constraint wow_market_price_observations_event_nonempty check (btrim(provider_event_id) <> ''),
    constraint wow_market_price_observations_sport_nonempty check (btrim(sport_key) <> ''),
    constraint wow_market_price_observations_book_nonempty check (btrim(sportsbook) <> ''),
    constraint wow_market_price_observations_no_prediction_authority check (prediction_authority = false),
    constraint wow_market_price_observations_no_execution check (can_execute = false),
    constraint wow_market_price_observations_decimal_valid check (decimal_odds is null or decimal_odds > 1.0)
);

create index if not exists wow_market_price_event_history_idx
    on public.wow_market_price_observations
    (provider, provider_event_id, market_id, participant_id, affiliate_id, price_updated_at desc nulls last, fetched_at desc);

create index if not exists wow_market_price_sport_fetch_idx
    on public.wow_market_price_observations (sport_key, fetched_at desc);

create index if not exists wow_market_price_event_start_idx
    on public.wow_market_price_observations (event_start_utc, provider_event_id);

create table if not exists public.wow_market_feed_sync_state (
    feed_key text primary key,
    provider text not null,
    sport_key text,
    slate_date date,
    market_ids text[] not null default '{}'::text[],
    affiliate_ids text[] not null default '{}'::text[],
    acquisition_mode text not null default 'SNAPSHOT',
    data_delay_seconds integer,
    delta_cursor bigint,
    last_success_at timestamptz,
    last_failure_at timestamptz,
    last_error_code text,
    last_rows_written integer not null default 0,
    catalog_refreshed_at timestamptz,
    metadata jsonb not null default '{}'::jsonb,
    can_execute boolean not null default false,
    updated_at timestamptz not null default now(),

    constraint wow_market_feed_sync_mode_check
        check (acquisition_mode in ('SNAPSHOT', 'DELTA', 'WEBSOCKET')),
    constraint wow_market_feed_sync_delay_check
        check (data_delay_seconds is null or data_delay_seconds >= 0),
    constraint wow_market_feed_sync_rows_check
        check (last_rows_written >= 0),
    constraint wow_market_feed_sync_no_execution check (can_execute = false)
);

create table if not exists public.wow_market_provider_catalog (
    provider text not null,
    catalog_type text not null,
    provider_id text not null,
    canonical_key text,
    display_name text,
    active boolean,
    payload jsonb not null default '{}'::jsonb,
    refreshed_at timestamptz not null default now(),
    can_execute boolean not null default false,
    primary key (provider, catalog_type, provider_id),

    constraint wow_market_provider_catalog_type_check
        check (catalog_type in ('SPORT', 'MARKET', 'AFFILIATE')),
    constraint wow_market_provider_catalog_no_execution check (can_execute = false)
);

alter table public.wow_market_price_observations enable row level security;
alter table public.wow_market_feed_sync_state enable row level security;
alter table public.wow_market_provider_catalog enable row level security;

-- Server-owned collector only. No anon/authenticated access is granted.
grant select, insert on table public.wow_market_price_observations to service_role;
grant select, insert, update on table public.wow_market_feed_sync_state to service_role;
grant select, insert, update on table public.wow_market_provider_catalog to service_role;

-- service_role normally bypasses RLS, but explicit policies document the intended
-- write principal and keep behavior correct in environments where bypass differs.
drop policy if exists wow_market_price_service_role on public.wow_market_price_observations;
create policy wow_market_price_service_role
    on public.wow_market_price_observations
    for all
    to service_role
    using (true)
    with check (prediction_authority = false and can_execute = false);

drop policy if exists wow_market_sync_service_role on public.wow_market_feed_sync_state;
create policy wow_market_sync_service_role
    on public.wow_market_feed_sync_state
    for all
    to service_role
    using (true)
    with check (can_execute = false);

drop policy if exists wow_market_catalog_service_role on public.wow_market_provider_catalog;
create policy wow_market_catalog_service_role
    on public.wow_market_provider_catalog
    for all
    to service_role
    using (true)
    with check (can_execute = false);

comment on table public.wow_market_price_observations is
    'Append-only provider market quotes. Evidence only; never sporting probability authority.';
comment on column public.wow_market_price_observations.price_updated_at is
    'Provider quote timestamp used for freshness. fetched_at must not substitute for this value.';
comment on table public.wow_market_feed_sync_state is
    'Collector recovery/entitlement state. DELTA is permitted only after runtime confirms zero delay and a valid cursor.';
comment on table public.wow_market_provider_catalog is
    'Runtime-discovered sport/market/affiliate identifiers; avoids permanent provider ID hardcoding.';
