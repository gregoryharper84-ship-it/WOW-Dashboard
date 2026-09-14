-- Kalshi Weather V2 R2 replay/certification accelerator.
-- Analytical only. No order or trade execution capability is introduced.

create table if not exists public.wow_kalshi_weather_replay_samples (
  sample_id text primary key,
  station_id text not null,
  series_ticker text not null,
  event_ticker text not null,
  observation_time timestamptz not null,
  settlement_source_name text not null,
  settled_value_f double precision not null,
  model_version text not null,
  lead_time_bucket text not null,
  decision_time timestamptz not null,
  run_initialization timestamptz not null,
  safe_available_at timestamptz not null,
  gfs_f double precision not null,
  ecmwf_f double precision not null,
  central_estimate_f double precision not null,
  archive_source_url text not null,
  archive_payload_hash text not null,
  predicates jsonb not null default '[]'::jsonb,
  no_lookahead_verified boolean not null default false,
  source_identity_verified boolean not null default false,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,
  constraint chk_kalshi_weather_replay_execute_false check (can_execute = false),
  constraint chk_kalshi_weather_replay_no_lookahead check (safe_available_at <= decision_time),
  unique (station_id, observation_time, model_version, lead_time_bucket)
);

create index if not exists idx_kalshi_weather_replay_profile_time
  on public.wow_kalshi_weather_replay_samples
  (station_id, model_version, lead_time_bucket, observation_time);

create table if not exists public.wow_kalshi_weather_certification_runs (
  certification_run_id text primary key,
  station_id text not null,
  model_version text not null,
  lead_time_bucket text not null,
  evaluated_at timestamptz not null,
  train_n integer not null default 0 check (train_n >= 0),
  holdout_n integer not null default 0 check (holdout_n >= 0),
  status text not null,
  promotion_eligible boolean not null default false,
  report jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,
  constraint chk_kalshi_weather_certification_execute_false check (can_execute = false)
);

create index if not exists idx_kalshi_weather_certification_profile_time
  on public.wow_kalshi_weather_certification_runs
  (station_id, model_version, lead_time_bucket, evaluated_at desc);

-- Reuse the immutable-ledger mutation guard installed by the Weather V2 base migration.
do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'wow_kalshi_weather_replay_samples',
    'wow_kalshi_weather_certification_runs'
  ]
  loop
    execute format('drop trigger if exists trg_%I_immutable on public.%I', table_name, table_name);
    execute format(
      'create trigger trg_%I_immutable before update or delete on public.%I for each row execute function public.wow_reject_immutable_mutation()',
      table_name, table_name
    );
    execute format('revoke all privileges on table public.%I from anon', table_name);
    execute format('revoke all privileges on table public.%I from authenticated', table_name);
    execute format('grant select, insert on table public.%I to service_role', table_name);
  end loop;
end;
$$;
