-- WOW V17 NCAAF future-event player-form research snapshots.
-- Source is approved CFBD /games/players POSTGAME outcomes already observed
-- by WOW; the feature is frozen NOW before a still-upcoming *other* event.
-- Strictly research-only; roster, injury, book, fitted model, calibrated
-- probability, terminal publication, and wagering remain unverified.
create schema if not exists wow_prop_history;
revoke all on schema wow_prop_history from public,anon,authenticated;
grant usage on schema wow_prop_history to service_role;

create table if not exists wow_prop_history.ncaaf_forward_pregame_player_features (
 feature_id uuid primary key default gen_random_uuid(),
 official_event_id text not null check(length(trim(official_event_id))>0),
 official_player_id text not null check(length(trim(official_player_id))>0),
 source_provider text not null default 'CFBD' check(source_provider='CFBD'),
 season integer not null check(season=2026),
 team text not null check(length(trim(team))>0),
 opponent text not null check(length(trim(opponent))>0 and opponent<>team),
 stat_type text not null check(stat_type in (
  'PASSING_YARDS','COMPLETIONS','PASS_ATTEMPTS','RUSHING_YARDS',
  'RUSH_ATTEMPTS','RECEIVING_YARDS','RECEPTIONS'
 )),
 planned_kickoff timestamptz not null,
 schedule_captured_at timestamptz not null,
 schedule_source_snapshot_id uuid not null references public.wow_ncaaf_source_snapshots(source_snapshot_id),
 schedule_payload_sha256 text not null check(schedule_payload_sha256~'^[0-9a-f]{64}$'),
 feature_as_of timestamptz not null,
 latest_prior_result_observed_at timestamptz not null,
 latest_prior_game_start timestamptz not null,
 prior_games integer not null check(prior_games between 4 and 5),
 prior_mean numeric(12,4) not null,
 prior_results jsonb not null check(jsonb_typeof(prior_results)='array'
  and jsonb_array_length(prior_results) between 4 and 5),
 prior_results_sha256 text not null check(prior_results_sha256~'^[0-9a-f]{64}$'),
 source_rights_scope text not null default 'CFBD_V17_APPROVED_SPORTING_RESEARCH'
  check(source_rights_scope='CFBD_V17_APPROVED_SPORTING_RESEARCH'),
 current_roster_verified boolean not null default false check(current_roster_verified=false),
 exact_market_line_verified boolean not null default false check(exact_market_line_verified=false),
 model_training_eligible boolean not null default false check(model_training_eligible=false),
 probability_publishable boolean not null default false check(probability_publishable=false),
 can_execute boolean not null default false check(can_execute=false),
 inserted_at timestamptz not null default now(),
 check(schedule_captured_at<=feature_as_of and feature_as_of<planned_kickoff),
 check(latest_prior_result_observed_at<=feature_as_of),
 check(latest_prior_game_start<feature_as_of),
 unique(official_event_id,official_player_id,stat_type,feature_as_of)
);
create index if not exists ncaaf_forward_pregame_event_idx
 on wow_prop_history.ncaaf_forward_pregame_player_features(official_event_id,feature_as_of);
alter table wow_prop_history.ncaaf_forward_pregame_player_features enable row level security;
revoke all on wow_prop_history.ncaaf_forward_pregame_player_features
 from public,anon,authenticated,service_role;
grant select,insert on wow_prop_history.ncaaf_forward_pregame_player_features
 to service_role;

-- One authentic future event/stat per authorized research invocation.
-- No manual as-of argument: database transaction time is authoritative.
-- Historical player labels must already exist and be source-observed before now.
create or replace function wow_prop_history.freeze_ncaaf_future_player_form(
 p_event_id text, p_stat_type text
) returns jsonb
language plpgsql security invoker set search_path=''
as $$
declare
 s record;
 saved_n integer;
 capture_time timestamptz := pg_catalog.now();
begin
 if p_event_id is null or p_event_id !~ '^[0-9]{9,12}$'
    or p_stat_type not in ('PASSING_YARDS','COMPLETIONS','PASS_ATTEMPTS',
      'RUSHING_YARDS','RUSH_ATTEMPTS','RECEIVING_YARDS','RECEPTIONS') then
   raise exception 'NCAAF_FORWARD_FEATURE_EVENT_OR_STAT_UNSUPPORTED';
 end if;
 select
  snap.source_snapshot_id,snap.payload_sha256,snap.retrieved_at,
  g->>'id' event_id,g->>'homeTeam' home_team,g->>'awayTeam' away_team,
  (g->>'startDate')::timestamptz as kickoff
 into s
 from public.wow_ncaaf_source_snapshots snap
 cross join lateral pg_catalog.jsonb_array_elements(snap.response_rows) g
 where snap.provider='CFBD' and snap.endpoint='/games'
  and snap.acquisition_status='AVAILABLE' and snap.can_execute=false
  and snap.season=2026 and g->>'id'=p_event_id
  and g->>'completed'='false' and g->>'startDate' is not null
  and snap.retrieved_at<capture_time
 order by snap.retrieved_at desc,snap.source_snapshot_id desc
 limit 1;
 if not found or s.home_team is null or s.away_team is null
    or s.home_team=s.away_team or capture_time>=s.kickoff then
   raise exception 'NCAAF_FORWARD_FEATURE_UPCOMING_IDENTITY_UNVERIFIED';
 end if;
 if exists(select 1 from public.wow_ncaaf_training_games g
           where g.official_event_id=p_event_id) then
   raise exception 'NCAAF_FORWARD_FEATURE_EVENT_ALREADY_IN_RESULTS';
 end if;
 with eligible as (
  select o.official_event_id,o.official_player_id,o.team,o.stat_type,
   o.event_start_time,o.observed_at,o.actual_value,o.source_payload_sha256,
   pg_catalog.row_number() over(
    partition by o.official_player_id,o.team,o.stat_type
    order by o.event_start_time desc,o.official_event_id desc
   ) as prior_rank
  from wow_prop_history.ncaaf_player_game_outcomes o
  where o.season=2026 and o.stat_type=p_stat_type
   and o.team in(s.home_team,s.away_team)
   and o.official_event_id<>p_event_id
   and o.event_start_time<s.kickoff and o.observed_at<capture_time
   and o.source_provider='CFBD' and o.source_status='NORMALIZED_RESEARCH_ONLY'
   and o.can_execute=false and o.probability_publishable=false
 ),
 prior as (
  select official_player_id,team,stat_type,count(*)::int prior_n,
   avg(actual_value)::numeric(12,4) prior_avg,
   max(observed_at) latest_observed,max(event_start_time) latest_game,
   pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
    'official_event_id',official_event_id,'actual_value',actual_value,
    'event_start_time',event_start_time,'source_observed_at',observed_at,
    'source_payload_sha256',source_payload_sha256
   ) order by event_start_time,official_event_id) prior_json
  from eligible where prior_rank<=5
  group by official_player_id,team,stat_type
  having count(*) between 4 and 5
 ), inserted as (
  insert into wow_prop_history.ncaaf_forward_pregame_player_features (
   official_event_id,official_player_id,season,team,opponent,stat_type,
   planned_kickoff,schedule_captured_at,schedule_source_snapshot_id,schedule_payload_sha256,
   feature_as_of,latest_prior_result_observed_at,latest_prior_game_start,
   prior_games,prior_mean,prior_results,prior_results_sha256
  )
  select p_event_id,p.official_player_id,2026,p.team,
   case when p.team=s.home_team then s.away_team else s.home_team end,
   p.stat_type,s.kickoff,s.retrieved_at,s.source_snapshot_id,s.payload_sha256,
   capture_time,p.latest_observed,p.latest_game,p.prior_n,p.prior_avg,
   p.prior_json,pg_catalog.encode(public.digest(p.prior_json::text,'sha256'),'hex')
  from prior p
  returning feature_id
 )
 select count(*)::int into saved_n from inserted;
 if saved_n=0 then
   raise exception 'NCAAF_FORWARD_FEATURE_NO_ELIGIBLE_PRIOR_PLAYER_FORM';
 end if;
 return pg_catalog.jsonb_build_object(
  'status','NCAAF_FORWARD_PIT_PLAYER_FORM_RESEARCH_FROZEN',
  'official_event_id',p_event_id,
  'stat_type',p_stat_type,
  'planned_kickoff',s.kickoff,
  'feature_as_of',capture_time,
  'saved_research_player_n',saved_n,
  'roster_confirmed',false,
  'model_training_eligible',false,
  'probability_publishable',false,
  'can_execute',false
 );
end $$;
revoke all on function wow_prop_history.freeze_ncaaf_future_player_form(text,text)
 from public,anon,authenticated;
grant execute on function wow_prop_history.freeze_ncaaf_future_player_form(text,text)
 to service_role;
