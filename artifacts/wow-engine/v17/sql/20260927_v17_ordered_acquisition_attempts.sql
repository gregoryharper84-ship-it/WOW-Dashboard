-- WOW V17 ordered acquisition-attempt provenance (R2-restorative).
--
-- Additive rollout: apply this migration before the application commit.
-- Existing rows remain readable because acquisition_attempts is nullable. The JSON array
-- is bounded to three closed records in actual ESPN -> Odds -> Rundown order.
-- It may never contain URLs, headers, messages, bodies, secrets, prices,
-- probabilities, participant data, or arbitrary/free-text fields.

create or replace function public.wow_v17_acquisition_attempts_valid(payload jsonb)
returns boolean
language plpgsql
immutable
set search_path = pg_catalog
as $$
declare
    attempt jsonb;
    expected_ordinal integer := 1;
    current_rank integer;
    prior_rank integer := 0;
    path_state text;
    blocker_code text;
    origin_code text;
    upstream_text text;
begin
    if payload is null then
        return true;
    end if;
    if jsonb_typeof(payload) <> 'array' or jsonb_array_length(payload) > 3 then
        return false;
    end if;

    for attempt in select value from jsonb_array_elements(payload)
    loop
        if jsonb_typeof(attempt) <> 'object' then
            return false;
        end if;
        if (select count(*) from jsonb_object_keys(attempt)) <> 8 then
            return false;
        end if;
        if exists (
            select 1
            from jsonb_object_keys(attempt) as key_name
            where key_name not in (
                'ordinal', 'path_id', 'path_state', 'blocker_code',
                'originating_blocker_code', 'upstream_status',
                'content_type_class', 'credential_alias'
            )
        ) then
            return false;
        end if;
        if jsonb_typeof(attempt -> 'ordinal') <> 'number'
           or (attempt ->> 'ordinal') !~ '^[1-3]$'
           or (attempt ->> 'ordinal')::integer <> expected_ordinal then
            return false;
        end if;

        if jsonb_typeof(attempt -> 'path_id') <> 'string'
           or jsonb_typeof(attempt -> 'path_state') <> 'string' then
            return false;
        end if;
        current_rank := case attempt ->> 'path_id'
            when 'ESPN_SCOREBOARD' then 1
            when 'ODDS_PROXY' then 2
            when 'RUNDOWN' then 3
            else 0
        end;
        if current_rank = 0 or current_rank <= prior_rank then
            return false;
        end if;
        prior_rank := current_rank;

        path_state := attempt ->> 'path_state';
        if path_state is null or path_state not in (
            'SUCCEEDED_EMPTY', 'SUCCEEDED_WITH_ROWS', 'FAILED_TYPED',
            'NOT_ATTEMPTED', 'NOT_APPLICABLE',
            'CIRCUIT_OPEN_FROM_PRIOR_TYPED_FAILURE'
        ) then
            return false;
        end if;

        blocker_code := attempt ->> 'blocker_code';
        origin_code := attempt ->> 'originating_blocker_code';
        if blocker_code is not null and (
            jsonb_typeof(attempt -> 'blocker_code') <> 'string'
            or blocker_code !~ '^[A-Z0-9_.-]{1,96}$'
        ) then
            return false;
        end if;
        if origin_code is not null and (
            jsonb_typeof(attempt -> 'originating_blocker_code') <> 'string'
            or origin_code !~ '^[A-Z0-9_.-]{1,96}$'
        ) then
            return false;
        end if;
        if path_state = 'CIRCUIT_OPEN_FROM_PRIOR_TYPED_FAILURE' then
            if origin_code is null then
                return false;
            end if;
        elsif origin_code is not null then
            return false;
        end if;

        upstream_text := attempt ->> 'upstream_status';
        if upstream_text is not null and (
            jsonb_typeof(attempt -> 'upstream_status') <> 'number'
            or upstream_text !~ '^[0-9]{3}$'
            or upstream_text::integer not between 100 and 599
        ) then
            return false;
        end if;
        if attempt ->> 'content_type_class' is not null
           and (
               jsonb_typeof(attempt -> 'content_type_class') <> 'string'
               or attempt ->> 'content_type_class' not in (
                   'JSON', 'TEXT_HTML', 'TEXT_PLAIN', 'OTHER', 'EMPTY'
               )
           ) then
            return false;
        end if;
        if attempt ->> 'credential_alias' is not null
           and (
               jsonb_typeof(attempt -> 'credential_alias') <> 'string'
               or attempt ->> 'credential_alias' not in (
                   'ODDS_API_PAID_KEY', 'ODDS_API_KEY_100K',
                   'ODDS_API_FREE_KEY', 'ODDS_API_KEY'
               )
           ) then
            return false;
        end if;
        expected_ordinal := expected_ordinal + 1;
    end loop;
    return true;
end;
$$;

alter table public.wow_v17_daily_run_acquisition_detail
    add column if not exists acquisition_attempts jsonb;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'wow_v17_acquisition_attempts_closed'
          and conrelid = 'public.wow_v17_daily_run_acquisition_detail'::regclass
    ) then
        alter table public.wow_v17_daily_run_acquisition_detail
            add constraint wow_v17_acquisition_attempts_closed
            check (public.wow_v17_acquisition_attempts_valid(acquisition_attempts));
    end if;
end;
$$;

comment on column public.wow_v17_daily_run_acquisition_detail.acquisition_attempts is
'Max-three ordered closed acquisition attempts; no raw provider data or numeric authority.';

-- Safe application rollback: revert the application commit and retain this
-- nullable column/function so already-written immutable evidence is not deleted.
-- A later governed retention migration may remove them only after audit expiry.
