-- WOW MLB forward-shadow material-change collector — 2026-08-28
--
-- Replaces raw-response polling semantics with stable pregame identity
-- comparison. Dynamic game status, scores, live linescore state, and display
-- names are NOT part of the material identity.
--
-- A new shadow snapshot is created only when at least one still-future game is
-- new/materially changed, or a previously captured still-future game disappears
-- from the current official slate. This prevents live-feed churn from creating
-- duplicate calibration observations while still preserving real starter/time/
-- venue/team changes.
--
-- Safety: research only; probability publication and execution remain false.

-- Raw source bytes are evidence, not an identity key. The old uniqueness rule
-- could incorrectly collapse a real A->B->A pregame identity reversion back onto
-- an older snapshot if the official response bytes repeated. Material identity
-- comparison plus the advisory lock now owns deduplication; retain a normal
-- lookup index for raw hashes without asserting uniqueness across capture time.
alter table public.wow_mlb_forward_shadow_source_snapshots
  drop constraint if exists wow_mlb_forward_shadow_source_snapsho_slate_date_raw_sha256_key;

create index if not exists idx_wow_mlb_forward_shadow_source_slate_raw_sha256
  on public.wow_mlb_forward_shadow_source_snapshots(slate_date,raw_sha256);

create or replace function public.wow_mlb_forward_pregame_identity(p_game jsonb)
returns jsonb
language sql
immutable
set search_path to ''
as $function$
select jsonb_build_object(
  'gamePk', p_game->>'gamePk',
  'gameDate', p_game->>'gameDate',
  'officialDate', p_game->>'officialDate',
  'gameType', coalesce(p_game->>'gameType',''),
  'doubleHeader', coalesce(p_game->>'doubleHeader',''),
  'gameNumber', coalesce(p_game->>'gameNumber',''),
  'scheduledInnings', coalesce(p_game->>'scheduledInnings',''),
  'homeTeamId', coalesce(p_game#>>'{teams,home,team,id}',''),
  'awayTeamId', coalesce(p_game#>>'{teams,away,team,id}',''),
  'venueId', coalesce(p_game#>>'{venue,id}',''),
  'homeStarterId', coalesce(p_game#>>'{teams,home,probablePitcher,id}',''),
  'awayStarterId', coalesce(p_game#>>'{teams,away,probablePitcher,id}','')
);
$function$;

create or replace function public.wow_mlb_capture_forward_shadow_schedule(
  p_spec_id uuid,
  p_slate_date date
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  v_url text;
  v_resp extensions.http_response;
  v_body jsonb;
  v_snapshot_id uuid;
  v_existing_snapshot_id uuid;
  v_game jsonb;
  v_prior_game jsonb;
  v_current_identity jsonb;
  v_prior_identity jsonb;
  v_current_future_ids text[] := '{}';
  v_capture_at timestamptz;
  v_future_n integer := 0;
  v_changed_n integer := 0;
  v_missing_n integer := 0;
  v_inserted_n integer := 0;
  v_row_count integer := 0;
  v_raw_sha256 text;
begin
  if not exists (
    select 1
    from public.wow_mlb_v2d_frozen_spec
    where spec_id=p_spec_id and status='RESEARCH_FROZEN'
  ) then
    raise exception 'research-frozen spec not found';
  end if;

  -- Serialize captures for the same spec/slate so concurrent cron/manual runs
  -- cannot create duplicate material-change snapshots.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'wow_mlb_forward_capture:' || p_spec_id::text || ':' || p_slate_date::text,
      0
    )
  );

  v_url := 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date='
           || p_slate_date::text
           || '&hydrate=team,probablePitcher,venue';
  v_resp := extensions.http_get(v_url::varchar);
  if v_resp.status <> 200 then
    raise exception 'official MLB schedule unavailable status %',v_resp.status;
  end if;

  v_body := v_resp.content::jsonb;

  -- Provenance time is the time at which the complete official response is
  -- already in hand, never the pre-request time. A game that begins while the
  -- HTTP request is in flight therefore cannot be mislabeled as pregame.
  v_capture_at := clock_timestamp();

  if jsonb_array_length(coalesce(v_body->'dates','[]'::jsonb)) = 0 then
    return jsonb_build_object(
      'status','NO_SLATE_GAMES',
      'slate_date',p_slate_date,
      'research_only',true,
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  -- Compare only games that are still future after the response completed.
  -- Started games cannot trigger a new snapshot merely because live state moved.
  for v_game in
    select value
    from jsonb_array_elements(coalesce(v_body->'dates'->0->'games','[]'::jsonb))
  loop
    if nullif(v_game->>'gameDate','') is null
       or (v_game->>'gameDate')::timestamptz <= v_capture_at then
      continue;
    end if;

    v_future_n := v_future_n + 1;
    v_current_future_ids := array_append(v_current_future_ids,v_game->>'gamePk');
    v_current_identity := public.wow_mlb_forward_pregame_identity(v_game);

    select e.source_game_json
    into v_prior_game
    from public.wow_mlb_forward_shadow_events e
    where e.spec_id=p_spec_id
      and e.official_event_id=v_game->>'gamePk'
    order by e.snapshot_timestamp desc,e.shadow_event_id desc
    limit 1;

    if v_prior_game is null then
      v_changed_n := v_changed_n + 1;
    else
      v_prior_identity := public.wow_mlb_forward_pregame_identity(v_prior_game);
      if v_prior_identity is distinct from v_current_identity then
        v_changed_n := v_changed_n + 1;
      end if;
    end if;
  end loop;

  if v_future_n = 0 then
    return jsonb_build_object(
      'status','NO_FUTURE_GAMES',
      'slate_date',p_slate_date,
      'research_only',true,
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  -- Detect a previously captured game that should still be future according to
  -- the latest frozen observation but disappeared from the current official
  -- slate. Normally-started games do not qualify for this missing-game check.
  select count(*)
  into v_missing_n
  from (
    select distinct on (e.official_event_id)
           e.official_event_id,e.event_start_time
    from public.wow_mlb_forward_shadow_events e
    where e.spec_id=p_spec_id
      and e.official_date=p_slate_date
    order by e.official_event_id,e.snapshot_timestamp desc,e.shadow_event_id desc
  ) prior
  where prior.event_start_time > v_capture_at
    and not (prior.official_event_id = any(v_current_future_ids));

  if v_changed_n = 0 and v_missing_n = 0 then
    select e.snapshot_id
    into v_existing_snapshot_id
    from public.wow_mlb_forward_shadow_events e
    where e.spec_id=p_spec_id
      and e.official_date=p_slate_date
    order by e.snapshot_timestamp desc,e.shadow_event_id desc
    limit 1;

    return jsonb_build_object(
      'status','UNCHANGED_PREGAME_IDENTITY',
      'snapshot_id',v_existing_snapshot_id,
      'slate_date',p_slate_date,
      'future_games',v_future_n,
      'material_changes',0,
      'missing_future_games',0,
      'research_only',true,
      'probability_publishable',false,
      'can_execute',false
    );
  end if;

  v_raw_sha256 := encode(
    extensions.digest(convert_to(v_resp.content,'UTF8'),'sha256'),
    'hex'
  );

  -- Always create a new immutable source snapshot for a real material change,
  -- even if raw bytes happen to match an older observation after an A->B->A
  -- identity reversion. Deduplication already happened above under the lock.
  insert into public.wow_mlb_forward_shadow_source_snapshots(
    captured_at,slate_date,source_url,http_status,raw_body,raw_sha256,
    research_only,probability_publishable,can_execute
  ) values (
    v_capture_at,p_slate_date,v_url,v_resp.status,v_resp.content,v_raw_sha256,
    true,false,false
  )
  returning snapshot_id into v_snapshot_id;

  for v_game in
    select value
    from jsonb_array_elements(coalesce(v_body->'dates'->0->'games','[]'::jsonb))
  loop
    if nullif(v_game->>'gameDate','') is null
       or (v_game->>'gameDate')::timestamptz <= v_capture_at then
      continue;
    end if;

    insert into public.wow_mlb_forward_shadow_events(
      spec_id,snapshot_id,snapshot_timestamp,official_event_id,event_start_time,official_date,event_status,
      home_team_id,away_team_id,home_team,away_team,home_abbreviation,away_abbreviation,venue_id,venue_name,
      home_probable_pitcher_id,away_probable_pitcher_id,home_probable_pitcher,away_probable_pitcher,source_game_json,
      research_only,probability_publishable,can_execute
    ) values (
      p_spec_id,v_snapshot_id,v_capture_at,v_game->>'gamePk',(v_game->>'gameDate')::timestamptz,
      (v_game->>'officialDate')::date,v_game->'status'->>'detailedState',
      nullif(v_game#>>'{teams,home,team,id}','')::integer,
      nullif(v_game#>>'{teams,away,team,id}','')::integer,
      v_game#>>'{teams,home,team,name}',v_game#>>'{teams,away,team,name}',
      v_game#>>'{teams,home,team,abbreviation}',v_game#>>'{teams,away,team,abbreviation}',
      nullif(v_game#>>'{venue,id}','')::integer,v_game#>>'{venue,name}',
      nullif(v_game#>>'{teams,home,probablePitcher,id}','')::integer,
      nullif(v_game#>>'{teams,away,probablePitcher,id}','')::integer,
      v_game#>>'{teams,home,probablePitcher,fullName}',v_game#>>'{teams,away,probablePitcher,fullName}',
      v_game,true,false,false
    )
    on conflict(spec_id,official_event_id,snapshot_id) do nothing;
    get diagnostics v_row_count = row_count;
    v_inserted_n := v_inserted_n + v_row_count;
  end loop;

  return jsonb_build_object(
    'status','CAPTURED_MATERIAL_CHANGE',
    'snapshot_id',v_snapshot_id,
    'slate_date',p_slate_date,
    'future_games',v_future_n,
    'material_changes',v_changed_n,
    'missing_future_games',v_missing_n,
    'future_games_captured',v_inserted_n,
    'captured_at',v_capture_at,
    'research_only',true,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;

create or replace function public.wow_mlb_forward_auto_capture_pregame()
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  v_spec_id uuid;
  v_slate_date date := (clock_timestamp() at time zone 'America/Chicago')::date;
  v_result jsonb;
begin
  select spec_id
  into v_spec_id
  from public.wow_mlb_v2d_frozen_spec
  where status='RESEARCH_FROZEN'
  order by created_at desc
  limit 1;

  if v_spec_id is null then
    return jsonb_build_object(
      'status','BLOCKED',
      'reason','NO_RESEARCH_FROZEN_SPEC',
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

  v_result := public.wow_mlb_capture_forward_shadow_schedule(v_spec_id,v_slate_date);
  return v_result || jsonb_build_object('probability_publishable',false,'can_execute',false);
end;
$function$;

-- Capture finishes ahead of hydration (5,20,35,50) and away from the existing
-- outcome grader (0,15,30,45).
select cron.schedule(
  'wow-mlb-forward-shadow-auto-capture',
  '2,17,32,47 * * * *',
  $$select public.wow_mlb_forward_auto_capture_pregame();$$
);
