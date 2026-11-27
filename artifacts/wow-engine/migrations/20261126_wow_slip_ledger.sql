-- WOW unified paper-trade slip ledger (PrizePicks + Kalshi).
-- Hard lock: can_execute must remain false on every row. No live execution.
-- Evolves the PrizePicks-only 20261125 schema without destroying existing rows:
-- legacy tables are renamed, new tables created, then rows are backfilled.
-- Apply only after protected review; service-role writes, no anon/authenticated mutation.

drop view if exists public.wow_archetype_performance;

-- Preserve any already-recorded research ledger rows.
do $$
begin
  if to_regclass('public.wow_slip_grades') is not null
     and to_regclass('public.wow_slip_grades_pre_20261126') is null then
    execute 'alter table public.wow_slip_grades rename to wow_slip_grades_pre_20261126';
  end if;
  if to_regclass('public.wow_slip_snapshots') is not null
     and to_regclass('public.wow_slip_snapshots_pre_20261126') is null then
    execute 'alter table public.wow_slip_snapshots rename to wow_slip_snapshots_pre_20261126';
  end if;
end $$;

create table if not exists public.wow_slip_snapshots (
  slip_id uuid primary key,
  created_at timestamptz not null default now(),
  platform text not null check (platform in ('prizepicks', 'kalshi')),
  archetype text not null check (archetype in (
    'nba_niche_under_early',
    'nba_4_pack_fade',
    'nba_half_pace_fade',
    'nba_micro_volume_over',
    'nfl_1q_kicker_combo',
    'nfl_niche_fade',
    'core_2p_value_power',
    'soccer_possession_core_2p',
    'soccer_possession_stack_flex',
    'cs2_kills_under_power',
    'kalshi_nfl_qb_stack_sgp',
    'kalshi_same_player_double_sgp',
    'kalshi_nba_boards_dimes_sgp'
  )),
  slip_type text not null check (slip_type in (
    '2_pick_power',
    '3_pick_power',
    '4_pick_power',
    '5_pick_power',
    '6_pick_power',
    '6_pick_flex',
    'kalshi_sgp_2',
    'kalshi_sgp_3'
  )),
  payout_multiplier numeric(6,2) not null,
  recommended_unit_scale numeric(4,2) not null default 1.00,
  joint_calibrated_prob numeric(8,5) not null,
  expected_value_roi numeric(8,4) not null,
  min_leg_lower_bound numeric(6,4) not null,
  unprotected_leg_count integer not null default 0,
  signal_sources text[] not null default '{}',
  can_execute boolean not null default false check (can_execute = false),
  legs_json jsonb not null,
  constraint wow_slip_snapshots_legs_is_array check (jsonb_typeof(legs_json) = 'array')
);

create table if not exists public.wow_slip_grades (
  slip_id uuid primary key
    references public.wow_slip_snapshots (slip_id) on delete cascade,
  graded_at timestamptz not null default now(),
  platform text not null,
  archetype text not null,
  legs_won integer not null check (legs_won >= 0),
  legs_lost integer not null check (legs_lost >= 0),
  legs_pushed integer not null check (legs_pushed >= 0),
  effective_multiplier numeric(6,2) not null,
  realized_net_units numeric(8,4) not null,
  hold_to_settlement_net_units numeric(8,4) not null,
  exit_alpha_units numeric(8,4) not null,
  exit_action text,
  fragile_win_count integer not null default 0 check (fragile_win_count >= 0),
  graded_legs_json jsonb not null,
  constraint wow_slip_grades_legs_is_array check (jsonb_typeof(graded_legs_json) = 'array')
);

-- Backfill preserved 20261125 research rows into the unified schema.
do $$
begin
  if to_regclass('public.wow_slip_snapshots_pre_20261126') is not null then
    insert into public.wow_slip_snapshots (
      slip_id,
      created_at,
      platform,
      archetype,
      slip_type,
      payout_multiplier,
      recommended_unit_scale,
      joint_calibrated_prob,
      expected_value_roi,
      min_leg_lower_bound,
      unprotected_leg_count,
      signal_sources,
      can_execute,
      legs_json
    )
    select
      s.slip_id,
      coalesce(s.built_at, timezone('utc', now())),
      'prizepicks',
      s.archetype,
      s.product,
      s.multiplier,
      1.00,
      0.00000,
      0.0000,
      0.0000,
      0,
      '{}'::text[],
      false,
      s.legs_json
    from public.wow_slip_snapshots_pre_20261126 as s
    where s.can_execute = false
      and s.product in (
        '2_pick_power',
        '3_pick_power',
        '4_pick_power',
        '5_pick_power',
        '6_pick_power',
        '6_pick_flex'
      )
      and s.archetype in (
        'nba_niche_under_early',
        'nba_4_pack_fade',
        'nba_half_pace_fade',
        'nba_micro_volume_over',
        'nfl_1q_kicker_combo',
        'nfl_niche_fade',
        'core_2p_value_power',
        'soccer_possession_core_2p',
        'soccer_possession_stack_flex',
        'cs2_kills_under_power'
      )
    on conflict (slip_id) do nothing;
  end if;

  if to_regclass('public.wow_slip_grades_pre_20261126') is not null then
    insert into public.wow_slip_grades (
      slip_id,
      graded_at,
      platform,
      archetype,
      legs_won,
      legs_lost,
      legs_pushed,
      effective_multiplier,
      realized_net_units,
      hold_to_settlement_net_units,
      exit_alpha_units,
      exit_action,
      fragile_win_count,
      graded_legs_json
    )
    select
      g.slip_id,
      coalesce(g.graded_at, timezone('utc', now())),
      'prizepicks',
      g.archetype,
      g.wins,
      g.losses,
      g.pushes,
      g.effective_multiplier,
      g.net_units,
      g.net_units,
      0.0000,
      null,
      coalesce(g.fragile_win_count, 0),
      g.graded_legs_json
    from public.wow_slip_grades_pre_20261126 as g
    join public.wow_slip_snapshots as s
      on s.slip_id = g.slip_id
    where g.can_execute = false
    on conflict (slip_id) do nothing;
  end if;
end $$;

alter table public.wow_slip_snapshots enable row level security;
alter table public.wow_slip_grades enable row level security;

revoke all on table public.wow_slip_snapshots from public, anon, authenticated;
revoke all on table public.wow_slip_grades from public, anon, authenticated;

revoke update, delete, truncate, references, trigger
  on table public.wow_slip_snapshots from service_role;
revoke update, delete, truncate, references, trigger
  on table public.wow_slip_grades from service_role;

grant select, insert on table public.wow_slip_snapshots to service_role;
grant select, insert on table public.wow_slip_grades to service_role;

-- Legacy preserved tables stay readable for audit/rollback; no client mutation.
do $$
begin
  if to_regclass('public.wow_slip_snapshots_pre_20261126') is not null then
    execute 'alter table public.wow_slip_snapshots_pre_20261126 enable row level security';
    execute 'revoke all on table public.wow_slip_snapshots_pre_20261126 from public, anon, authenticated';
    execute 'grant select on table public.wow_slip_snapshots_pre_20261126 to service_role';
  end if;
  if to_regclass('public.wow_slip_grades_pre_20261126') is not null then
    execute 'alter table public.wow_slip_grades_pre_20261126 enable row level security';
    execute 'revoke all on table public.wow_slip_grades_pre_20261126 from public, anon, authenticated';
    execute 'grant select on table public.wow_slip_grades_pre_20261126 to service_role';
  end if;
end $$;

create or replace view public.wow_archetype_performance
with (security_invoker = true, security_barrier = true)
as
select
  s.platform,
  s.archetype,
  count(s.slip_id)::bigint as total_slips,
  coalesce(sum(g.legs_won + g.legs_lost), 0)::bigint as decided_legs,
  case
    when coalesce(sum(g.legs_won + g.legs_lost), 0) = 0 then null
    else round(
      100.0 * sum(g.legs_won)::numeric / sum(g.legs_won + g.legs_lost)::numeric,
      4
    )
  end as leg_hit_rate_pct,
  case
    when coalesce(sum(g.legs_won), 0) = 0 then null
    else round(
      100.0 * sum(g.fragile_win_count)::numeric / sum(g.legs_won)::numeric,
      4
    )
  end as fragile_win_share_pct,
  coalesce(sum(g.realized_net_units), 0.0)::numeric as total_realized_units,
  coalesce(sum(g.hold_to_settlement_net_units), 0.0)::numeric as total_hold_units,
  coalesce(sum(g.exit_alpha_units), 0.0)::numeric as total_exit_alpha_units,
  case
    when count(g.slip_id) = 0 then null
    else round(
      100.0 * sum(g.realized_net_units)::numeric / count(g.slip_id)::numeric,
      4
    )
  end as slip_roi_pct,
  case
    when s.platform = 'prizepicks' then (
      coalesce(sum(g.legs_won + g.legs_lost), 0) >= 100
      and (
        sum(g.legs_won)::double precision
        / nullif(sum(g.legs_won + g.legs_lost)::double precision, 0.0)
      ) > 0.56
    )
    when s.platform = 'kalshi' then (
      count(g.slip_id) >= 25
      and coalesce(
        100.0 * sum(g.realized_net_units)::numeric / nullif(count(g.slip_id), 0)::numeric,
        0
      ) > 10.0
      and coalesce(sum(g.exit_alpha_units), 0.0) >= 0
    )
    else false
  end as clears_promotion_gate
from public.wow_slip_snapshots as s
left join public.wow_slip_grades as g
  on g.slip_id = s.slip_id
where s.can_execute = false
group by s.platform, s.archetype;

revoke all on public.wow_archetype_performance from public, anon, authenticated;
grant select on public.wow_archetype_performance to service_role;
