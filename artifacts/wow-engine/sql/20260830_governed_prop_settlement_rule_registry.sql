-- Applied to the production WOW Supabase project on 2026-08-30.
-- Server-owned settlement authority. No row can authorize execution.

create table if not exists public.wow_prop_settlement_rule_registry (
  rule_id uuid primary key default gen_random_uuid(),
  provider text not null,
  sport text not null,
  stat_type text not null default '*',
  period text not null,
  direction text not null check (direction in ('MORE','LESS')),
  settlement_basis text not null,
  boundary_operator text not null check (boundary_operator in ('GT','GE','LT','LE')),
  equality_treatment text not null check (equality_treatment in ('PUSH','WIN','LOSS')),
  void_treatment text not null check (void_treatment in ('RETURN_STAKE','REMOVE_LEG_REPRICE')),
  money_semantics text not null check (money_semantics in ('FIXED_ODDS_RETURN_STAKE','LINEUP_CONTEXT_REQUIRED')),
  rule_version text not null,
  source_ref text not null,
  source_refs jsonb not null default '[]'::jsonb,
  source_observed_at timestamptz not null,
  source_hash text not null,
  effective_from timestamptz not null,
  effective_to timestamptz null,
  lifecycle_state text not null check (lifecycle_state in ('REVIEWED_CERTIFIED','RETIRED')),
  notes jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  can_execute boolean not null default false check (can_execute = false),
  constraint wow_prop_settlement_rule_effective_window check (effective_to is null or effective_to > effective_from)
);

create unique index if not exists wow_prop_settlement_rule_registry_uq
on public.wow_prop_settlement_rule_registry(provider, sport, stat_type, period, direction, rule_version);

create index if not exists wow_prop_settlement_rule_lookup_idx
on public.wow_prop_settlement_rule_registry(provider, sport, period, direction, effective_from desc)
where lifecycle_state = 'REVIEWED_CERTIFIED';

alter table public.wow_prop_settlement_rule_registry enable row level security;

-- PrizePicks rows were seeded from current official provider documentation:
-- DNPs/Reboots/Ties (updated 2026-08-11), Reboots (current MLB/WNBA rules),
-- and Official Scoring. The production migration ledger is the canonical copy
-- of those seeded rows and source fingerprints.
