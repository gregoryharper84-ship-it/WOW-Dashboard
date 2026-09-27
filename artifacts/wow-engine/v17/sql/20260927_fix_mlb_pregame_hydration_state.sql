-- V17 MLB authoritative pregame hydration-state repair — 2026-09-27
--
-- Class B orchestration/hydration repair for #946/#947. Scheduled start time is
-- not authoritative proof that a delayed MLB game has begun. For any event at
-- or after its nominal start, fresh official MLB live-feed status plus zero
-- gameplay is required before hydration/lineup/governance may continue.
--
-- No sporting probability math, fitted artifact, coefficient, calibration,
-- lower bound, qualification threshold, specialist ownership, terminal
-- authority, publication authority, or execution permission changes.

create or replace function public.wow_mlb_current_pregame_status(
  p_official_event_id text
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  v_resp extensions.http_response;
  v_body jsonb;
  v_fetched_at timestamptz;
  v_abstract_state text;
  v_detailed_state text;
  v_pitch_n integer := 0;
  v_completed_play_n integer := 0;
begin
  if nullif(btrim(coalesce(p_official_event_id,'')),'') is null then
    return jsonb_build_object(
      'status','BLOCKED','code','OFFICIAL_EVENT_ID_MISSING',
      'pregame',false,'can_execute',false
    );
  end if;

  begin
    v_resp := extensions.http_get(
      format('https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',p_official_event_id)::varchar
    );
  exception when others then
    return jsonb_build_object(
      'status','BLOCKED','code','OFFICIAL_MLB_LIVE_FEED_UNAVAILABLE',
      'error_type',sqlstate,'pregame',false,'can_execute',false
    );
  end;

  if v_resp.status <> 200 then
    return jsonb_build_object(
      'status','BLOCKED','code','OFFICIAL_MLB_LIVE_FEED_UNAVAILABLE',
      'http_status',v_resp.status,'pregame',false,'can_execute',false
    );
  end if;

  v_body := v_resp.content::jsonb;
  v_fetched_at := clock_timestamp();
  v_abstract_state := coalesce(v_body#>>'{gameData,status,abstractGameState}','');
  v_detailed_state := coalesce(v_body#>>'{gameData,status,detailedState}','');

  select count(*) into v_pitch_n
  from jsonb_array_elements(coalesce(v_body#>'{liveData,plays,allPlays}','[]'::jsonb)) p
  cross join lateral jsonb_array_elements(coalesce(p#>'{playEvents}','[]'::jsonb)) pe
  where coalesce(nullif(pe->>'isPitch','')::boolean,false);

  select count(*) into v_completed_play_n
  from jsonb_array_elements(coalesce(v_body#>'{liveData,plays,allPlays}','[]'::jsonb)) p
  where coalesce(nullif(p#>>'{about,isComplete}','')::boolean,false)
    and coalesce(p#>>'{result,event}','') <> 'Game Advisory';

  if v_pitch_n > 0
     or v_completed_play_n > 0
     or v_abstract_state in ('Live','Final')
     or v_detailed_state in (
       'In Progress','Final','Game Over','Postponed','Cancelled','Canceled','Suspended'
     ) then
    return jsonb_build_object(
      'status','HOLD','code','EVENT_NOT_PREGAME','pregame',false,
      'official_abstract_state',v_abstract_state,
      'official_detailed_state',v_detailed_state,
      'pitch_events',v_pitch_n,'completed_plays',v_completed_play_n,
      'fetched_at',v_fetched_at,'can_execute',false
    );
  end if;

  if v_detailed_state not in ('Scheduled','Pre-Game','Delayed Start','Warmup') then
    return jsonb_build_object(
      'status','HOLD','code','EVENT_PREGAME_STATUS_UNPROVEN','pregame',false,
      'official_abstract_state',v_abstract_state,
      'official_detailed_state',v_detailed_state,
      'fetched_at',v_fetched_at,'can_execute',false
    );
  end if;

  return jsonb_build_object(
    'status','PASS','code','MLB_OFFICIAL_PREGAME_STATUS_PASS','pregame',true,
    'official_abstract_state',v_abstract_state,
    'official_detailed_state',v_detailed_state,
    'pitch_events',v_pitch_n,'completed_plays',v_completed_play_n,
    'fetched_at',v_fetched_at,'can_execute',false
  );
end;
$function$;

revoke all on function public.wow_mlb_current_pregame_status(text) from public, anon, authenticated;
grant execute on function public.wow_mlb_current_pregame_status(text) to service_role;

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
  v_current_status jsonb;
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
  where s.captured_at >= clock_timestamp() - interval '24 hours'
    and exists (
      select 1
      from public.wow_mlb_forward_shadow_events se
      where se.snapshot_id = s.snapshot_id
        and (
          lower(btrim(coalesce(se.event_status,''))) in (
            'scheduled','pre-game','pregame','delayed start','warmup'
          )
          or (
            btrim(coalesce(se.event_status,'')) = ''
            and se.event_start_time > clock_timestamp()
          )
        )
    )
  order by s.captured_at desc
  limit 1;

  if v_snapshot_id is null then
    return jsonb_build_object('status','NO_PREGAME_SNAPSHOT','probability_publishable',false,'can_execute',false);
  end if;

  if extract(year from v_slate_date)::integer <> 2026 then
    return jsonb_build_object(
      'status','BLOCKED','reason','UNSUPPORTED_FROZEN_FEATURE_SEASON',
      'slate_date',v_slate_date,'supported_season',2026,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  if not exists (
    select 1 from public.wow_mlb_forward_aux_snapshots
    where shadow_snapshot_id=v_snapshot_id
      and source_kind='MLB_SCHEDULE_SEASON_TO_DATE'
      and subject_id=v_schedule_subject
  ) then
    v_schedule_url := format(
      'https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=03/25/2026&endDate=%s&hydrate=team,venue,linescore',
      to_char(v_slate_date - 1, 'MM/DD/YYYY')
    );
    v_schedule_aux_id := public.wow_mlb_forward_cache_url(
      v_snapshot_id,'MLB_SCHEDULE_SEASON_TO_DATE',v_schedule_subject,v_schedule_url
    );
  end if;

  if not exists (
    select 1 from public.wow_mlb_forward_schedule_games where shadow_snapshot_id=v_snapshot_id
  ) then
    v_schedule_materialization := public.wow_mlb_forward_materialize_schedule(v_snapshot_id);
  end if;

  select count(distinct team_id) into v_expected_teams
  from (
    select home_team_id as team_id from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id and home_team_id is not null
    union
    select away_team_id as team_id from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id and away_team_id is not null
  ) t;

  select count(*) into v_workload_pass
  from public.wow_mlb_forward_bullpen_workload
  where shadow_snapshot_id=v_snapshot_id and hydration_status='PASS';

  if v_workload_pass < v_expected_teams then
    v_workload := public.wow_mlb_capture_recent_bullpen_workload(
      v_snapshot_id,v_slate_date - 3,v_slate_date - 1
    );
  end if;

  update public.wow_mlb_forward_shadow_events
  set feature_hydration_status='DELAYED_STARTER_UNRESOLVED'
  where snapshot_id=v_snapshot_id
    and (
      lower(btrim(coalesce(event_status,''))) in ('scheduled','pre-game','pregame','delayed start','warmup')
      or (btrim(coalesce(event_status,'')) = '' and event_start_time > clock_timestamp())
    )
    and coalesce(feature_hydration_status,'NOT_STARTED') <> 'PASS'
    and (home_probable_pitcher_id is null or away_probable_pitcher_id is null);
  get diagnostics v_delayed = row_count;

  for e in
    select shadow_event_id,official_event_id,event_start_time,event_status,
           feature_hydration_status,model_score_status
    from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id
      and (
        lower(btrim(coalesce(event_status,''))) in ('scheduled','pre-game','pregame','delayed start','warmup')
        or (btrim(coalesce(event_status,'')) = '' and event_start_time > clock_timestamp())
      )
      and home_probable_pitcher_id is not null
      and away_probable_pitcher_id is not null
      and (
        coalesce(feature_hydration_status,'NOT_STARTED') <> 'PASS'
        or coalesce(model_score_status,'NOT_SCORED') not like 'SHADOW_SCORED%'
      )
    order by event_start_time,official_event_id
  loop
    v_considered := v_considered + 1;
    begin
      if e.event_start_time <= clock_timestamp() then
        v_current_status := public.wow_mlb_current_pregame_status(e.official_event_id);
        if coalesce(v_current_status->>'status','') <> 'PASS'
           or coalesce((v_current_status->>'pregame')::boolean,false) is not true then
          v_blocked := v_blocked + 1;
          v_results := v_results || jsonb_build_array(jsonb_build_object(
            'official_event_id',e.official_event_id,
            'status','CURRENT_STATUS_BLOCKED',
            'reason',coalesce(v_current_status->>'code','MLB_CURRENT_PREGAME_STATUS_UNAVAILABLE'),
            'official_detailed_state',v_current_status->>'official_detailed_state'
          ));
          continue;
        end if;
      end if;

      if coalesce(e.feature_hydration_status,'NOT_STARTED') <> 'PASS' then
        v_cache := public.wow_mlb_forward_cache_event_inputs(e.shadow_event_id);
        if coalesce(v_cache->>'status','') <> 'CACHED' then
          v_blocked := v_blocked + 1;
          v_results := v_results || jsonb_build_array(jsonb_build_object(
            'official_event_id',e.official_event_id,'status','CACHE_BLOCKED','reason',v_cache->>'reason'
          ));
          continue;
        end if;

        v_home := public.wow_mlb_forward_build_side_features(e.shadow_event_id,'HOME');
        v_away := public.wow_mlb_forward_build_side_features(e.shadow_event_id,'AWAY');
        if coalesce(v_home->>'status','') <> 'PASS'
           or coalesce(v_away->>'status','') <> 'PASS' then
          v_blocked := v_blocked + 1;
          v_results := v_results || jsonb_build_array(jsonb_build_object(
            'official_event_id',e.official_event_id,'status','FEATURE_BLOCKED',
            'home_status',v_home->>'status','home_reason',v_home->>'reason',
            'away_status',v_away->>'status','away_reason',v_away->>'reason'
          ));
          continue;
        end if;
        v_hydrated := v_hydrated + 1;
      end if;

      v_score := public.wow_mlb_forward_score_event(e.shadow_event_id);
      if coalesce(v_score->>'status','') like 'SHADOW_SCORED%' then
        v_scored := v_scored + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'official_event_id',e.official_event_id,'status',v_score->>'status',
          'probability_publishable',false,'can_execute',false
        ));
      else
        v_blocked := v_blocked + 1;
        v_results := v_results || jsonb_build_array(jsonb_build_object(
          'official_event_id',e.official_event_id,'status','SCORE_BLOCKED','reason',v_score->>'reason'
        ));
      end if;
    exception when others then
      v_errors := v_errors + 1;
      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'official_event_id',e.official_event_id,'status','ERROR','error_type',sqlstate
      ));
    end;
  end loop;

  return jsonb_build_object(
    'status','COMPLETE','shadow_snapshot_id',v_snapshot_id,'slate_date',v_slate_date,
    'schedule_context_ready',exists(
      select 1 from public.wow_mlb_forward_schedule_games where shadow_snapshot_id=v_snapshot_id
    ),
    'expected_teams',v_expected_teams,
    'workload_pass_teams',(
      select count(*) from public.wow_mlb_forward_bullpen_workload
      where shadow_snapshot_id=v_snapshot_id and hydration_status='PASS'
    ),
    'considered',v_considered,'hydrated',v_hydrated,'scored',v_scored,
    'delayed_starter_unresolved',v_delayed,'blocked',v_blocked,'errors',v_errors,
    'results',v_results,'probability_publishable',false,'can_execute',false
  );
end;
$function$;

-- Delayed-start lineup confirmation: remove scheduled-clock proxy and prove
-- current pregame state from the official live feed immediately before writing.
create or replace function public.wow_mlb_forward_confirm_lineup(p_shadow_event_id uuid)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  e public.wow_mlb_forward_shadow_events%rowtype;
  r extensions.http_response;
  v_url text;
  v_body jsonb;
  v_capture_at timestamptz;
  v_home_team_id integer;
  v_away_team_id integer;
  v_home_order integer[] := '{}';
  v_away_order integer[] := '{}';
  v_abstract_state text;
  v_detailed_state text;
  v_pitch_n integer := 0;
  v_completed_play_n integer := 0;
  v_identity jsonb;
  v_identity_sha text;
  v_raw_sha text;
  v_lineup_snapshot_id uuid;
  v_existing public.wow_mlb_forward_lineup_snapshots%rowtype;
  v_was_confirmed boolean := false;
  v_score jsonb;
  v_score_status text;
  v_score_snapshot_id text;
begin
  select * into e
  from public.wow_mlb_forward_shadow_events
  where shadow_event_id=p_shadow_event_id
  for update;
  if not found then raise exception 'shadow event not found'; end if;

  v_url := format('https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',e.official_event_id);
  begin
    r := extensions.http_get(v_url::varchar);
  exception when others then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_LINEUP_SOURCE_UNAVAILABLE',
      'error_type',sqlstate,'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end;
  if r.status <> 200 then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_LINEUP_SOURCE_HTTP_ERROR',
      'http_status',r.status,'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_body := r.content::jsonb;
  v_capture_at := clock_timestamp();
  v_abstract_state := coalesce(v_body#>>'{gameData,status,abstractGameState}','');
  v_detailed_state := coalesce(v_body#>>'{gameData,status,detailedState}','');

  select count(*) into v_pitch_n
  from jsonb_array_elements(coalesce(v_body#>'{liveData,plays,allPlays}','[]'::jsonb)) p
  cross join lateral jsonb_array_elements(coalesce(p#>'{playEvents}','[]'::jsonb)) pe
  where coalesce(nullif(pe->>'isPitch','')::boolean,false);

  select count(*) into v_completed_play_n
  from jsonb_array_elements(coalesce(v_body#>'{liveData,plays,allPlays}','[]'::jsonb)) p
  where coalesce(nullif(p#>>'{about,isComplete}','')::boolean,false)
    and coalesce(p#>>'{result,event}','') <> 'Game Advisory';

  if v_pitch_n > 0
     or v_completed_play_n > 0
     or v_abstract_state in ('Live','Final')
     or v_detailed_state in ('In Progress','Game Over','Final','Postponed','Cancelled','Canceled','Suspended') then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_GAMEPLAY_ALREADY_STARTED',
      'official_abstract_state',v_abstract_state,'official_detailed_state',v_detailed_state,
      'pitch_events',v_pitch_n,'completed_plays',v_completed_play_n,
      'shadow_event_id',p_shadow_event_id,'probability_publishable',false,'can_execute',false
    );
  end if;

  if v_detailed_state not in ('Scheduled','Pre-Game','Delayed Start','Warmup') then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_PREGAME_STATUS_UNPROVEN',
      'official_abstract_state',v_abstract_state,'official_detailed_state',v_detailed_state,
      'shadow_event_id',p_shadow_event_id,'probability_publishable',false,'can_execute',false
    );
  end if;

  v_home_team_id := nullif(v_body#>>'{gameData,teams,home,id}','')::integer;
  v_away_team_id := nullif(v_body#>>'{gameData,teams,away,id}','')::integer;
  if v_home_team_id is distinct from e.home_team_id
     or v_away_team_id is distinct from e.away_team_id then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_LINEUP_TEAM_ID_MISMATCH',
      'shadow_event_id',p_shadow_event_id,'probability_publishable',false,'can_execute',false
    );
  end if;

  select coalesce(array_agg(value::integer order by ord),'{}'::integer[]) into v_home_order
  from jsonb_array_elements_text(coalesce(v_body#>'{liveData,boxscore,teams,home,battingOrder}','[]'::jsonb))
       with ordinality as x(value,ord);
  select coalesce(array_agg(value::integer order by ord),'{}'::integer[]) into v_away_order
  from jsonb_array_elements_text(coalesce(v_body#>'{liveData,boxscore,teams,away,battingOrder}','[]'::jsonb))
       with ordinality as x(value,ord);

  if cardinality(v_home_order) <> 9
     or cardinality(v_away_order) <> 9
     or (select count(distinct player_id) from unnest(v_home_order) player_id) <> 9
     or (select count(distinct player_id) from unnest(v_away_order) player_id) <> 9 then
    return jsonb_build_object(
      'status','DELAYED','reason','OFFICIAL_LINEUP_NOT_AVAILABLE',
      'home_batting_order_n',cardinality(v_home_order),'away_batting_order_n',cardinality(v_away_order),
      'shadow_event_id',p_shadow_event_id,'probability_publishable',false,'can_execute',false
    );
  end if;

  v_identity := jsonb_build_object(
    'official_event_id',e.official_event_id,'home_team_id',v_home_team_id,'away_team_id',v_away_team_id,
    'home_batting_order',to_jsonb(v_home_order),'away_batting_order',to_jsonb(v_away_order)
  );
  v_identity_sha := encode(extensions.digest(convert_to(v_identity::text,'UTF8'),'sha256'),'hex');
  v_raw_sha := encode(extensions.digest(convert_to(r.content,'UTF8'),'sha256'),'hex');
  v_was_confirmed := e.lineup_status='CONFIRMED';

  select * into v_existing
  from public.wow_mlb_forward_lineup_snapshots
  where shadow_event_id=p_shadow_event_id and lineup_identity_sha256=v_identity_sha
  order by captured_at desc limit 1;

  if found then
    update public.wow_mlb_forward_shadow_events
    set lineup_status='CONFIRMED',lineup_snapshot_id=v_existing.lineup_snapshot_id,
        lineup_confirmed_at=v_existing.captured_at
    where shadow_event_id=p_shadow_event_id;
    if not v_was_confirmed and e.feature_hydration_status='PASS' then
      v_score := public.wow_mlb_forward_score_event(p_shadow_event_id);
      v_score_status := v_score->>'status';
      v_score_snapshot_id := v_score->>'score_snapshot_id';
    end if;
    return jsonb_build_object(
      'status',case when v_was_confirmed then 'UNCHANGED_CONFIRMED_LINEUP' else 'CONFIRMED_FROM_EXISTING_SNAPSHOT' end,
      'shadow_event_id',p_shadow_event_id,'lineup_snapshot_id',v_existing.lineup_snapshot_id,
      'lineup_identity_sha256',v_identity_sha,'score_status',v_score_status,
      'score_snapshot_id',v_score_snapshot_id,'probability_publishable',false,'can_execute',false
    );
  end if;

  insert into public.wow_mlb_forward_lineup_snapshots(
    shadow_event_id,official_event_id,captured_at,home_team_id,away_team_id,
    home_batting_order,away_batting_order,lineup_identity_sha256,
    official_abstract_state,official_detailed_state,
    official_pitch_events_at_capture,official_completed_plays_at_capture,strict_pregame_provenance,
    source_url,http_status,raw_body,raw_sha256,lineup_status,research_only,probability_publishable,can_execute
  ) values (
    p_shadow_event_id,e.official_event_id,v_capture_at,v_home_team_id,v_away_team_id,
    v_home_order,v_away_order,v_identity_sha,v_abstract_state,v_detailed_state,
    v_pitch_n,v_completed_play_n,true,v_url,r.status,r.content,v_raw_sha,'CONFIRMED',true,false,false
  ) returning lineup_snapshot_id into v_lineup_snapshot_id;

  update public.wow_mlb_forward_shadow_events
  set lineup_status='CONFIRMED',lineup_snapshot_id=v_lineup_snapshot_id,lineup_confirmed_at=v_capture_at
  where shadow_event_id=p_shadow_event_id;

  if e.feature_hydration_status='PASS' then
    v_score := public.wow_mlb_forward_score_event(p_shadow_event_id);
    v_score_status := v_score->>'status';
    v_score_snapshot_id := v_score->>'score_snapshot_id';
  end if;

  return jsonb_build_object(
    'status',case when v_was_confirmed then 'CONFIRMED_LINEUP_CHANGED' else 'CONFIRMED' end,
    'shadow_event_id',p_shadow_event_id,'lineup_snapshot_id',v_lineup_snapshot_id,
    'lineup_identity_sha256',v_identity_sha,'home_batting_order_n',cardinality(v_home_order),
    'away_batting_order_n',cardinality(v_away_order),'official_abstract_state',v_abstract_state,
    'official_detailed_state',v_detailed_state,'pitch_events',v_pitch_n,
    'completed_plays',v_completed_play_n,'score_status',v_score_status,
    'score_snapshot_id',v_score_snapshot_id,'probability_publishable',false,'can_execute',false
  );
end;
$function$;

create or replace function public.wow_mlb_forward_auto_confirm_lineups()
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  v_snapshot_id uuid;
  e record;
  v_result jsonb;
  v_checked integer := 0;
  v_confirmed integer := 0;
  v_unchanged integer := 0;
  v_delayed integer := 0;
  v_blocked integer := 0;
  v_errors integer := 0;
  v_results jsonb := '[]'::jsonb;
begin
  select s.snapshot_id into v_snapshot_id
  from public.wow_mlb_forward_shadow_source_snapshots s
  where s.captured_at >= clock_timestamp() - interval '24 hours'
    and exists (
      select 1 from public.wow_mlb_forward_shadow_events se
      where se.snapshot_id=s.snapshot_id
        and lower(btrim(coalesce(se.event_status,''))) in ('scheduled','pre-game','pregame','delayed start','warmup')
    )
  order by s.captured_at desc limit 1;

  if v_snapshot_id is null then
    return jsonb_build_object('status','NO_PREGAME_SNAPSHOT','probability_publishable',false,'can_execute',false);
  end if;

  for e in
    select shadow_event_id,official_event_id,event_start_time,lineup_status
    from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id
      and lower(btrim(coalesce(event_status,''))) in ('scheduled','pre-game','pregame','delayed start','warmup')
      and feature_hydration_status='PASS'
    order by event_start_time,official_event_id
  loop
    v_checked := v_checked + 1;
    begin
      v_result := public.wow_mlb_forward_confirm_lineup(e.shadow_event_id);
      if coalesce(v_result->>'status','') in ('CONFIRMED','CONFIRMED_LINEUP_CHANGED','CONFIRMED_FROM_EXISTING_SNAPSHOT') then
        v_confirmed := v_confirmed + 1;
      elsif coalesce(v_result->>'status','')='UNCHANGED_CONFIRMED_LINEUP' then
        v_unchanged := v_unchanged + 1;
      elsif coalesce(v_result->>'status','')='DELAYED' then
        v_delayed := v_delayed + 1;
      else
        v_blocked := v_blocked + 1;
      end if;
      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'official_event_id',e.official_event_id,'status',v_result->>'status',
        'reason',v_result->>'reason','score_status',v_result->>'score_status',
        'probability_publishable',false,'can_execute',false
      ));
    exception when others then
      v_errors := v_errors + 1;
      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'official_event_id',e.official_event_id,'status','ERROR','error_type',sqlstate,
        'probability_publishable',false,'can_execute',false
      ));
    end;
  end loop;

  return jsonb_build_object(
    'status','COMPLETE','shadow_snapshot_id',v_snapshot_id,'checked',v_checked,
    'confirmed',v_confirmed,'unchanged',v_unchanged,'delayed',v_delayed,
    'blocked',v_blocked,'errors',v_errors,'results',v_results,
    'probability_publishable',false,'can_execute',false
  );
end;
$function$;

-- Preserve every later production hardening change in the 4-argument governance
-- hydrator. Remove only the obsolete nominal-clock blocker and expand its
-- already-existing official-live-state fail-closed set to include Suspended.
do $patch$
declare
  v_oid oid;
  v_ddl text;
  v_old_clock text := $$if clock_timestamp()>=r.event_start_time then return jsonb_build_object('status','HOLD','blockers',jsonb_build_array('EVENT_NOT_PREGAME'),'can_execute',false); end if;$$;
  v_old_states text := $$live_state in ('In Progress','Final','Game Over','Postponed','Cancelled','Canceled')$$;
  v_new_states text := $$live_state in ('In Progress','Final','Game Over','Postponed','Cancelled','Canceled','Suspended')$$;
begin
  v_oid := to_regprocedure('public.wow_v17_hydrate_mlb_event_governance_evidence(uuid,uuid,jsonb,text)');
  if v_oid is null then
    raise exception '4-argument MLB governance hydrator is missing';
  end if;
  select pg_get_functiondef(v_oid) into v_ddl;
  if position(v_old_clock in v_ddl) > 0 then
    v_ddl := replace(v_ddl,v_old_clock,'');
  end if;
  if position(v_old_states in v_ddl) > 0 then
    v_ddl := replace(v_ddl,v_old_states,v_new_states);
  elsif position(v_new_states in v_ddl) = 0 then
    raise exception 'MLB governance official-state guard has unexpected shape';
  end if;
  execute v_ddl;
end
$patch$;

-- The cron schedules remain unchanged; the underlying functions are now
-- delayed-start aware and still fail closed from fresh official evidence.
