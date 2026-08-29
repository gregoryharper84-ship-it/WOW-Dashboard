-- WOW MLB forward-shadow automatic pregame hydration/scoring loop — 2026-08-28
--
-- Purpose: close the orchestration gap between a newly captured forward-shadow
-- slate snapshot and the existing research-only frozen-model scorer. The job
-- operates only on the freshest snapshot that still has pregame events.
-- Missing probable starters, sources, feature components, or scorer evidence
-- remain delayed/blocked. This migration never authorizes probability
-- publication or execution and does not alter the separate production-readiness
-- ratification latch.
--
-- The currently frozen forward feature builder is explicitly 2026-specific
-- (its schedule-source subject is "2026"). This orchestrator therefore blocks
-- any other season instead of silently generalizing the feature contract.

create or replace function public.wow_mlb_forward_auto_hydrate_pregame()
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  v_snapshot_id uuid;
  v_slate_date date;
  v_schedule_subject text := '2026';
  v_schedule_url text;
  v_schedule_aux_id uuid;
  v_schedule_materialization jsonb;
  v_expected_teams integer := 0;
  v_workload_pass integer := 0;
  v_workload jsonb;
  e record;
  v_cache jsonb;
  v_home jsonb;
  v_away jsonb;
  v_score jsonb;
  v_considered integer := 0;
  v_hydrated integer := 0;
  v_scored integer := 0;
  v_delayed integer := 0;
  v_blocked integer := 0;
  v_errors integer := 0;
  v_results jsonb := '[]'::jsonb;
begin
  select s.snapshot_id, s.slate_date
  into v_snapshot_id, v_slate_date
  from public.wow_mlb_forward_shadow_source_snapshots s
  where exists (
    select 1
    from public.wow_mlb_forward_shadow_events se
    where se.snapshot_id = s.snapshot_id
      and se.event_start_time > clock_timestamp()
  )
  order by s.captured_at desc
  limit 1;

  if v_snapshot_id is null then
    return jsonb_build_object(
      'status','NO_PREGAME_SNAPSHOT',
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  if extract(year from v_slate_date)::integer <> 2026 then
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','UNSUPPORTED_FROZEN_FEATURE_SEASON',
      'slate_date',v_slate_date,
      'supported_season',2026,
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  -- The 38-feature builder needs a timestamped regular-season schedule
  -- context for prior team/park history. Freeze it under this shadow snapshot
  -- before any event features are built. materialize_schedule admits Final
  -- games only and schedule_context further restricts to dates before the
  -- target event date.
  if not exists (
    select 1
    from public.wow_mlb_forward_aux_snapshots
    where shadow_snapshot_id=v_snapshot_id
      and source_kind='MLB_SCHEDULE_SEASON_TO_DATE'
      and subject_id=v_schedule_subject
  ) then
    v_schedule_url := format(
      'https://statsapi.mlb.com/api/v1/schedule?sportId=1&season=%s&gameType=R&hydrate=team,venue,linescore',
      v_schedule_subject
    );
    v_schedule_aux_id := public.wow_mlb_forward_cache_url(
      v_snapshot_id,
      'MLB_SCHEDULE_SEASON_TO_DATE',
      v_schedule_subject,
      v_schedule_url
    );
  end if;

  if not exists (
    select 1
    from public.wow_mlb_forward_schedule_games
    where shadow_snapshot_id=v_snapshot_id
  ) then
    v_schedule_materialization := public.wow_mlb_forward_materialize_schedule(v_snapshot_id);
  end if;

  select count(distinct team_id)
  into v_expected_teams
  from (
    select home_team_id as team_id
    from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id and home_team_id is not null
    union
    select away_team_id as team_id
    from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id and away_team_id is not null
  ) t;

  select count(*)
  into v_workload_pass
  from public.wow_mlb_forward_bullpen_workload
  where shadow_snapshot_id=v_snapshot_id
    and hydration_status='PASS';

  if v_workload_pass < v_expected_teams then
    v_workload := public.wow_mlb_capture_recent_bullpen_workload(
      v_snapshot_id,
      v_slate_date - 3,
      v_slate_date - 1
    );
  end if;

  update public.wow_mlb_forward_shadow_events
  set feature_hydration_status='DELAYED_STARTER_UNRESOLVED'
  where snapshot_id=v_snapshot_id
    and event_start_time > clock_timestamp()
    and coalesce(feature_hydration_status,'NOT_STARTED') <> 'PASS'
    and (home_probable_pitcher_id is null or away_probable_pitcher_id is null);
  get diagnostics v_delayed = row_count;

  for e in
    select shadow_event_id, official_event_id, event_start_time,
           feature_hydration_status, model_score_status
    from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id
      and event_start_time > clock_timestamp()
      and home_probable_pitcher_id is not null
      and away_probable_pitcher_id is not null
      and (
        coalesce(feature_hydration_status,'NOT_STARTED') <> 'PASS'
        or coalesce(model_score_status,'NOT_SCORED') not like 'SHADOW_SCORED%'
      )
    order by event_start_time, official_event_id
  loop
    v_considered := v_considered + 1;
    begin
      if coalesce(e.feature_hydration_status,'NOT_STARTED') <> 'PASS' then
        v_cache := public.wow_mlb_forward_cache_event_inputs(e.shadow_event_id);
        if coalesce(v_cache->>'status','') <> 'CACHED' then
          v_blocked := v_blocked + 1;
          v_results := v_results || jsonb_build_array(jsonb_build_object(
            'official_event_id',e.official_event_id,
            'status','CACHE_BLOCKED',
            'reason',v_cache->>'reason'
          ));
          continue;
        end if;

        v_home := public.wow_mlb_forward_build_side_features(e.shadow_event_id,'HOME');
        v_away := public.wow_mlb_forward_build_side_features(e.shadow_event_id,'AWAY');
        if coalesce(v_home->>'status','') <> 'PASS'
           or coalesce(v_away->>'status','') <> 'PASS' then
          v_blocked := v_blocked + 1;
          v_results := v_results || jsonb_build_array(jsonb_build_object(
            'official_event_id',e.official_event_id,
            'status','FEATURE_BLOCKED',
            'home_status',v_home->>'status',
            'home_reason',v_home->>'reason',
            'away_status',v_away->>'status',
            'away_reason',v_away->>'reason'
          ));
          continue;
        end if;
        v_hydrated := v_hydrated + 1;
      end if;

      v_score := public.wow_mlb_forward_score_event(e.shadow_event_id);
      if coalesce(v_score->>'status','') like 'SHADOW_SCORED%' then
        v_scored := v_scored + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'official_event_id',e.official_event_id,
          'status',v_score->>'status',
          'probability_publishable',false,
          'can_execute',false
        ));
      else
        v_blocked := v_blocked + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'official_event_id',e.official_event_id,
          'status','SCORE_BLOCKED',
          'reason',v_score->>'reason'
        ));
      end if;
    exception when others then
      v_errors := v_errors + 1;
      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'official_event_id',e.official_event_id,
        'status','ERROR',
        'error_type',sqlstate
      ));
    end;
  end loop;

  return jsonb_build_object(
    'status','COMPLETE',
    'shadow_snapshot_id',v_snapshot_id,
    'slate_date',v_slate_date,
    'schedule_context_ready',exists(
      select 1
      from public.wow_mlb_forward_schedule_games
      where shadow_snapshot_id=v_snapshot_id
    ),
    'expected_teams',v_expected_teams,
    'workload_pass_teams',(
      select count(*)
      from public.wow_mlb_forward_bullpen_workload
      where shadow_snapshot_id=v_snapshot_id and hydration_status='PASS'
    ),
    'considered',v_considered,
    'hydrated',v_hydrated,
    'scored',v_scored,
    'delayed_starter_unresolved',v_delayed,
    'blocked',v_blocked,
    'errors',v_errors,
    'results',v_results,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;

select cron.schedule(
  'wow-mlb-forward-shadow-auto-hydrate',
  '5,20,35,50 * * * *',
  $$select public.wow_mlb_forward_auto_hydrate_pregame();$$
);
