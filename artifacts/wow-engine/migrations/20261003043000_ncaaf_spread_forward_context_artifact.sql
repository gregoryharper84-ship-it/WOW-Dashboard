-- V17 NCAAF spread-forward persisted serving context.
--
-- Class B serving reliability only. The row stores the exact existing
-- research-only fitted artifact plus immutable settled-event history so the
-- constrained Render web process never fits or pages the full corpus at startup.
-- No production probability/certification/promotion/execution authority is added.

create table if not exists public.wow_ncaaf_spread_forward_context_artifacts (
    context_checksum text primary key,
    schema_version text not null,
    sport text not null check (sport = 'NCAAF'),
    model_family text not null,
    feature_schema_version text not null,
    training_dataset_hash text not null,
    source_fingerprint jsonb not null,
    latest_training_event timestamptz not null,
    artifact_payload jsonb not null,
    settled_events jsonb not null,
    settled_event_count integer not null check (settled_event_count > 0),
    builder text not null,
    built_at timestamptz not null default now(),
    probability_publishable boolean not null default false check (probability_publishable = false),
    automatic_certification boolean not null default false check (automatic_certification = false),
    automatic_promotion boolean not null default false check (automatic_promotion = false),
    can_execute boolean not null default false check (can_execute = false)
);

create index if not exists wow_ncaaf_spread_forward_context_built_idx
    on public.wow_ncaaf_spread_forward_context_artifacts (built_at desc);

alter table public.wow_ncaaf_spread_forward_context_artifacts enable row level security;

comment on table public.wow_ncaaf_spread_forward_context_artifacts is
'Append-only V17 NCAAF spread-forward research context. Exact fitted artifact + immutable settled history; probability_publishable=false, can_execute=false.';
