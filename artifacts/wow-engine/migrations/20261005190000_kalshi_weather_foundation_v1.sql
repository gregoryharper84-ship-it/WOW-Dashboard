-- Kalshi Weather foundation V1: settlement digital twin + independent market microstructure recorder.
-- Class B additive infrastructure only. Does not alter weather probability math, certification, or execution authority.
-- All rows are immutable point-in-time evidence; corrections are new rows.

create table if not exists public.wow_kalshi_weather_settlement_twins (
  twin_snapshot_id text primary key,
  rule_snapshot_id text not null references public.wow_kalshi_weather_contract_rules(rule_snapshot_id),
  ticker text not null,
  built_at timestamptz not null,
  state_as_of timestamptz not null,
  settlement_source_name text not null,
  settlement_source_url text,
  settlement_location_code text,
  settlement_station_id text,
  station_timezone text not null,
  observation_window text not null,
  metric text not null,
  units text not null,
  rounding_convention text not null,
  yes_predicate jsonb not null,
  no_predicate jsonb not null,
  possible_outcomes jsonb not null default '[]'::jsonb,
  impossible_outcomes jsonb not null default '[]'::jsonb,
  remaining_outcomes jsonb not null default '[]'::jsonb,
  observed_value double precision,
  observed_extreme double precision,
  settlement_state text not null,
  source_snapshot_ids jsonb not null default '[]'::jsonb,
  blockers jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  method_version text not null,
  market_data_used_as_weather_probability_input boolean not null default false,
  can_execute boolean not null default false,
  created_at timestamptz not null default now(),
  constraint chk_kalshi_weather_twin_market_separation
    check (market_data_used_as_weather_probability_input = false),
  constraint chk_kalshi_weather_twin_execute_false
    check (can_execute = false),
  constraint chk_kalshi_weather_twin_state
    check (settlement_state in ('OPEN', 'LOCKED_YES', 'LOCKED_NO', 'SETTLED', 'AMBIGUOUS'))
);

create index if not exists idx_kalshi_weather_settlement_twin_rule_time
  on public.wow_kalshi_weather_settlement_twins (rule_snapshot_id, state_as_of desc);

create index if not exists idx_kalshi_weather_settlement_twin_ticker_time
  on public.wow_kalshi_weather_settlement_twins (ticker, state_as_of desc);

create table if not exists public.wow_kalshi_weather_market_microstructure_snapshots (
  microstructure_snapshot_id text primary key,
  ticker text not null,
  event_ticker text,
  series_ticker text,
  prediction_id text references public.wow_kalshi_weather_predictions(prediction_id),
  retrieved_at timestamptz not null,
  market_status text not null,
  yes_best_bid double precision,
  no_best_bid double precision,
  yes_best_ask double precision,
  no_best_ask double precision,
  yes_bid_size double precision,
  no_bid_size double precision,
  yes_ask_size double precision,
  no_ask_size double precision,
  yes_spread double precision,
  no_spread double precision,
  orderbook_imbalance double precision,
  volume double precision,
  open_interest double precision,
  liquidity double precision,
  raw_market jsonb not null,
  raw_orderbook jsonb not null,
  weather_probability_input_allowed boolean not null default false,
  can_execute boolean not null default false,
  created_at timestamptz not null default now(),
  constraint chk_kalshi_weather_microstructure_probability_separation
    check (weather_probability_input_allowed = false),
  constraint chk_kalshi_weather_microstructure_execute_false
    check (can_execute = false)
);

create index if not exists idx_kalshi_weather_microstructure_ticker_time
  on public.wow_kalshi_weather_market_microstructure_snapshots (ticker, retrieved_at desc);

create index if not exists idx_kalshi_weather_microstructure_prediction_time
  on public.wow_kalshi_weather_market_microstructure_snapshots (prediction_id, retrieved_at desc)
  where prediction_id is not null;

-- Append-only mutation rejection. The helper already exists in the V2 base migration.
drop trigger if exists trg_wow_kalshi_weather_settlement_twins_immutable
  on public.wow_kalshi_weather_settlement_twins;
create trigger trg_wow_kalshi_weather_settlement_twins_immutable
  before update or delete on public.wow_kalshi_weather_settlement_twins
  for each row execute function public.wow_reject_immutable_mutation();

drop trigger if exists trg_wow_kalshi_weather_market_microstructure_snapshots_immutable
  on public.wow_kalshi_weather_market_microstructure_snapshots;
create trigger trg_wow_kalshi_weather_market_microstructure_snapshots_immutable
  before update or delete on public.wow_kalshi_weather_market_microstructure_snapshots
  for each row execute function public.wow_reject_immutable_mutation();

-- Public/browser roles get no direct table access. Server role is append-only.
alter table public.wow_kalshi_weather_settlement_twins enable row level security;
alter table public.wow_kalshi_weather_market_microstructure_snapshots enable row level security;

revoke all privileges on table public.wow_kalshi_weather_settlement_twins from anon, authenticated, service_role;
revoke all privileges on table public.wow_kalshi_weather_market_microstructure_snapshots from anon, authenticated, service_role;

grant select, insert on table public.wow_kalshi_weather_settlement_twins to service_role;
grant select, insert on table public.wow_kalshi_weather_market_microstructure_snapshots to service_role;
