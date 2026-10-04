-- Kalshi Weather v1.4 P2 probability-change ledger.
-- Append-only observability only. Market context may be referenced but cannot
-- be used as weather-probability attribution. No execution interface is added.

create table if not exists public.wow_kalshi_weather_probability_changes (
  probability_change_id text primary key,
  ticker text not null,
  previous_prediction_id text not null references public.wow_kalshi_weather_predictions(prediction_id),
  current_prediction_id text not null references public.wow_kalshi_weather_predictions(prediction_id),
  before_decision_time timestamptz not null,
  after_decision_time timestamptz not null,
  p_yes_before double precision not null,
  p_yes_after double precision not null,
  total_delta double precision not null,
  attribution_total double precision not null,
  reconciliation_tolerance double precision not null default 1e-9,
  attribution_components jsonb not null,
  attribution_evidence_ids jsonb not null default '[]'::jsonb,
  market_context_snapshot_ids jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,
  constraint chk_kalshi_weather_change_prediction_ids_distinct
    check (previous_prediction_id <> current_prediction_id),
  constraint chk_kalshi_weather_change_time_order
    check (after_decision_time > before_decision_time),
  constraint chk_kalshi_weather_change_before_range
    check (p_yes_before > 0 and p_yes_before < 1),
  constraint chk_kalshi_weather_change_after_range
    check (p_yes_after > 0 and p_yes_after < 1),
  constraint chk_kalshi_weather_change_tolerance_nonnegative
    check (reconciliation_tolerance >= 0),
  constraint chk_kalshi_weather_change_total_delta
    check (abs(total_delta - (p_yes_after - p_yes_before)) <= 1e-12),
  constraint chk_kalshi_weather_change_attribution_reconciles
    check (abs(attribution_total - total_delta) <= reconciliation_tolerance),
  constraint chk_kalshi_weather_change_components_array
    check (jsonb_typeof(attribution_components) = 'array' and jsonb_array_length(attribution_components) > 0),
  constraint chk_kalshi_weather_change_execute_false
    check (can_execute = false)
);

create index if not exists idx_kalshi_weather_probability_changes_ticker_time
  on public.wow_kalshi_weather_probability_changes (ticker, after_decision_time desc);

alter table public.wow_kalshi_weather_probability_changes enable row level security;

revoke all on table public.wow_kalshi_weather_probability_changes from anon, authenticated;
revoke update, delete on table public.wow_kalshi_weather_probability_changes from service_role;
grant select, insert on table public.wow_kalshi_weather_probability_changes to service_role;

drop trigger if exists trg_wow_kalshi_weather_probability_changes_immutable
  on public.wow_kalshi_weather_probability_changes;

create trigger trg_wow_kalshi_weather_probability_changes_immutable
before update or delete on public.wow_kalshi_weather_probability_changes
for each row execute function public.wow_reject_immutable_mutation();
