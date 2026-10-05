-- V17 JS Style Intelligence learning ledger
-- Research/learning only. No probability or execution authority.

create schema if not exists wow_js_style;

create table if not exists wow_js_style.learning_examples (
    example_id text primary key,
    source_type text not null default 'USER_SUPPLIED_SCREENSHOT',
    source_ref text,
    sport text,
    league text,
    event_id text,
    player text,
    team text,
    opponent text,
    stat_type text,
    period text,
    exact_line numeric,
    direction text,
    offer_type text,
    entry_timestamp timestamptz,
    selected_by_js boolean not null default true,
    pregame_feature_snapshot jsonb,
    pregame_snapshot_available boolean not null default false,
    feature_replay_required boolean not null default true,
    outcome jsonb not null default '{}'::jsonb,
    hindsight_guard text not null default 'POSTGAME_FIELDS_NEVER_FEED_SELECTION_SCORE',
    probability_authority text not null default 'CONTROLLING_SPECIALIST_ONLY',
    terminal_authority text not null default 'V17_TERMINAL_REDUCER',
    can_execute boolean not null default false,
    created_at timestamptz not null default now(),
    constraint js_style_direction_check
        check (direction is null or direction in ('MORE','LESS')),
    constraint js_style_no_execution_check
        check (can_execute = false)
);

create index if not exists idx_js_style_learning_sport_created
    on wow_js_style.learning_examples (sport, created_at desc);

create index if not exists idx_js_style_learning_event
    on wow_js_style.learning_examples (event_id)
    where event_id is not null;

create index if not exists idx_js_style_learning_replay
    on wow_js_style.learning_examples (feature_replay_required, created_at desc);

comment on table wow_js_style.learning_examples is
'Append-only research ledger for externally observed JS-style prop selections. Pregame features and realized outcomes remain separated; rows have no probability or execution authority.';

comment on column wow_js_style.learning_examples.pregame_feature_snapshot is
'Immutable pregame-only features when available. Never reconstruct from the settled outcome.';

comment on column wow_js_style.learning_examples.outcome is
'Post-settlement observation stored separately from pregame selection features.';
