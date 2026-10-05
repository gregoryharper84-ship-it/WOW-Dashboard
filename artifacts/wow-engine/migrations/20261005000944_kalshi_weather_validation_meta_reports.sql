-- Kalshi Weather v1.4 P4 validation meta-layer persistence.
-- Append-only research/governance ledger. No execution capability.

create table public.wow_kalshi_weather_validation_meta_reports (
  report_id text primary key,
  as_of_time timestamptz not null,
  validation_meta_version text not null,
  input_manifest jsonb not null,
  sample_ids jsonb not null,
  counterfactual_sample_ids jsonb not null default '[]'::jsonb,
  model_scores jsonb not null,
  baseline_deltas jsonb not null default '[]'::jsonb,
  selective_curve jsonb not null,
  counterfactual_scores jsonb not null default '[]'::jsonb,
  red_team_results jsonb not null,
  complexity_gate jsonb not null,
  market_price_used_as_weather_input boolean not null default false,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false,

  constraint chk_kalshi_weather_validation_input_manifest
    check (
      jsonb_typeof(input_manifest) = 'object'
      and input_manifest ? 'version'
      and input_manifest ? 'as_of_time'
      and input_manifest ? 'probability_samples'
      and input_manifest ? 'complexity_policy'
      and jsonb_typeof(input_manifest->'probability_samples') = 'array'
      and jsonb_array_length(input_manifest->'probability_samples') > 0
    ),
  constraint chk_kalshi_weather_validation_sample_ids
    check (jsonb_typeof(sample_ids) = 'array' and jsonb_array_length(sample_ids) > 0),
  constraint chk_kalshi_weather_validation_counterfactual_ids
    check (jsonb_typeof(counterfactual_sample_ids) = 'array'),
  constraint chk_kalshi_weather_validation_model_scores
    check (jsonb_typeof(model_scores) = 'array' and jsonb_array_length(model_scores) > 0),
  constraint chk_kalshi_weather_validation_baseline_deltas
    check (jsonb_typeof(baseline_deltas) = 'array'),
  constraint chk_kalshi_weather_validation_selective_curve
    check (jsonb_typeof(selective_curve) = 'array' and jsonb_array_length(selective_curve) > 0),
  constraint chk_kalshi_weather_validation_counterfactual_scores
    check (jsonb_typeof(counterfactual_scores) = 'array'),
  constraint chk_kalshi_weather_validation_red_team
    check (jsonb_typeof(red_team_results) = 'array' and jsonb_array_length(red_team_results) > 0),
  constraint chk_kalshi_weather_validation_complexity_gate
    check (
      jsonb_typeof(complexity_gate) = 'object'
      and complexity_gate ? 'policy_identity'
      and complexity_gate ? 'recommendation'
      and complexity_gate ? 'blockers'
      and complexity_gate ? 'holdout_n'
      and complexity_gate ? 'selected_confidence_threshold'
      and complexity_gate ? 'can_execute'
      and complexity_gate->>'recommendation' in ('HOLD', 'ELIGIBLE_FOR_GOVERNED_REVIEW')
      and complexity_gate->>'can_execute' = 'false'
    ),
  constraint chk_kalshi_weather_validation_manifest_complexity_policy
    check (
      jsonb_typeof(input_manifest->'complexity_policy') = 'object'
      and (input_manifest->'complexity_policy') ? 'promotion_confidence_threshold'
      and (input_manifest->'complexity_policy') ? 'required_meteorological_baseline_kinds'
      and (input_manifest->'complexity_policy') ? 'required_red_team_categories'
      and jsonb_typeof(input_manifest->'complexity_policy'->'required_meteorological_baseline_kinds') = 'array'
      and jsonb_array_length(input_manifest->'complexity_policy'->'required_meteorological_baseline_kinds') > 0
      and jsonb_typeof(input_manifest->'complexity_policy'->'required_red_team_categories') = 'array'
      and jsonb_array_length(input_manifest->'complexity_policy'->'required_red_team_categories') > 0
      and not jsonb_path_exists(
        input_manifest,
        '$.complexity_policy.required_meteorological_baseline_kinds[*] ? (@ == "MARKET")'
      )
    ),
  constraint chk_kalshi_weather_validation_no_market_feedback
    check (market_price_used_as_weather_input = false),
  constraint chk_kalshi_weather_validation_execute_false
    check (can_execute = false),
  constraint chk_kalshi_weather_validation_manifest_no_market_feedback
    check (
      not jsonb_path_exists(
        input_manifest,
        '$.** ? (@.market_price_used_as_weather_input == true)'
      )
    ),
  constraint chk_kalshi_weather_validation_manifest_no_execute
    check (
      not jsonb_path_exists(
        input_manifest,
        '$.** ? (@.can_execute == true)'
      )
    ),
  constraint chk_kalshi_weather_validation_no_nested_execute
    check (
      not jsonb_path_exists(model_scores, '$[*] ? (@.can_execute == true)')
      and not jsonb_path_exists(baseline_deltas, '$[*] ? (@.can_execute == true)')
      and not jsonb_path_exists(selective_curve, '$[*] ? (@.can_execute == true)')
      and not jsonb_path_exists(counterfactual_scores, '$[*] ? (@.can_execute == true)')
      and not jsonb_path_exists(red_team_results, '$[*] ? (@.can_execute == true)')
      and not jsonb_path_exists(complexity_gate, '$ ? (@.can_execute == true)')
    )
);

create index idx_kalshi_weather_validation_meta_time
  on public.wow_kalshi_weather_validation_meta_reports (as_of_time desc);

alter table public.wow_kalshi_weather_validation_meta_reports enable row level security;

revoke all privileges on table public.wow_kalshi_weather_validation_meta_reports
  from anon, authenticated, service_role;

grant select, insert on table public.wow_kalshi_weather_validation_meta_reports
  to service_role;

create trigger trg_wow_kalshi_weather_validation_meta_reports_immutable
before update or delete on public.wow_kalshi_weather_validation_meta_reports
for each row execute function public.wow_reject_immutable_mutation();
