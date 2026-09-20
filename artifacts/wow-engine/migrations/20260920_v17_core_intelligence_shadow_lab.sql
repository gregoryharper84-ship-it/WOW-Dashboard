-- WOW V17 Core Intelligence paired champion/challenger shadow evidence.
-- Apply after 20260920_v17_core_intelligence_compounding.sql.

create table if not exists public.wow_intelligence_shadow_rows (
    shadow_row_id uuid primary key,
    challenger_id text not null,
    target_key text not null,
    source_row_id text not null,
    outcome_target smallint not null check (outcome_target in (0,1)),
    champion_probability numeric not null check (champion_probability > 0 and champion_probability < 1),
    challenger_probability numeric not null check (challenger_probability > 0 and challenger_probability < 1),
    champion_brier numeric not null check (champion_brier >= 0),
    challenger_brier numeric not null check (challenger_brier >= 0),
    champion_log_loss numeric not null check (champion_log_loss >= 0),
    challenger_log_loss numeric not null check (challenger_log_loss >= 0),
    schema_version text not null,
    authority text not null default 'ADVISORY_ONLY' check (authority='ADVISORY_ONLY'),
    can_execute boolean not null default false check (can_execute=false),
    created_at timestamptz not null default now(),
    unique (challenger_id, target_key, source_row_id, schema_version)
);

create index if not exists idx_wow_intelligence_shadow_challenger
on public.wow_intelligence_shadow_rows(challenger_id, target_key, created_at);

drop trigger if exists trg_wow_intelligence_shadow_rows_immutable
on public.wow_intelligence_shadow_rows;
create trigger trg_wow_intelligence_shadow_rows_immutable
before update or delete on public.wow_intelligence_shadow_rows
for each row execute function public.wow_core_intelligence_block_mutation();

revoke all on public.wow_intelligence_shadow_rows from public, anon, authenticated;
grant select, insert on public.wow_intelligence_shadow_rows to service_role;
