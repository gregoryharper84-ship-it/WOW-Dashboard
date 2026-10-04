-- Database-level attribution guard for Kalshi Weather probability changes.
-- Preserve the invariant that market/execution state never becomes a cause of P_weather.

alter table public.wow_kalshi_weather_probability_changes
  add constraint chk_kalshi_weather_change_no_market_attribution
  check (
    not jsonb_path_exists(
      attribution_components,
      '$[*] ? (@.domain == "MARKET_STATE" || @.domain == "EXECUTION_FRICTION")'
    )
  ),
  add constraint chk_kalshi_weather_change_no_market_feedback_flag
  check (
    not jsonb_path_exists(
      attribution_components,
      '$[*] ? (@.market_price_used_as_weather_input == true)'
    )
  ),
  add constraint chk_kalshi_weather_change_component_execute_false
  check (
    not jsonb_path_exists(
      attribution_components,
      '$[*] ? (@.can_execute == true)'
    )
  );
