-- WOW paper-trade slip ledger (PrizePicks Power Play research only).
-- Hard lock: can_execute must remain false on every row. No live execution.
-- Apply only after protected review; service-role writes, no anon/authenticated mutation.

create table if not exists public.wow_slip_snapshots (
  slip_id uuid primary key,
  archetype text not null,
  product text not null
    check (product in ('4_pick_power', '5_pick_power', '6_pick_power')),
  multiplier double precision not null check (multiplier > 0),
  primary_direction text not null check (primary_direction in ('MORE', 'LESS')),
  captured_at timestamptz not null,
  built_at timestamptz not null default timezone('utc', now()),
  stake_units double precision not null default 1.0 check (stake_units > 0),
  lane text not null default 'WOW_PROP_LANE' check (lane = 'WOW_PROP_LANE'),
  can_execute boolean not null default false check (can_execute = false),
  legs_json jsonb not null,
  constraint wow_slip_snapshots_legs_is_array check (jsonb_typeof(legs_json) = 'array')
);

create table if not exists public.wow_slip_grades (
  slip_id uuid primary key
    references public.wow_slip_snapshots (slip_id) on delete cascade,
  archetype text not null,
  product text not null,
  total_legs integer not null check (total_legs > 0),
  wins integer not null check (wins >= 0),
  losses integer not null check (losses >= 0),
  pushes integer not null check (pushes >= 0),
  active_legs integer not null check (active_legs >= 0),
  original_multiplier double precision not null,
  effective_multiplier double precision not null check (effective_multiplier >= 0),
  net_units double precision not null,
  fragile_win_count integer not null default 0 check (fragile_win_count >= 0),
  graded_at timestamptz not null default timezone('utc', now()),
  can_execute boolean not null default false check (can_execute = false),
  graded_legs_json jsonb not null,
  constraint wow_slip_grades_legs_is_array check (jsonb_typeof(graded_legs_json) = 'array'),
  constraint wow_slip_grades_counts_add_up check (
    wins + losses + pushes = total_legs
    and active_legs = total_legs - pushes
  )
);

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

create or replace view public.wow_archetype_performance
with (security_invoker = true, security_barrier = true)
as
select
  s.archetype,
  count(s.slip_id)::bigint as total_slips,
  coalesce(sum(g.wins + g.losses), 0)::bigint as decided_legs,
  case
    when coalesce(sum(g.wins + g.losses), 0) = 0 then null
    else (sum(g.wins)::double precision / sum(g.wins + g.losses)::double precision)
  end as leg_hit_rate,
  case
    when coalesce(sum(g.wins + g.losses), 0) = 0 then null
    else round(
      100.0 * sum(g.wins)::numeric / sum(g.wins + g.losses)::numeric,
      4
    )
  end as leg_hit_rate_pct,
  case
    when coalesce(sum(g.wins), 0) = 0 then null
    else round(
      100.0 * sum(g.fragile_win_count)::numeric / sum(g.wins)::numeric,
      4
    )
  end as fragile_win_share_pct,
  coalesce(sum(g.net_units), 0.0)::double precision as total_net_units,
  case
    when count(g.slip_id) = 0 then null
    else round(
      100.0 * sum(g.net_units)::numeric / count(g.slip_id)::numeric,
      4
    )
  end as slip_roi_pct,
  (
    coalesce(sum(g.wins + g.losses), 0) >= 100
    and (
      sum(g.wins)::double precision
      / nullif(sum(g.wins + g.losses)::double precision, 0.0)
    ) > 0.56
  ) as clears_promotion_gate
from public.wow_slip_snapshots as s
left join public.wow_slip_grades as g
  on g.slip_id = s.slip_id
where s.can_execute = false
group by s.archetype;

revoke all on public.wow_archetype_performance from public, anon, authenticated;
grant select on public.wow_archetype_performance to service_role;
