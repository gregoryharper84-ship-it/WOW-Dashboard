-- Kalshi Weather v1.4 P3 portfolio/scenario risk persistence.
-- Decision-support only. No order or execution interfaces are created.

create table public.wow_kalshi_weather_portfolio_risk_snapshots (
  risk_snapshot_id text primary key,
  as_of_time timestamptz not null,
  dependence_mode text not null,
  engine_version text not null,
  position_count integer not null,
  scenario_count integer not null,
  gross_cost_at_risk double precision not null,
  gross_contracts double precision not null,
  expected_pnl double precision not null,
  expected_loss double precision not null,
  probability_of_loss double precision not null,
  worst_case_pnl double precision not null,
  max_loss double precision not null,
  max_event_concentration double precision not null,
  max_region_concentration double precision not null,
  fractional_kelly_multiplier double precision,
  prediction_ids jsonb not null default '[]'::jsonb,
  market_snapshot_ids jsonb not null default '[]'::jsonb,
  positions jsonb not null,
  scenarios jsonb not null,
  metrics jsonb not null,
  market_price_used_as_weather_input boolean not null default false,
  risk_state_used_as_weather_input boolean not null default false,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,

  constraint chk_kalshi_weather_portfolio_dependence_mode
    check (dependence_mode in ('SAME_EVENT_EXACT', 'REGIONAL_FACTOR_SCENARIOS')),
  constraint chk_kalshi_weather_portfolio_counts
    check (position_count > 0 and scenario_count > 0),
  constraint chk_kalshi_weather_portfolio_gross_positive
    check (gross_cost_at_risk > 0 and gross_contracts > 0),
  constraint chk_kalshi_weather_portfolio_expected_loss
    check (expected_loss >= 0),
  constraint chk_kalshi_weather_portfolio_loss_probability
    check (probability_of_loss >= 0 and probability_of_loss <= 1),
  constraint chk_kalshi_weather_portfolio_max_loss
    check (max_loss >= 0),
  constraint chk_kalshi_weather_portfolio_event_concentration
    check (max_event_concentration > 0 and max_event_concentration <= 1),
  constraint chk_kalshi_weather_portfolio_region_concentration
    check (max_region_concentration > 0 and max_region_concentration <= 1),
  constraint chk_kalshi_weather_portfolio_kelly_multiplier
    check (fractional_kelly_multiplier is null or (fractional_kelly_multiplier > 0 and fractional_kelly_multiplier <= 1)),
  constraint chk_kalshi_weather_portfolio_prediction_ids
    check (jsonb_typeof(prediction_ids) = 'array' and jsonb_array_length(prediction_ids) > 0),
  constraint chk_kalshi_weather_portfolio_market_ids
    check (jsonb_typeof(market_snapshot_ids) = 'array' and jsonb_array_length(market_snapshot_ids) > 0),
  constraint chk_kalshi_weather_portfolio_positions_array
    check (jsonb_typeof(positions) = 'array' and jsonb_array_length(positions) = position_count),
  constraint chk_kalshi_weather_portfolio_scenarios_array
    check (jsonb_typeof(scenarios) = 'array' and jsonb_array_length(scenarios) = scenario_count),
  constraint chk_kalshi_weather_portfolio_metrics_object
    check (jsonb_typeof(metrics) = 'object'),
  constraint chk_kalshi_weather_portfolio_no_market_feedback
    check (market_price_used_as_weather_input = false),
  constraint chk_kalshi_weather_portfolio_no_risk_feedback
    check (risk_state_used_as_weather_input = false),
  constraint chk_kalshi_weather_portfolio_execute_false
    check (can_execute = false),
  constraint chk_kalshi_weather_portfolio_position_guards
    check (
      not jsonb_path_exists(positions, '$[*] ? (@.market_price_used_as_weather_input == true)')
      and not jsonb_path_exists(positions, '$[*] ? (@.risk_state_used_as_weather_input == true)')
      and not jsonb_path_exists(positions, '$[*] ? (@.can_execute == true)')
    ),
  constraint chk_kalshi_weather_portfolio_scenario_execute_false
    check (not jsonb_path_exists(scenarios, '$[*] ? (@.can_execute == true)')),
  constraint chk_kalshi_weather_portfolio_kelly_execute_false
    check (not jsonb_path_exists(metrics, '$.kelly_research[*] ? (@.can_execute == true)'))
);

create index idx_kalshi_weather_portfolio_risk_time
  on public.wow_kalshi_weather_portfolio_risk_snapshots (as_of_time desc);

create index idx_kalshi_weather_portfolio_risk_mode_time
  on public.wow_kalshi_weather_portfolio_risk_snapshots (dependence_mode, as_of_time desc);

alter table public.wow_kalshi_weather_portfolio_risk_snapshots enable row level security;

revoke all privileges on table public.wow_kalshi_weather_portfolio_risk_snapshots
  from anon, authenticated, service_role;

grant select, insert on table public.wow_kalshi_weather_portfolio_risk_snapshots
  to service_role;

create trigger trg_wow_kalshi_weather_portfolio_risk_snapshots_immutable
before update or delete on public.wow_kalshi_weather_portfolio_risk_snapshots
for each row execute function public.wow_reject_immutable_mutation();
