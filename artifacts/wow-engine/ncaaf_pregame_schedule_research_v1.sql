-- Actual observed-before-kickoff CFBD /games snapshots, research-only.
-- This is schedule/identity provenance, NOT player/lineup/injury/prop features.
-- Captured game start may later change; retain both versions explicitly.
-- Does not prove a fitted model or license sportsbook market snapshots.
create or replace view wow_prop_history.ncaaf_pregame_schedule_research_v1
with (security_invoker=true, security_barrier=true)
as
with raw as (
  select
    s.season,
    s.source_snapshot_id,
    s.payload_sha256 as source_payload_sha256,
    s.retrieved_at as source_captured_at,
    g.value->>'id' as official_event_id_claimed,
    g.value->>'homeTeam' as captured_home_team,
    g.value->>'awayTeam' as captured_away_team,
    (g.value->>'startDate')::timestamptz as captured_kickoff,
    g.value->>'completed' as captured_completed
  from public.wow_ncaaf_source_snapshots s
  cross join lateral pg_catalog.jsonb_array_elements(s.response_rows) g
  where s.provider='CFBD'
    and s.endpoint='/games'
    and s.acquisition_status='AVAILABLE'
    and s.can_execute=false
    and s.season=2026
    and g.value->>'startDate' is not null
    and g.value->>'completed'='false'
), valid as (
  select *
  from raw
  where source_captured_at < captured_kickoff
    and official_event_id_claimed is not null
    and length(trim(official_event_id_claimed)) > 0
    and captured_home_team is not null
    and captured_away_team is not null
    and captured_home_team <> captured_away_team
    and source_payload_sha256 ~ '^[0-9a-f]{64}$'
), earliest as (
  select *,
    row_number() over (
      partition by official_event_id_claimed
      order by source_captured_at,source_snapshot_id
    ) as capture_rank
  from valid
)
select
 e.season,
 e.official_event_id_claimed,
 e.source_snapshot_id,
 e.source_payload_sha256,
 e.source_captured_at,
 e.captured_kickoff,
 e.captured_home_team,
 e.captured_away_team,
 t.event_start_time as settled_kickoff,
 t.home_team as settled_home_team,
 t.away_team as settled_away_team,
 case
   when t.official_event_id is null
      then 'NOT_YET_SETTLED_IN_LEDGER'
   when e.source_captured_at >= t.event_start_time
      then 'CAPTURE_NOT_PREGAME_RELATIVE_TO_SETTLED_KICKOFF'
   when e.captured_home_team<>t.home_team or e.captured_away_team<>t.away_team
      then 'MATCHUP_IDENTITY_CHANGED'
   when e.captured_kickoff<>t.event_start_time
      then 'CAPTURED_KICKOFF_CHANGED'
   else 'MATCHED_SETTLED_SCHEDULE'
 end as schedule_reconciliation,
 e.source_captured_at < e.captured_kickoff as pregame_vs_captured_time,
 case
   when t.official_event_id is null then null::boolean
   else e.source_captured_at < t.event_start_time
 end as pregame_vs_settled_time,
 true::boolean as captured_schedule_source_provenance_verified,
 false::boolean as player_prop_feature_pit_verified,
 false::boolean as model_training_eligible,
 false::boolean as probability_publishable,
 false::boolean as can_execute
from earliest e
left join public.wow_ncaaf_training_games t
  on t.official_event_id=e.official_event_id_claimed
where e.capture_rank=1;

revoke all on wow_prop_history.ncaaf_pregame_schedule_research_v1
from public, anon, authenticated, service_role;
grant select on wow_prop_history.ncaaf_pregame_schedule_research_v1
to service_role;
comment on view wow_prop_history.ncaaf_pregame_schedule_research_v1 is
'Actual CFBD game schedule captures observed before the captured kickoff. Source-hash anchored; earliest per official event id. Settled kickoff changes preserved, never overwritten. Schedule-only, not player stat, lineup, historical sportsbook market, fitted probability or authorized training feature.';
