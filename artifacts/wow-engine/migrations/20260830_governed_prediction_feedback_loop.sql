-- WOW v16 Clean Core — governed primary prediction feedback loop
-- Research/validation only. This migration cannot execute wagers or market orders.
--
-- Design:
--   * official MLB Stats API evidence only;
--   * exact event/player identity before persistence;
--   * one immutable outcome per prediction;
--   * push/void preserved for prop settlement;
--   * unsupported lanes fail closed and do not write;
--   * dispatcher delegates to lane-specific graders rather than inventing outcomes.

create or replace function public.wow_mlb_resolve_game_identity(
  p_event_id text,
  p_event_start_time timestamptz default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_match text[];
  v_date date;
  v_away text;
  v_home text;
  v_schedule_url text;
  v_response extensions.http_response;
  v_body jsonb;
  v_game jsonb;
  v_game_pk text;
  v_match_count integer := 0;
begin
  if p_event_id is null or btrim(p_event_id) = '' then
    return jsonb_build_object('status','IDENTITY_UNRESOLVED','reason','EVENT_ID_MISSING');
  end if;

  if p_event_id ~ '^[0-9]+$' then
    return jsonb_build_object(
      'status','PASS',
      'game_pk',p_event_id,
      'identity_source','EXACT_OFFICIAL_GAME_PK'
    );
  end if;

  v_match := regexp_match(
    upper(btrim(p_event_id)),
    '^MLB-([0-9]{4}-[0-9]{2}-[0-9]{2})-([A-Z0-9]+)-([A-Z0-9]+)$'
  );
  if v_match is null then
    return jsonb_build_object('status','IDENTITY_UNRESOLVED','reason','UNSUPPORTED_EVENT_ID_FORMAT');
  end if;

  v_date := v_match[1]::date;
  v_away := v_match[2];
  v_home := v_match[3];
  v_schedule_url := format(
    'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=%s&hydrate=team',
    to_char(v_date,'YYYY-MM-DD')
  );

  begin
    v_response := extensions.http_get(v_schedule_url::varchar);
  exception when others then
    return jsonb_build_object('status','OFFICIAL_SOURCE_UNAVAILABLE','reason','SCHEDULE_REQUEST_FAILED');
  end;

  if v_response.status <> 200 then
    return jsonb_build_object(
      'status','OFFICIAL_SOURCE_UNAVAILABLE',
      'reason','SCHEDULE_HTTP_ERROR',
      'http_status',v_response.status
    );
  end if;

  begin
    v_body := v_response.content::jsonb;
  exception when others then
    return jsonb_build_object('status','OFFICIAL_SOURCE_INVALID','reason','SCHEDULE_JSON_INVALID');
  end;

  for v_game in
    select value
    from jsonb_array_elements(coalesce(v_body #> '{dates,0,games}','[]'::jsonb))
  loop
    if upper(coalesce(v_game #>> '{teams,away,team,abbreviation}','')) = v_away
       and upper(coalesce(v_game #>> '{teams,home,team,abbreviation}','')) = v_home then
      v_match_count := v_match_count + 1;
      v_game_pk := v_game->>'gamePk';
    end if;
  end loop;

  if v_match_count <> 1 or v_game_pk is null then
    return jsonb_build_object(
      'status','IDENTITY_UNRESOLVED',
      'reason','SCHEDULE_MATCH_NOT_UNIQUE',
      'match_count',v_match_count
    );
  end if;

  return jsonb_build_object(
    'status','PASS',
    'game_pk',v_game_pk,
    'identity_source','OFFICIAL_SCHEDULE_DATE_AWAY_HOME',
    'schedule_url',v_schedule_url,
    'event_start_time',p_event_start_time
  );
end;
$$;

create or replace function public.wow_mlb_team_identity_matches(
  p_expected text,
  p_official_team jsonb
)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select
    p_expected is not null
    and btrim(p_expected) <> ''
    and lower(btrim(p_expected)) in (
      lower(coalesce(p_official_team->>'name','')),
      lower(coalesce(p_official_team->>'abbreviation',''))
    );
$$;

create or replace function public.wow_grade_mlb_pitcher_strikeout_prediction(
  p_prediction_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  p public.wow_predictions%rowtype;
  v_identity jsonb;
  v_game_pk text;
  v_feed_url text;
  v_response extensions.http_response;
  v_body jsonb;
  v_state text;
  v_player_key text;
  v_player_count integer := 0;
  v_entry jsonb;
  v_away_player jsonb;
  v_home_player jsonb;
  v_player jsonb;
  v_strikeouts numeric;
  v_games_started integer;
  v_push boolean;
  v_hit boolean;
  v_result text;
begin
  select * into p
  from public.wow_predictions
  where prediction_id = p_prediction_id;

  if not found then
    return jsonb_build_object('status','PREDICTION_NOT_FOUND','prediction_id',p_prediction_id);
  end if;

  if exists (select 1 from public.wow_outcomes o where o.prediction_id = p_prediction_id) then
    return jsonb_build_object('status','ALREADY_SETTLED','prediction_id',p_prediction_id);
  end if;

  if upper(coalesce(p.sport,'')) <> 'MLB'
     or upper(coalesce(p.stat_type,'')) <> 'PITCHER_STRIKEOUTS'
     or p.model_family is null
     or p.model_artifact_version is null
     or p.player is null
     or p.line is null
     or upper(coalesce(p.direction,'')) not in ('MORE','LESS') then
    return jsonb_build_object('status','MODEL_OR_IDENTITY_UNSUPPORTED','prediction_id',p_prediction_id);
  end if;

  if p.event_start_time is null or now() < p.event_start_time + interval '2 hours' then
    return jsonb_build_object('status','WAIT_FINAL_WINDOW','prediction_id',p_prediction_id);
  end if;

  v_identity := public.wow_mlb_resolve_game_identity(p.event_id,p.event_start_time);
  if coalesce(v_identity->>'status','') <> 'PASS' then
    return jsonb_build_object(
      'status',coalesce(v_identity->>'status','IDENTITY_UNRESOLVED'),
      'prediction_id',p_prediction_id,
      'identity',v_identity
    );
  end if;

  v_game_pk := v_identity->>'game_pk';
  v_feed_url := format('https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',v_game_pk);
  begin
    v_response := extensions.http_get(v_feed_url::varchar);
  exception when others then
    return jsonb_build_object('status','OFFICIAL_SOURCE_UNAVAILABLE','prediction_id',p_prediction_id);
  end;
  if v_response.status <> 200 then
    return jsonb_build_object('status','OFFICIAL_SOURCE_UNAVAILABLE','prediction_id',p_prediction_id,'http_status',v_response.status);
  end if;

  begin
    v_body := v_response.content::jsonb;
  exception when others then
    return jsonb_build_object('status','OFFICIAL_SOURCE_INVALID','prediction_id',p_prediction_id);
  end;

  if (v_body #>> '{gamePk}') is distinct from v_game_pk then
    return jsonb_build_object('status','OFFICIAL_EVENT_ID_MISMATCH','prediction_id',p_prediction_id);
  end if;

  v_state := v_body #>> '{gameData,status,abstractGameState}';
  if v_state <> 'Final' then
    return jsonb_build_object('status','NOT_FINAL','prediction_id',p_prediction_id,'game_state',v_state);
  end if;

  for v_player_key, v_entry in
    select key, value
    from jsonb_each(coalesce(v_body #> '{gameData,players}','{}'::jsonb))
    where lower(value->>'fullName') = lower(p.player)
  loop
    v_player_count := v_player_count + 1;
  end loop;

  if v_player_count <> 1 or v_player_key is null then
    return jsonb_build_object(
      'status','PLAYER_IDENTITY_UNRESOLVED',
      'prediction_id',p_prediction_id,
      'match_count',v_player_count
    );
  end if;

  v_away_player := v_body #> array['liveData','boxscore','teams','away','players',v_player_key];
  v_home_player := v_body #> array['liveData','boxscore','teams','home','players',v_player_key];
  v_player := case
    when v_away_player is not null then v_away_player
    when v_home_player is not null then v_home_player
    else null
  end;

  if v_player is null then
    return jsonb_build_object('status','PLAYER_BOX_SCORE_MISSING','prediction_id',p_prediction_id);
  end if;

  begin
    v_games_started := nullif(v_player #>> '{stats,pitching,gamesStarted}','')::integer;
    v_strikeouts := nullif(v_player #>> '{stats,pitching,strikeOuts}','')::numeric;
  exception when others then
    return jsonb_build_object('status','OFFICIAL_STAT_INVALID','prediction_id',p_prediction_id);
  end;

  if coalesce(v_games_started,0) <> 1 then
    insert into public.wow_outcomes(
      prediction_id,official_result,actual_stat,hit,push,void,
      settlement_source,settlement_timestamp,failure_category
    ) values (
      p_prediction_id,'VOID_NOT_STARTER',null,null,false,true,
      v_feed_url,now(),'STARTER_SETTLEMENT_VOID'
    ) on conflict (prediction_id) do nothing;
    return jsonb_build_object('status','SETTLED_VOID_NOT_STARTER','prediction_id',p_prediction_id);
  end if;

  if v_strikeouts is null then
    return jsonb_build_object('status','OFFICIAL_STAT_MISSING','prediction_id',p_prediction_id);
  end if;

  v_push := (v_strikeouts = p.line);
  if v_push then
    v_hit := null;
    v_result := 'PUSH';
  elsif upper(p.direction) = 'MORE' then
    v_hit := v_strikeouts > p.line;
    v_result := case when v_hit then 'HIT' else 'MISS' end;
  else
    v_hit := v_strikeouts < p.line;
    v_result := case when v_hit then 'HIT' else 'MISS' end;
  end if;

  insert into public.wow_outcomes(
    prediction_id,official_result,actual_stat,hit,push,void,
    settlement_source,settlement_timestamp,failure_category
  ) values (
    p_prediction_id,v_result,v_strikeouts,v_hit,v_push,false,
    v_feed_url,now(),case when v_hit is false then coalesce(p.primary_failure_path,'MODEL_MISS') else null end
  ) on conflict (prediction_id) do nothing;

  return jsonb_build_object(
    'status','SETTLED',
    'prediction_id',p_prediction_id,
    'game_pk',v_game_pk,
    'actual_stat',v_strikeouts,
    'result',v_result,
    'can_execute',false
  );
end;
$$;

create or replace function public.wow_grade_mlb_event_prediction(
  p_event_prediction_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  p public.wow_event_predictions%rowtype;
  v_identity jsonb;
  v_game_pk text;
  v_feed_url text;
  v_response extensions.http_response;
  v_body jsonb;
  v_state text;
  v_home_team jsonb;
  v_away_team jsonb;
  v_home_score integer;
  v_away_score integer;
  v_official_winner text;
  v_failure_category text;
begin
  select * into p
  from public.wow_event_predictions
  where event_prediction_id = p_event_prediction_id;

  if not found then
    return jsonb_build_object('status','PREDICTION_NOT_FOUND','event_prediction_id',p_event_prediction_id);
  end if;

  if exists (select 1 from public.wow_event_outcomes o where o.event_prediction_id = p_event_prediction_id) then
    return jsonb_build_object('status','ALREADY_SETTLED','event_prediction_id',p_event_prediction_id);
  end if;

  if upper(coalesce(p.sport,'')) <> 'MLB'
     or upper(coalesce(p.market_family,'')) <> 'OUTRIGHT_WINNER'
     or upper(coalesce(p.settlement_basis,'')) <> 'FULL_GAME_INCLUDING_EXTRA_INNINGS'
     or p.model_version is null then
    return jsonb_build_object('status','MODEL_OR_IDENTITY_UNSUPPORTED','event_prediction_id',p_event_prediction_id);
  end if;

  if now() < p.event_start_time + interval '2 hours' then
    return jsonb_build_object('status','WAIT_FINAL_WINDOW','event_prediction_id',p_event_prediction_id);
  end if;

  v_identity := public.wow_mlb_resolve_game_identity(p.official_event_id,p.event_start_time);
  if coalesce(v_identity->>'status','') <> 'PASS' then
    return jsonb_build_object(
      'status',coalesce(v_identity->>'status','IDENTITY_UNRESOLVED'),
      'event_prediction_id',p_event_prediction_id,
      'identity',v_identity
    );
  end if;

  v_game_pk := v_identity->>'game_pk';
  v_feed_url := format('https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',v_game_pk);
  begin
    v_response := extensions.http_get(v_feed_url::varchar);
  exception when others then
    return jsonb_build_object('status','OFFICIAL_SOURCE_UNAVAILABLE','event_prediction_id',p_event_prediction_id);
  end;
  if v_response.status <> 200 then
    return jsonb_build_object('status','OFFICIAL_SOURCE_UNAVAILABLE','event_prediction_id',p_event_prediction_id,'http_status',v_response.status);
  end if;

  begin
    v_body := v_response.content::jsonb;
  exception when others then
    return jsonb_build_object('status','OFFICIAL_SOURCE_INVALID','event_prediction_id',p_event_prediction_id);
  end;

  if (v_body #>> '{gamePk}') is distinct from v_game_pk then
    return jsonb_build_object('status','OFFICIAL_EVENT_ID_MISMATCH','event_prediction_id',p_event_prediction_id);
  end if;

  v_state := v_body #>> '{gameData,status,abstractGameState}';
  if v_state <> 'Final' then
    return jsonb_build_object('status','NOT_FINAL','event_prediction_id',p_event_prediction_id,'game_state',v_state);
  end if;

  v_home_team := v_body #> '{gameData,teams,home}';
  v_away_team := v_body #> '{gameData,teams,away}';
  if not public.wow_mlb_team_identity_matches(p.home_team,v_home_team)
     or not public.wow_mlb_team_identity_matches(p.away_team,v_away_team) then
    return jsonb_build_object('status','OFFICIAL_TEAM_IDENTITY_MISMATCH','event_prediction_id',p_event_prediction_id);
  end if;

  begin
    v_home_score := nullif(v_body #>> '{liveData,linescore,teams,home,runs}','')::integer;
    v_away_score := nullif(v_body #>> '{liveData,linescore,teams,away,runs}','')::integer;
  exception when others then
    return jsonb_build_object('status','FINAL_SCORE_INVALID','event_prediction_id',p_event_prediction_id);
  end;

  if v_home_score is null or v_away_score is null or v_home_score = v_away_score then
    return jsonb_build_object('status','FINAL_SCORE_INVALID','event_prediction_id',p_event_prediction_id);
  end if;

  v_official_winner := case
    when v_home_score > v_away_score then v_home_team->>'name'
    else v_away_team->>'name'
  end;

  if p.selected_participant is null then
    v_failure_category := null;
  elsif lower(p.selected_participant) = lower(v_official_winner)
        or lower(p.selected_participant) = lower(case when v_home_score > v_away_score then coalesce(v_home_team->>'abbreviation','') else coalesce(v_away_team->>'abbreviation','') end) then
    v_failure_category := 'SELECTION_WON';
  else
    v_failure_category := 'SELECTION_LOST';
  end if;

  insert into public.wow_event_outcomes(
    event_prediction_id,official_winner,home_score,away_score,void,
    settlement_source,settlement_timestamp,failure_category
  ) values (
    p_event_prediction_id,v_official_winner,v_home_score,v_away_score,false,
    v_feed_url,now(),v_failure_category
  ) on conflict (event_prediction_id) do nothing;

  return jsonb_build_object(
    'status','SETTLED',
    'event_prediction_id',p_event_prediction_id,
    'game_pk',v_game_pk,
    'official_winner',v_official_winner,
    'home_score',v_home_score,
    'away_score',v_away_score,
    'can_execute',false
  );
end;
$$;

create or replace function public.wow_governed_auto_grade_predictions(
  p_limit integer default 200
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  r record;
  v_result jsonb;
  v_prop_checked integer := 0;
  v_prop_settled integer := 0;
  v_prop_held integer := 0;
  v_event_checked integer := 0;
  v_event_settled integer := 0;
  v_event_held integer := 0;
  v_prop_results jsonb := '[]'::jsonb;
  v_event_results jsonb := '[]'::jsonb;
begin
  p_limit := greatest(1,least(coalesce(p_limit,200),1000));

  for r in
    select p.prediction_id
    from public.wow_predictions p
    left join public.wow_outcomes o on o.prediction_id = p.prediction_id
    where o.prediction_id is null
      and upper(coalesce(p.sport,'')) = 'MLB'
      and upper(coalesce(p.stat_type,'')) = 'PITCHER_STRIKEOUTS'
      and p.model_family is not null
      and p.model_artifact_version is not null
      and p.player is not null
      and p.event_start_time is not null
      and now() >= p.event_start_time + interval '2 hours'
    order by p.event_start_time,p.prediction_id
    limit p_limit
  loop
    v_prop_checked := v_prop_checked + 1;
    begin
      v_result := public.wow_grade_mlb_pitcher_strikeout_prediction(r.prediction_id);
    exception when others then
      v_result := jsonb_build_object('status','ERROR','prediction_id',r.prediction_id,'error_type',sqlstate);
    end;
    if coalesce(v_result->>'status','') like 'SETTLED%' or coalesce(v_result->>'status','') = 'ALREADY_SETTLED' then
      v_prop_settled := v_prop_settled + 1;
    else
      v_prop_held := v_prop_held + 1;
    end if;
    v_prop_results := v_prop_results || jsonb_build_array(v_result);
  end loop;

  for r in
    select p.event_prediction_id
    from public.wow_event_predictions p
    left join public.wow_event_outcomes o on o.event_prediction_id = p.event_prediction_id
    where o.event_prediction_id is null
      and upper(coalesce(p.sport,'')) = 'MLB'
      and upper(coalesce(p.market_family,'')) = 'OUTRIGHT_WINNER'
      and upper(coalesce(p.settlement_basis,'')) = 'FULL_GAME_INCLUDING_EXTRA_INNINGS'
      and p.model_version is not null
      and p.event_start_time is not null
      and now() >= p.event_start_time + interval '2 hours'
    order by p.event_start_time,p.event_prediction_id
    limit p_limit
  loop
    v_event_checked := v_event_checked + 1;
    begin
      v_result := public.wow_grade_mlb_event_prediction(r.event_prediction_id);
    exception when others then
      v_result := jsonb_build_object('status','ERROR','event_prediction_id',r.event_prediction_id,'error_type',sqlstate);
    end;
    if coalesce(v_result->>'status','') = 'SETTLED' or coalesce(v_result->>'status','') = 'ALREADY_SETTLED' then
      v_event_settled := v_event_settled + 1;
    else
      v_event_held := v_event_held + 1;
    end if;
    v_event_results := v_event_results || jsonb_build_array(v_result);
  end loop;

  return jsonb_build_object(
    'status','COMPLETE',
    'prop',jsonb_build_object(
      'checked',v_prop_checked,'settled',v_prop_settled,'held',v_prop_held,'results',v_prop_results
    ),
    'event',jsonb_build_object(
      'checked',v_event_checked,'settled',v_event_settled,'held',v_event_held,'results',v_event_results
    ),
    'ncaaf',jsonb_build_object(
      'status','MODEL_UNAVAILABLE',
      'blockers',jsonb_build_array('CERTIFIED_OFFICIAL_OUTCOME_ADAPTER_NOT_WIRED')
    ),
    'live',jsonb_build_object(
      'status','MODEL_UNAVAILABLE',
      'blockers',jsonb_build_array('CERTIFIED_OFFICIAL_OUTCOME_ADAPTER_NOT_WIRED')
    ),
    'probability_publishable',false,
    'can_execute',false
  );
end;
$$;

-- Outcome ledgers are append-only after the first successful official settlement.
create or replace function public.wow_reject_outcome_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'WOW_OUTCOME_IMMUTABLE:%',tg_table_name;
end;
$$;

drop trigger if exists trg_wow_outcomes_immutable on public.wow_outcomes;
create trigger trg_wow_outcomes_immutable
before update or delete on public.wow_outcomes
for each row execute function public.wow_reject_outcome_mutation();

drop trigger if exists trg_wow_event_outcomes_immutable on public.wow_event_outcomes;
create trigger trg_wow_event_outcomes_immutable
before update or delete on public.wow_event_outcomes
for each row execute function public.wow_reject_outcome_mutation();

drop trigger if exists trg_wow_ncaaf_outcomes_immutable on public.wow_ncaaf_outcomes;
create trigger trg_wow_ncaaf_outcomes_immutable
before update or delete on public.wow_ncaaf_outcomes
for each row execute function public.wow_reject_outcome_mutation();

revoke update, delete, truncate on public.wow_outcomes from service_role;
revoke update, delete, truncate on public.wow_event_outcomes from service_role;
revoke update, delete, truncate on public.wow_ncaaf_outcomes from service_role;

revoke all on function public.wow_mlb_resolve_game_identity(text,timestamptz) from public, anon, authenticated;
revoke all on function public.wow_mlb_team_identity_matches(text,jsonb) from public, anon, authenticated;
revoke all on function public.wow_grade_mlb_pitcher_strikeout_prediction(uuid) from public, anon, authenticated;
revoke all on function public.wow_grade_mlb_event_prediction(uuid) from public, anon, authenticated;
revoke all on function public.wow_governed_auto_grade_predictions(integer) from public, anon, authenticated;

grant execute on function public.wow_mlb_resolve_game_identity(text,timestamptz) to service_role;
grant execute on function public.wow_grade_mlb_pitcher_strikeout_prediction(uuid) to service_role;
grant execute on function public.wow_grade_mlb_event_prediction(uuid) to service_role;
grant execute on function public.wow_governed_auto_grade_predictions(integer) to service_role;

-- One recurring primary-ledger dispatcher. It remains grading-only and cannot execute wagers.
do $$
declare
  v_jobid bigint;
begin
  select jobid into v_jobid from cron.job where jobname = 'wow-governed-primary-ledger-auto-grade' limit 1;
  if v_jobid is not null then
    perform cron.unschedule(v_jobid);
  end if;
  perform cron.schedule(
    'wow-governed-primary-ledger-auto-grade',
    '*/15 * * * *',
    'select public.wow_governed_auto_grade_predictions();'
  );
end;
$$;
