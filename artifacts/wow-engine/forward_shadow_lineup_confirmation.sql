-- WOW MLB forward-shadow official lineup confirmation — 2026-08-28
--
-- Adds timestamped official batting-order provenance to the research-only
-- forward-shadow lane. A lineup is CONFIRMED only when the official MLB live
-- feed exposes exactly nine unique batting-order player IDs for both teams,
-- the response is received strictly before the scheduled event start, and the
-- official feed proves no real gameplay has begun. Pregame Game Advisory /
-- warmup events are tolerated; any recorded pitch or completed non-advisory
-- play blocks confirmation.
--
-- Repeated identical confirmations are idempotent. A material batting-order
-- change creates a new immutable lineup snapshot and re-scores through the
-- existing frozen model path. This never publishes probability or execution.

create table if not exists public.wow_mlb_forward_lineup_snapshots (
  lineup_snapshot_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  shadow_event_id uuid not null references public.wow_mlb_forward_shadow_events(shadow_event_id),
  official_event_id text not null,
  captured_at timestamptz not null,
  home_team_id integer not null,
  away_team_id integer not null,
  home_batting_order integer[] not null check (cardinality(home_batting_order)=9),
  away_batting_order integer[] not null check (cardinality(away_batting_order)=9),
  lineup_identity_sha256 text not null check (length(lineup_identity_sha256)=64),
  official_abstract_state text,
  official_detailed_state text,
  official_pitch_events_at_capture integer not null check (official_pitch_events_at_capture>=0),
  official_completed_plays_at_capture integer not null check (official_completed_plays_at_capture>=0),
  strict_pregame_provenance boolean not null default true check (strict_pregame_provenance=true),
  source_url text not null,
  http_status integer not null check (http_status=200),
  raw_body text not null,
  raw_sha256 text not null check (length(raw_sha256)=64),
  lineup_status text not null default 'CONFIRMED' check (lineup_status='CONFIRMED'),
  research_only boolean not null default true check (research_only=true),
  probability_publishable boolean not null default false check (probability_publishable=false),
  can_execute boolean not null default false check (can_execute=false),
  constraint uq_wow_mlb_forward_lineup_identity unique (shadow_event_id,lineup_identity_sha256)
);

alter table public.wow_mlb_forward_lineup_snapshots enable row level security;

create index if not exists idx_wow_mlb_forward_lineup_event_time
  on public.wow_mlb_forward_lineup_snapshots(shadow_event_id,captured_at desc);

alter table public.wow_mlb_forward_shadow_events
  add column if not exists lineup_snapshot_id uuid,
  add column if not exists lineup_confirmed_at timestamptz;

do $ddl$
begin
  if not exists (
    select 1 from pg_catalog.pg_constraint
    where conname='wow_mlb_forward_shadow_events_lineup_snapshot_id_fkey'
      and conrelid='public.wow_mlb_forward_shadow_events'::regclass
  ) then
    alter table public.wow_mlb_forward_shadow_events
      add constraint wow_mlb_forward_shadow_events_lineup_snapshot_id_fkey
      foreign key (lineup_snapshot_id)
      references public.wow_mlb_forward_lineup_snapshots(lineup_snapshot_id);
  end if;
end
$ddl$;

create or replace function public.wow_mlb_forward_lineup_snapshot_immutable()
returns trigger
language plpgsql
set search_path to ''
as $function$
begin
  raise exception 'wow_mlb_forward_lineup_snapshots is immutable';
end;
$function$;

drop trigger if exists trg_wow_mlb_forward_lineup_snapshot_immutable_upd
  on public.wow_mlb_forward_lineup_snapshots;
create trigger trg_wow_mlb_forward_lineup_snapshot_immutable_upd
  before update on public.wow_mlb_forward_lineup_snapshots
  for each row execute function public.wow_mlb_forward_lineup_snapshot_immutable();

drop trigger if exists trg_wow_mlb_forward_lineup_snapshot_immutable_del
  on public.wow_mlb_forward_lineup_snapshots;
create trigger trg_wow_mlb_forward_lineup_snapshot_immutable_del
  before delete on public.wow_mlb_forward_lineup_snapshots
  for each row execute function public.wow_mlb_forward_lineup_snapshot_immutable();

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

  if clock_timestamp() >= e.event_start_time then
    return jsonb_build_object(
      'status','BLOCKED','reason','EVENT_NOT_PREGAME',
      'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_url := format('https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',e.official_event_id);
  r := extensions.http_get(v_url::varchar);
  if r.status <> 200 then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_LINEUP_SOURCE_HTTP_ERROR',
      'http_status',r.status,'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_body := r.content::jsonb;
  v_capture_at := clock_timestamp();
  if v_capture_at >= e.event_start_time then
    return jsonb_build_object(
      'status','BLOCKED','reason','EVENT_STARTED_DURING_LINEUP_FETCH',
      'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_abstract_state := v_body#>>'{gameData,status,abstractGameState}';
  v_detailed_state := v_body#>>'{gameData,status,detailedState}';

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
     or coalesce(v_abstract_state,'')='Final'
     or coalesce(v_detailed_state,'') in ('In Progress','Game Over','Final') then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_GAMEPLAY_ALREADY_STARTED',
      'official_abstract_state',v_abstract_state,
      'official_detailed_state',v_detailed_state,
      'pitch_events',v_pitch_n,'completed_plays',v_completed_play_n,
      'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_home_team_id := nullif(v_body#>>'{gameData,teams,home,id}','')::integer;
  v_away_team_id := nullif(v_body#>>'{gameData,teams,away,id}','')::integer;
  if v_home_team_id is distinct from e.home_team_id
     or v_away_team_id is distinct from e.away_team_id then
    return jsonb_build_object(
      'status','BLOCKED','reason','OFFICIAL_LINEUP_TEAM_ID_MISMATCH',
      'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  select coalesce(array_agg(value::integer order by ord),'{}'::integer[])
  into v_home_order
  from jsonb_array_elements_text(coalesce(v_body#>'{liveData,boxscore,teams,home,battingOrder}','[]'::jsonb))
       with ordinality as x(value,ord);

  select coalesce(array_agg(value::integer order by ord),'{}'::integer[])
  into v_away_order
  from jsonb_array_elements_text(coalesce(v_body#>'{liveData,boxscore,teams,away,battingOrder}','[]'::jsonb))
       with ordinality as x(value,ord);

  if cardinality(v_home_order) <> 9
     or cardinality(v_away_order) <> 9
     or (select count(distinct player_id) from unnest(v_home_order) player_id) <> 9
     or (select count(distinct player_id) from unnest(v_away_order) player_id) <> 9 then
    return jsonb_build_object(
      'status','DELAYED','reason','OFFICIAL_LINEUP_NOT_AVAILABLE',
      'home_batting_order_n',cardinality(v_home_order),
      'away_batting_order_n',cardinality(v_away_order),
      'shadow_event_id',p_shadow_event_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_identity := jsonb_build_object(
    'official_event_id',e.official_event_id,
    'home_team_id',v_home_team_id,
    'away_team_id',v_away_team_id,
    'home_batting_order',to_jsonb(v_home_order),
    'away_batting_order',to_jsonb(v_away_order)
  );
  v_identity_sha := encode(extensions.digest(convert_to(v_identity::text,'UTF8'),'sha256'),'hex');
  v_raw_sha := encode(extensions.digest(convert_to(r.content,'UTF8'),'sha256'),'hex');
  v_was_confirmed := e.lineup_status='CONFIRMED';

  select * into v_existing
  from public.wow_mlb_forward_lineup_snapshots
  where shadow_event_id=p_shadow_event_id
    and lineup_identity_sha256=v_identity_sha
  order by captured_at desc
  limit 1;

  if found then
    update public.wow_mlb_forward_shadow_events
    set lineup_status='CONFIRMED',
        lineup_snapshot_id=v_existing.lineup_snapshot_id,
        lineup_confirmed_at=v_existing.captured_at
    where shadow_event_id=p_shadow_event_id;

    if not v_was_confirmed and e.feature_hydration_status='PASS' then
      v_score := public.wow_mlb_forward_score_event(p_shadow_event_id);
      v_score_status := v_score->>'status';
      v_score_snapshot_id := v_score->>'score_snapshot_id';
    end if;

    return jsonb_build_object(
      'status',case when v_was_confirmed then 'UNCHANGED_CONFIRMED_LINEUP' else 'CONFIRMED_FROM_EXISTING_SNAPSHOT' end,
      'shadow_event_id',p_shadow_event_id,
      'lineup_snapshot_id',v_existing.lineup_snapshot_id,
      'lineup_identity_sha256',v_identity_sha,
      'score_status',v_score_status,
      'score_snapshot_id',v_score_snapshot_id,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  insert into public.wow_mlb_forward_lineup_snapshots(
    shadow_event_id,official_event_id,captured_at,home_team_id,away_team_id,
    home_batting_order,away_batting_order,lineup_identity_sha256,
    official_abstract_state,official_detailed_state,
    official_pitch_events_at_capture,official_completed_plays_at_capture,strict_pregame_provenance,
    source_url,http_status,raw_body,raw_sha256,
    lineup_status,research_only,probability_publishable,can_execute
  ) values (
    p_shadow_event_id,e.official_event_id,v_capture_at,v_home_team_id,v_away_team_id,
    v_home_order,v_away_order,v_identity_sha,
    v_abstract_state,v_detailed_state,v_pitch_n,v_completed_play_n,true,
    v_url,r.status,r.content,v_raw_sha,
    'CONFIRMED',true,false,false
  ) returning lineup_snapshot_id into v_lineup_snapshot_id;

  update public.wow_mlb_forward_shadow_events
  set lineup_status='CONFIRMED',
      lineup_snapshot_id=v_lineup_snapshot_id,
      lineup_confirmed_at=v_capture_at
  where shadow_event_id=p_shadow_event_id;

  if e.feature_hydration_status='PASS' then
    v_score := public.wow_mlb_forward_score_event(p_shadow_event_id);
    v_score_status := v_score->>'status';
    v_score_snapshot_id := v_score->>'score_snapshot_id';
  end if;

  return jsonb_build_object(
    'status',case when v_was_confirmed then 'CONFIRMED_LINEUP_CHANGED' else 'CONFIRMED' end,
    'shadow_event_id',p_shadow_event_id,
    'lineup_snapshot_id',v_lineup_snapshot_id,
    'lineup_identity_sha256',v_identity_sha,
    'home_batting_order_n',cardinality(v_home_order),
    'away_batting_order_n',cardinality(v_away_order),
    'official_abstract_state',v_abstract_state,
    'official_detailed_state',v_detailed_state,
    'pitch_events',v_pitch_n,'completed_plays',v_completed_play_n,
    'score_status',v_score_status,
    'score_snapshot_id',v_score_snapshot_id,
    'probability_publishable',false,'can_execute',false
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
  where exists (
    select 1 from public.wow_mlb_forward_shadow_events se
    where se.snapshot_id=s.snapshot_id
      and se.event_start_time > clock_timestamp()
  )
  order by s.captured_at desc
  limit 1;

  if v_snapshot_id is null then
    return jsonb_build_object(
      'status','NO_PREGAME_SNAPSHOT',
      'probability_publishable',false,'can_execute',false
    );
  end if;

  for e in
    select shadow_event_id,official_event_id,event_start_time,lineup_status
    from public.wow_mlb_forward_shadow_events
    where snapshot_id=v_snapshot_id
      and event_start_time > clock_timestamp()
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
        'official_event_id',e.official_event_id,
        'status',v_result->>'status',
        'reason',v_result->>'reason',
        'score_status',v_result->>'score_status',
        'probability_publishable',false,
        'can_execute',false
      ));
    exception when others then
      v_errors := v_errors + 1;
      v_results := v_results || jsonb_build_array(jsonb_build_object(
        'official_event_id',e.official_event_id,
        'status','ERROR','error_type',sqlstate,
        'probability_publishable',false,'can_execute',false
      ));
    end;
  end loop;

  return jsonb_build_object(
    'status','COMPLETE','shadow_snapshot_id',v_snapshot_id,
    'checked',v_checked,'confirmed',v_confirmed,'unchanged',v_unchanged,
    'delayed',v_delayed,'blocked',v_blocked,'errors',v_errors,
    'results',v_results,
    'probability_publishable',false,'can_execute',false
  );
end;
$function$;

select cron.schedule(
  'wow-mlb-forward-shadow-auto-lineup',
  '8,23,38,53 * * * *',
  $$select public.wow_mlb_forward_auto_confirm_lineups();$$
);
