-- WOW V17 acquisition path observability (R2-restorative).
--
-- Additive/reversible source migration. Apply only through governed Supabase
-- release review after the application change is approved. Values are nullable
-- so existing rows remain valid. The columns store only closed path/status
-- vocabularies and configured credential alias names; never keys, URLs, raw
-- responses, provider messages, prices, probabilities, or participant data.

alter table public.wow_v17_daily_run_acquisition_detail
    add column if not exists primary_path_id text,
    add column if not exists primary_path_state text,
    add column if not exists primary_blocker_code text,
    add column if not exists fallback_path_id text,
    add column if not exists fallback_path_state text,
    add column if not exists fallback_blocker_code text,
    add column if not exists primary_upstream_status integer,
    add column if not exists primary_content_type_class text,
    add column if not exists primary_provider_alias text,
    add column if not exists fallback_upstream_status integer,
    add column if not exists fallback_content_type_class text,
    add column if not exists fallback_provider_alias text;

alter table public.wow_v17_daily_run_acquisition_detail
    add constraint wow_v17_acquisition_primary_path_id_closed
        check (primary_path_id is null or primary_path_id in (
            'ESPN_SCOREBOARD', 'ODDS_PROXY', 'RUNDOWN', 'GOVERNED_FALLBACK_UNION'
        )),
    add constraint wow_v17_acquisition_fallback_path_id_closed
        check (fallback_path_id is null or fallback_path_id in (
            'ESPN_SCOREBOARD', 'ODDS_PROXY', 'RUNDOWN', 'GOVERNED_FALLBACK_UNION'
        )),
    add constraint wow_v17_acquisition_primary_path_state_closed
        check (primary_path_state is null or primary_path_state in (
            'SUCCEEDED_EMPTY', 'SUCCEEDED_WITH_ROWS', 'FAILED_TYPED',
            'NOT_ATTEMPTED', 'NOT_APPLICABLE', 'CIRCUIT_OPEN_FROM_PRIOR_TYPED_FAILURE'
        )),
    add constraint wow_v17_acquisition_fallback_path_state_closed
        check (fallback_path_state is null or fallback_path_state in (
            'SUCCEEDED_EMPTY', 'SUCCEEDED_WITH_ROWS', 'FAILED_TYPED',
            'NOT_ATTEMPTED', 'NOT_APPLICABLE', 'CIRCUIT_OPEN_FROM_PRIOR_TYPED_FAILURE'
        )),
    add constraint wow_v17_acquisition_primary_upstream_status_http
        check (primary_upstream_status is null or primary_upstream_status between 100 and 599),
    add constraint wow_v17_acquisition_fallback_upstream_status_http
        check (fallback_upstream_status is null or fallback_upstream_status between 100 and 599),
    add constraint wow_v17_acquisition_primary_content_type_closed
        check (primary_content_type_class is null or primary_content_type_class in (
            'JSON', 'TEXT_HTML', 'TEXT_PLAIN', 'OTHER', 'EMPTY'
        )),
    add constraint wow_v17_acquisition_fallback_content_type_closed
        check (fallback_content_type_class is null or fallback_content_type_class in (
            'JSON', 'TEXT_HTML', 'TEXT_PLAIN', 'OTHER', 'EMPTY'
        )),
    add constraint wow_v17_acquisition_primary_alias_closed
        check (primary_provider_alias is null or primary_provider_alias in (
            'ODDS_API_PAID_KEY', 'ODDS_API_KEY_100K', 'ODDS_API_FREE_KEY', 'ODDS_API_KEY'
        )),
    add constraint wow_v17_acquisition_fallback_alias_closed
        check (fallback_provider_alias is null or fallback_provider_alias in (
            'ODDS_API_PAID_KEY', 'ODDS_API_KEY_100K', 'ODDS_API_FREE_KEY', 'ODDS_API_KEY'
        ));

comment on column public.wow_v17_daily_run_acquisition_detail.primary_provider_alias is
'Configured Odds API environment alias name only; never the credential value.';
comment on column public.wow_v17_daily_run_acquisition_detail.fallback_provider_alias is
'Configured Odds API environment alias name only; never the credential value.';

-- Existing RLS and grants are intentionally unchanged.

-- Deterministic rollback (after retained audit evidence is no longer required):
-- alter table public.wow_v17_daily_run_acquisition_detail
--   drop constraint if exists wow_v17_acquisition_primary_path_id_closed,
--   drop constraint if exists wow_v17_acquisition_fallback_path_id_closed,
--   drop constraint if exists wow_v17_acquisition_primary_path_state_closed,
--   drop constraint if exists wow_v17_acquisition_fallback_path_state_closed,
--   drop constraint if exists wow_v17_acquisition_primary_upstream_status_http,
--   drop constraint if exists wow_v17_acquisition_fallback_upstream_status_http,
--   drop constraint if exists wow_v17_acquisition_primary_content_type_closed,
--   drop constraint if exists wow_v17_acquisition_fallback_content_type_closed,
--   drop constraint if exists wow_v17_acquisition_primary_alias_closed,
--   drop constraint if exists wow_v17_acquisition_fallback_alias_closed,
--   drop column if exists primary_path_id,
--   drop column if exists primary_path_state,
--   drop column if exists primary_blocker_code,
--   drop column if exists fallback_path_id,
--   drop column if exists fallback_path_state,
--   drop column if exists fallback_blocker_code,
--   drop column if exists primary_upstream_status,
--   drop column if exists primary_content_type_class,
--   drop column if exists primary_provider_alias,
--   drop column if exists fallback_upstream_status,
--   drop column if exists fallback_content_type_class,
--   drop column if exists fallback_provider_alias;
