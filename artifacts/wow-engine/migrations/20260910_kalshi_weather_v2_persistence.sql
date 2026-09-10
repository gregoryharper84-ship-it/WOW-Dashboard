-- Kalshi Weather V2 governed persistence.
-- Analytical only. No trading/execution tables or order interfaces are created.

create or replace function public.wow_reject_immutable_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'immutable ledger row cannot be updated or deleted';
end;
$$;

create table if not exists public.wow_kalshi_weather_contract_rules (
  rule_snapshot_id text primary key,
  contract_package_id text not null,
  ticker text not null,
  event_ticker text not null,
  series_ticker text not null,
  acquired_at timestamptz not null,
  settlement_source_name text not null,
  settlement_source_url text,
  contract_url text,
  contract_terms_url text,
  rules_primary text not null,
  rules_secondary text not null default '',
  raw_market jsonb not null,
  raw_event jsonb not null,
  raw_series jsonb not null,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,
  constraint chk_kalshi_weather_contract_rules_execute_false check (can_execute = false)
);

create index if not exists idx_kalshi_weather_contract_rules_ticker_time
  on public.wow_kalshi_weather_contract_rules (ticker, acquired_at desc);

create table if not exists public.wow_kalshi_weather_source_snapshots (
  source_snapshot_id text primary key,
  rule_snapshot_id text not null references public.wow_kalshi_weather_contract_rules(rule_snapshot_id),
  provider text not null,
  source_role text not null,
  source_identifier text not null,
  evidence_time timestamptz,
  issued_at timestamptz,
  retrieved_at timestamptz not null,
  valid_times jsonb not null default '[]'::jsonb,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_kalshi_weather_source_rule_time
  on public.wow_kalshi_weather_source_snapshots (rule_snapshot_id, retrieved_at desc);

create table if not exists public.wow_kalshi_weather_calibration_profiles (
  calibration_profile_id text primary key,
  station_id text not null,
  lane text not null,
  lead_time_bucket text not null,
  model_version text not null,
  method text not null,
  bias_f double precision not null,
  sigma_f double precision not null check (sigma_f > 0),
  lower_sigma_f double precision not null check (lower_sigma_f > 0),
  upper_sigma_f double precision not null check (upper_sigma_f > 0),
  sample_n integer not null check (sample_n >= 0),
  certified boolean not null default false,
  certification_evidence jsonb not null default '{}'::jsonb,
  fitted_as_of timestamptz not null,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,
  constraint chk_kalshi_weather_calibration_execute_false check (can_execute = false)
);

create index if not exists idx_kalshi_weather_calibration_lookup
  on public.wow_kalshi_weather_calibration_profiles (station_id, lane, lead_time_bucket, fitted_as_of desc);

create table if not exists public.wow_kalshi_weather_predictions (
  prediction_id text primary key,
  rule_snapshot_id text not null references public.wow_kalshi_weather_contract_rules(rule_snapshot_id),
  ticker text not null,
  decision_time timestamptz not null,
  model_version text not null,
  calibration_profile_id text references public.wow_kalshi_weather_calibration_profiles(calibration_profile_id),
  p_yes double precision,
  p_no double precision,
  lower_bound_yes double precision,
  upper_bound_yes double precision,
  central_estimate_f double precision,
  threshold_distance text,
  probability_status text not null,
  probability_publishable boolean not null default false,
  blockers jsonb not null default '[]'::jsonb,
  warnings jsonb not null default '[]'::jsonb,
  evidence_snapshot_ids jsonb not null default '[]'::jsonb,
  model_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,
  constraint chk_kalshi_weather_prediction_yes_range check (p_yes is null or (p_yes >= 0 and p_yes <= 1)),
  constraint chk_kalshi_weather_prediction_no_range check (p_no is null or (p_no >= 0 and p_no <= 1)),
  constraint chk_kalshi_weather_prediction_execute_false check (can_execute = false)
);

create index if not exists idx_kalshi_weather_predictions_ticker_time
  on public.wow_kalshi_weather_predictions (ticker, decision_time desc);

create table if not exists public.wow_kalshi_weather_market_snapshots (
  market_snapshot_id text primary key,
  prediction_id text not null references public.wow_kalshi_weather_predictions(prediction_id),
  ticker text not null,
  retrieved_at timestamptz not null,
  market_status text not null,
  yes_best_bid double precision,
  no_best_bid double precision,
  yes_best_ask double precision,
  no_best_ask double precision,
  fee_policy_id text,
  yes_effective_break_even double precision,
  no_effective_break_even double precision,
  executable_price_verified boolean not null default false,
  friction_model_verified boolean not null default false,
  raw_market jsonb not null,
  raw_orderbook jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_kalshi_weather_market_prediction_time
  on public.wow_kalshi_weather_market_snapshots (prediction_id, retrieved_at desc);

create table if not exists public.wow_kalshi_weather_outcomes (
  outcome_id text primary key,
  prediction_id text not null unique references public.wow_kalshi_weather_predictions(prediction_id),
  settled_at timestamptz not null,
  settlement_source_name text not null,
  settlement_source_url text,
  settled_value double precision,
  yes_outcome boolean,
  settlement_payload jsonb not null default '{}'::jsonb,
  brier_score double precision,
  log_loss double precision,
  created_at timestamptz not null default now()
);

-- Immutable ledgers: corrections are new versioned rows, never in-place rewrites.
do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'wow_kalshi_weather_contract_rules',
    'wow_kalshi_weather_source_snapshots',
    'wow_kalshi_weather_calibration_profiles',
    'wow_kalshi_weather_predictions',
    'wow_kalshi_weather_market_snapshots',
    'wow_kalshi_weather_outcomes'
  ]
  loop
    execute format('drop trigger if exists trg_%I_immutable on public.%I', table_name, table_name);
    execute format(
      'create trigger trg_%I_immutable before update or delete on public.%I for each row execute function public.wow_reject_immutable_mutation()',
      table_name, table_name
    );
  end loop;
end;
$$;

insert into public.wow_runtime_capabilities (capability_key, capability_status, evidence, can_execute)
values (
  'KALSHI_WEATHER_PROBABILITY',
  'UNAVAILABLE',
  jsonb_build_object(
    'lifecycle', 'IMPLEMENTATION_NOT_CERTIFIED',
    'probability_publishable', false,
    'reason', 'Kalshi Weather V2 persistence registered before governed calibration and live acceptance',
    'host', 'WOW_KALSHI_ENGINE'
  ),
  false
)
on conflict (capability_key) do update
set capability_status = 'UNAVAILABLE',
    updated_at = now(),
    evidence = excluded.evidence,
    can_execute = false;
