-- WOW V17 CFBD player-game sporting outcome normalization (RESEARCH ONLY).
-- Source eligibility: CFBD is V17_APPROVED in historical_source_manifest_v1.json.
-- Only observed postgame stats; never historical betting lines or fitted probabilities.
-- Deployed in validation via migration wow_ncaaf_outcome_normalization_research_v1_20261029.
-- Apply this file to fresh installs and for the new read-only-admission projector.

create schema if not exists wow_prop_history;
revoke all on schema wow_prop_history from public, anon, authenticated;
grant usage on schema wow_prop_history to service_role;

create table if not exists wow_prop_history.ncaaf_player_game_outcomes (
 outcome_id uuid primary key default gen_random_uuid(),
 source_snapshot_id uuid not null references public.wow_ncaaf_source_snapshots(source_snapshot_id),
 source_provider text not null default 'CFBD' check (source_provider='CFBD'),
 season integer not null check (season between 2000 and 2100),
 week integer not null check (week between 0 and 30),
 official_event_id text not null check (length(trim(official_event_id))>0),
 official_player_id text not null check (length(trim(official_player_id))>0),
 team text not null check (length(trim(team))>0),
 opponent text not null check (length(trim(opponent))>0 and opponent<>team),
 event_start_time timestamptz not null,
 observed_at timestamptz not null,
 stat_type text not null check (stat_type in ('PASSING_YARDS','COMPLETIONS','PASS_ATTEMPTS',
  'RUSHING_YARDS','RUSH_ATTEMPTS','RECEIVING_YARDS','RECEPTIONS')),
 actual_value numeric(12,3) not null check (actual_value between -1000 and 2000),
 source_payload_sha256 text not null check (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
 source_status text not null default 'NORMALIZED_RESEARCH_ONLY'
  check (source_status='NORMALIZED_RESEARCH_ONLY'),
 historical_book text default null check (historical_book is null),
 historical_closing_line numeric default null check (historical_closing_line is null),
 historical_odds numeric default null check (historical_odds is null),
 historical_market_timestamp timestamptz default null check (historical_market_timestamp is null),
 model_training_eligible boolean not null default false check (model_training_eligible=false),
 probability_publishable boolean not null default false check (probability_publishable=false),
 can_execute boolean not null default false check (can_execute=false),
 inserted_at timestamptz not null default now(),
 check (observed_at > event_start_time),
 check (stat_type in ('PASSING_YARDS','RUSHING_YARDS','RECEIVING_YARDS') or actual_value>=0),
 unique (source_snapshot_id,official_event_id,official_player_id,stat_type)
);
create index if not exists ncaaf_player_game_outcomes_event_idx
 on wow_prop_history.ncaaf_player_game_outcomes(season,week,official_event_id);
create index if not exists ncaaf_player_game_outcomes_player_idx
 on wow_prop_history.ncaaf_player_game_outcomes(official_player_id,stat_type,event_start_time);
alter table wow_prop_history.ncaaf_player_game_outcomes enable row level security;
revoke all on table wow_prop_history.ncaaf_player_game_outcomes
 from public, anon, authenticated, service_role;
grant select, insert on table wow_prop_history.ncaaf_player_game_outcomes to service_role;

-- Atomic per-week projection. A failure rolls back the whole week.
-- Invoker rights only; never SECURITY DEFINER. Must be called by a privileged
-- research operator, not via public Data API. No role may update/delete rows.
create or replace function wow_prop_history.normalize_ncaaf_player_week(
 p_season integer, p_week integer
) returns integer language plpgsql security invoker set search_path = ''
as $$
declare
 src record;
 source_n integer;
 bad_n integer;
 expected_n integer;
 existing_n integer;
begin
 if p_season not between 2000 and 2100 or p_week not between 0 and 30 then
   raise exception 'NCAAF_PROP_NORMALIZATION_RANGE_INVALID';
 end if;
 select count(*) into source_n
 from public.wow_ncaaf_source_snapshots
 where provider='CFBD' and endpoint='/games/players'
 and season=p_season and week=p_week and acquisition_status='AVAILABLE'
 and can_execute=false;
 if source_n<>1 then
   raise exception 'NCAAF_PROP_NORMALIZATION_SOURCE_AMBIGUOUS_OR_MISSING: %',source_n;
 end if;
 select s.* into strict src from public.wow_ncaaf_source_snapshots s
 where s.provider='CFBD' and s.endpoint='/games/players'
 and s.season=p_season and s.week=p_week and s.acquisition_status='AVAILABLE'
 and s.can_execute=false;
 -- Preserve strict event/team chronology and identity: no unrecognized match.
 select count(*) into bad_n
 from pg_catalog.jsonb_array_elements(src.response_rows) g
 left join public.wow_ncaaf_training_games game
 on game.official_event_id=g->>'id' and game.season=p_season
 where game.official_event_id is null
 or src.retrieved_at <= game.event_start_time
 or pg_catalog.jsonb_typeof(g->'teams')<>'array'
 or pg_catalog.jsonb_array_length(g->'teams')<>2
 or exists (
  select 1 from pg_catalog.jsonb_array_elements(g->'teams') t
  where t->>'team' not in (game.home_team,game.away_team)
  or pg_catalog.jsonb_typeof(t->'categories')<>'array'
 );
 if bad_n<>0 then raise exception 'NCAAF_PROP_NORMALIZATION_GAME_IDENTITY_INVALID: %',bad_n; end if;
 -- Check exact supported stat formats and player IDs before casting/inserting.
 with f as (
  select c->>'name' as category,st->>'name' as metric,
         a->>'id' as player_id,a->>'stat' as val
  from pg_catalog.jsonb_array_elements(src.response_rows) g
  cross join lateral pg_catalog.jsonb_array_elements(g->'teams') t
  cross join lateral pg_catalog.jsonb_array_elements(t->'categories') c
  cross join lateral pg_catalog.jsonb_array_elements(c->'types') st
  cross join lateral pg_catalog.jsonb_array_elements(st->'athletes') a
  where (c->>'name'='passing' and st->>'name' in ('YDS','C/ATT'))
    or (c->>'name'='rushing' and st->>'name' in ('YDS','CAR'))
    or (c->>'name'='receiving' and st->>'name' in ('YDS','REC'))
 )
 select count(*)::integer,
   count(*) filter (
    where coalesce(trim(player_id),'')=''
    or coalesce(trim(val),'')=''
    or (metric='C/ATT' and (
      val !~ '^[0-9]+/[0-9]+$'
      or split_part(val,'/',1)::numeric>split_part(val,'/',2)::numeric
    ))
    or (metric<>'C/ATT' and val !~ '^[+-]?[0-9]+(\.0+)?$')
   )::integer
 into expected_n,bad_n from f;
 if bad_n<>0 or expected_n=0 then
   raise exception 'NCAAF_PROP_NORMALIZATION_STAT_INVALID: %',bad_n;
 end if;
 -- C/ATT expands to COMPLETIONS and PASS_ATTEMPTS (two distinct outcomes).
 select expected_n + count(*)::integer into expected_n
 from pg_catalog.jsonb_array_elements(src.response_rows) g
 cross join lateral pg_catalog.jsonb_array_elements(g->'teams') t
 cross join lateral pg_catalog.jsonb_array_elements(t->'categories') c
 cross join lateral pg_catalog.jsonb_array_elements(c->'types') st
 cross join lateral pg_catalog.jsonb_array_elements(st->'athletes') a
 where c->>'name'='passing' and st->>'name'='C/ATT';
 -- The exact raw-row count plus one per passing completion/attempt athlete.
 with f as (
  select g->>'id' as event_id,t->>'team' as team,c->>'name' as category,
   st->>'name' as metric,a->>'id' as player_id,a->>'stat' as val
  from pg_catalog.jsonb_array_elements(src.response_rows) g
  cross join lateral pg_catalog.jsonb_array_elements(g->'teams') t
  cross join lateral pg_catalog.jsonb_array_elements(t->'categories') c
  cross join lateral pg_catalog.jsonb_array_elements(c->'types') st
  cross join lateral pg_catalog.jsonb_array_elements(st->'athletes') a
  where (c->>'name'='passing' and st->>'name' in ('YDS','C/ATT'))
    or (c->>'name'='rushing' and st->>'name' in ('YDS','CAR'))
    or (c->>'name'='receiving' and st->>'name' in ('YDS','REC'))
 ),
 mapped as (
  select f.*,game.event_start_time,
   case when f.team=game.home_team then game.away_team else game.home_team end as opponent,
   m.stat_type,m.actual_value
  from f join public.wow_ncaaf_training_games game
    on game.official_event_id=f.event_id and game.season=p_season
    and f.team in(game.home_team,game.away_team)
  cross join lateral (values
   ('PASSING_YARDS',case when f.category='passing' and f.metric='YDS' then f.val::numeric end),
   ('COMPLETIONS',case when f.category='passing' and f.metric='C/ATT' then split_part(f.val,'/',1)::numeric end),
   ('PASS_ATTEMPTS',case when f.category='passing' and f.metric='C/ATT' then split_part(f.val,'/',2)::numeric end),
   ('RUSHING_YARDS',case when f.category='rushing' and f.metric='YDS' then f.val::numeric end),
   ('RUSH_ATTEMPTS',case when f.category='rushing' and f.metric='CAR' then f.val::numeric end),
   ('RECEIVING_YARDS',case when f.category='receiving' and f.metric='YDS' then f.val::numeric end),
   ('RECEPTIONS',case when f.category='receiving' and f.metric='REC' then f.val::numeric end)
  ) m(stat_type,actual_value) where m.actual_value is not null
 )
 insert into wow_prop_history.ncaaf_player_game_outcomes
 (source_snapshot_id,season,week,official_event_id,official_player_id,
 team,opponent,event_start_time,observed_at,stat_type,actual_value,source_payload_sha256)
 select src.source_snapshot_id,p_season,p_week,event_id,player_id,team,opponent,
 event_start_time,src.retrieved_at,stat_type,actual_value,src.payload_sha256 from mapped
 on conflict(source_snapshot_id,official_event_id,official_player_id,stat_type) do nothing;
 select count(*) into existing_n
 from wow_prop_history.ncaaf_player_game_outcomes
 where source_snapshot_id=src.source_snapshot_id;
 if existing_n<>expected_n then
   raise exception 'NCAAF_PROP_NORMALIZATION_COUNT_MISMATCH: expected %, got %',
     expected_n,existing_n;
 end if;
 return existing_n;
end
$$;
revoke all on function wow_prop_history.normalize_ncaaf_player_week(integer,integer)
from public, anon, authenticated;
grant execute on function wow_prop_history.normalize_ncaaf_player_week(integer,integer)
to service_role;
