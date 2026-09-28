-- WOW V17 JS-style research learning ledger.
-- Additive, research-only, service-role-only. No probability or execution authority.

create table if not exists public.wow_js_board_snapshots (
    board_snapshot_id text primary key,
    provider text not null,
    captured_at timestamptz not null,
    as_of timestamptz not null,
    source_snapshot_digest text not null,
    feature_schema_version text not null,
    ruleset_version text not null,
    js_probability_authority boolean not null default false check (js_probability_authority = false),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now(),
    unique (provider, captured_at, source_snapshot_digest, feature_schema_version)
);

create table if not exists public.wow_js_candidate_observations (
    observation_id text primary key,
    board_snapshot_id text not null references public.wow_js_board_snapshots(board_snapshot_id),
    provider_event_alias text,
    canonical_event_id text,
    participant_id text,
    participant_name text not null,
    sport text not null,
    stat text not null,
    period text not null,
    exact_settlement_threshold double precision not null,
    direction text not null check (direction in ('MORE', 'LESS')),
    promo_type text,
    settlement_rule_version text not null,
    archetypes jsonb not null default '[]'::jsonb,
    js_research_priority double precision check (
        js_research_priority is null or (js_research_priority >= 0 and js_research_priority <= 100)
    ),
    controlling_specialist_identity text,
    governed_scoring_status text,
    governed_typed_blocker text,
    selected_by_js boolean,
    eventual_settlement text,
    observation_metadata jsonb not null default '{}'::jsonb,
    feature_schema_version text not null,
    ruleset_version text not null,
    js_probability_authority boolean not null default false check (js_probability_authority = false),
    probability_publishable boolean not null default false check (probability_publishable = false),
    rank_eligible boolean not null default false check (rank_eligible = false),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

create table if not exists public.wow_js_selection_events (
    selection_event_id text primary key,
    observation_id text not null references public.wow_js_candidate_observations(observation_id),
    selected_by_js boolean not null,
    selected_at timestamptz not null,
    selection_source text not null,
    js_probability_authority boolean not null default false check (js_probability_authority = false),
    can_execute boolean not null default false check (can_execute = false),
    created_at timestamptz not null default now()
);

create index if not exists wow_js_candidate_observations_snapshot_idx
    on public.wow_js_candidate_observations (board_snapshot_id, sport, stat, period);
create index if not exists wow_js_selection_events_observation_idx
    on public.wow_js_selection_events (observation_id, selected_at);

alter table public.wow_js_board_snapshots enable row level security;
alter table public.wow_js_candidate_observations enable row level security;
alter table public.wow_js_selection_events enable row level security;

revoke all on table public.wow_js_board_snapshots from anon, authenticated;
revoke all on table public.wow_js_candidate_observations from anon, authenticated;
revoke all on table public.wow_js_selection_events from anon, authenticated;

grant all on table public.wow_js_board_snapshots to service_role;
grant all on table public.wow_js_candidate_observations to service_role;
grant all on table public.wow_js_selection_events to service_role;

comment on table public.wow_js_board_snapshots is
'Immutable JS-style research board snapshots. No sporting probability or execution authority.';
comment on table public.wow_js_candidate_observations is
'JS-style descriptive candidate observations linked to governed scorer status; never probability authority.';
comment on table public.wow_js_selection_events is
'Observed JS-style selection labels for future imitation research; selected_by_js is not sporting outcome.';
