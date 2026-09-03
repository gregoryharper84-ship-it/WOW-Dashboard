-- WOW V17 shared environmental evidence provider.
--
-- Purpose: weather/environment acquisition is a shared evidence capability, not
-- a Kalshi-only silo. This provider writes normalized, fresh environmental
-- evidence into the canonical wow_event_evidence ledger consumed by sporting
-- specialists and the V17 terminal reducer. It never changes a model
-- probability, never fabricates weather, and cannot execute a wager.
--
-- MLB adapter: official MLB live feed. Kalshi/NWS/Tomorrow adapters may feed the
-- same normalized contract in follow-up migrations without changing consumers.

create or replace function public.wow_v17_hydrate_shared_environmental_evidence(
  p_event_prediction_id uuid,
  p_score_snapshot_id uuid default null
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  v_resp extensions.http_response;
  v_url text;
  v_body jsonb;
  v_weather jsonb;
  v_status jsonb;
  v_now timestamptz;
  v_payload jsonb;
  v_hash text;
  v_evidence_id uuid;
  v_source text := 'MLB_STATS_API_OFFICIAL_LIVE_FEED';
  v_value text;
begin
  select * into r
  from public.wow_event_predictions
  where event_prediction_id=p_event_prediction_id;

  if not found then
    return jsonb_build_object(
      'status','HOLD','code','EVENT_PREDICTION_NOT_FOUND',
      'environmental_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  if r.sport <> 'MLB' then
    return jsonb_build_object(
      'status','HOLD','code','SHARED_ENVIRONMENT_ADAPTER_UNAVAILABLE_FOR_SPORT',
      'sport',r.sport,'environmental_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_now := clock_timestamp();
  if r.event_start_time is null or v_now >= r.event_start_time then
    return jsonb_build_object(
      'status','HOLD','code','EVENT_NOT_PREGAME',
      'environmental_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_url := format('https://statsapi.mlb.com/api/v1.1/game/%s/feed/live',r.official_event_id);
  begin
    v_resp := extensions.http_get(v_url::varchar);
  exception when others then
    return jsonb_build_object(
      'status','HOLD','code','ENVIRONMENT_SOURCE_REQUEST_FAILED',
      'error_type',sqlstate,'source_name',v_source,
      'environmental_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end;

  if v_resp.status <> 200 then
    insert into public.wow_event_source_attempts(
      source_attempt_id,event_prediction_id,evidence_kind,provider,
      attempt_order,attempted_at,attempt_status,source_ref,can_execute
    ) values (
      gen_random_uuid(),p_event_prediction_id,'WEATHER_STATUS',v_source,
      1,v_now,'ERROR',v_url,false
    );
    return jsonb_build_object(
      'status','HOLD','code','ENVIRONMENT_SOURCE_HTTP_ERROR',
      'http_status',v_resp.status,'source_name',v_source,
      'environmental_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_body := v_resp.content::jsonb;
  v_weather := v_body#>'{gameData,weather}';
  v_status := v_body#>'{gameData,status}';

  if coalesce(v_status->>'abstractGameState','') not in ('Preview','Pre-Game')
     and coalesce(v_status->>'detailedState','') not in ('Scheduled','Pre-Game') then
    return jsonb_build_object(
      'status','HOLD','code','OFFICIAL_EVENT_STATUS_NOT_PREGAME',
      'official_status',v_status,
      'environmental_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  if v_weather is null or jsonb_typeof(v_weather) <> 'object'
     or coalesce(v_weather,'{}'::jsonb)='{}'::jsonb then
    insert into public.wow_event_source_attempts(
      source_attempt_id,event_prediction_id,evidence_kind,provider,
      attempt_order,attempted_at,attempt_status,source_ref,can_execute
    ) values (
      gen_random_uuid(),p_event_prediction_id,'WEATHER_STATUS',v_source,
      1,v_now,'UNAVAILABLE',v_url,false
    );
    return jsonb_build_object(
      'status','HOLD','code','OFFICIAL_WEATHER_UNAVAILABLE',
      'source_name',v_source,'environmental_evidence_produced',false,
      'probability_publishable',false,'can_execute',false
    );
  end if;

  v_payload := jsonb_build_object(
    'schema_version','WOW_V17_SHARED_ENVIRONMENTAL_EVIDENCE_V1',
    'event_prediction_id',p_event_prediction_id,
    'official_event_id',r.official_event_id,
    'venue',r.venue,
    'sport',r.sport,
    'temperature_f',nullif(v_weather->>'temp',''),
    'wind',nullif(v_weather->>'wind',''),
    'condition',nullif(v_weather->>'condition',''),
    'official_weather',v_weather,
    'official_event_status',v_status,
    'source_name',v_source,
    'source_ref',v_url,
    'retrieved_at',v_now,
    'model_input_semantics','CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT',
    'probability_adjustment_applied',false,
    'can_execute',false
  );
  v_hash := md5(v_payload::text);
  v_value := concat_ws(' | ',
    nullif(v_weather->>'condition',''),
    case when nullif(v_weather->>'temp','') is not null then (v_weather->>'temp') || 'F' end,
    nullif(v_weather->>'wind','')
  );

  insert into public.wow_event_source_attempts(
    source_attempt_id,event_prediction_id,evidence_kind,provider,
    attempt_order,attempted_at,attempt_status,source_ref,can_execute
  ) values (
    gen_random_uuid(),p_event_prediction_id,'WEATHER_STATUS',v_source,
    1,v_now,'SUCCESS',v_url,false
  );

  select evidence_id into v_evidence_id
  from public.wow_event_evidence
  where event_prediction_id=p_event_prediction_id
    and evidence_kind='WEATHER_STATUS'
    and source_name=v_source
    and payload_hash=v_hash
  order by retrieved_at desc
  limit 1;

  if v_evidence_id is null then
    v_evidence_id := gen_random_uuid();
    insert into public.wow_event_evidence(
      evidence_id,event_prediction_id,evidence_kind,subject_side,
      source_name,source_ref,source_grade,evidence_status,
      evidence_timestamp,retrieved_at,freshness_ttl_seconds,payload_hash,
      evidence_value,evidence_payload,can_execute
    ) values (
      v_evidence_id,p_event_prediction_id,'WEATHER_STATUS',null,
      v_source,v_url,'OFFICIAL','RETRIEVED',
      v_now,v_now,1800,v_hash,
      coalesce(nullif(v_value,''),'OFFICIAL_WEATHER_RETRIEVED'),v_payload,false
    );
  end if;

  return jsonb_build_object(
    'status','PASS',
    'schema_version','WOW_V17_SHARED_ENVIRONMENTAL_EVIDENCE_V1',
    'event_prediction_id',p_event_prediction_id,
    'score_snapshot_id',p_score_snapshot_id,
    'evidence_id',v_evidence_id,
    'source_name',v_source,
    'weather',v_weather,
    'model_input_semantics','CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT',
    'probability_adjustment_applied',false,
    'environmental_evidence_produced',true,
    'probability_publishable',false,
    'can_execute',false
  );
end;
$function$;
