-- WOW V17 MLB final-refresh false weather material-change repair.
-- Related: #798.
--
-- Class B only: evidence semantics / final-refresh lifecycle. This migration
-- does not change any fitted sporting probability, coefficient, calibrator,
-- threshold, publication authority, or execution capability.
--
-- Root cause reproduced from persisted MLB rows:
--   * the shared environmental provider hashes the complete evidence payload,
--     including volatile retrieved_at metadata;
--   * the MLB governance hydrator and the shared provider use different payload
--     shapes/hash algorithms for the same official weather observation;
--   * final refresh compares those writer-specific hashes as though every
--     difference were a fitted-model input change.
--
-- Weather is explicitly context-only in the active certified V2D baseline.
-- Missing/stale weather must still fail closed. A future weather payload that is
-- marked as a probability adjustment / certified input remains material and
-- requires a model rescore.

create or replace function public.wow_v17_weather_semantic_hash(
  p_weather jsonb,
  p_model_input_semantics text default 'CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT',
  p_probability_adjustment_applied boolean default false
)
returns text
language sql
immutable
set search_path to ''
as $function$
  select encode(
    extensions.digest(
      convert_to(
        jsonb_build_object(
          'schema_version','WOW_V17_WEATHER_SEMANTIC_HASH_V1',
          'official_weather',coalesce(p_weather,'{}'::jsonb),
          'model_input_semantics',coalesce(p_model_input_semantics,''),
          'probability_adjustment_applied',coalesce(p_probability_adjustment_applied,false)
        )::text,
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );
$function$;

create or replace function public.wow_v17_weather_payload_is_context_only(p_payload jsonb)
returns boolean
language sql
immutable
set search_path to ''
as $function$
  select case
    when p_payload is null then false
    when lower(coalesce(p_payload->>'probability_adjustment_applied','false')) = 'true' then false
    when upper(coalesce(p_payload->>'model_input_semantics','')) = 'CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT' then true
    when upper(coalesce(p_payload->>'model_role','')) = 'CONTEXT_ONLY_NOT_NUMERIC_IN_CERTIFIED_V2D_BASELINE' then true
    else false
  end;
$function$;

create or replace function public.wow_v17_evidence_change_requires_rescore(
  p_evidence_kind text,
  p_latest_payload jsonb,
  p_scoring_payload jsonb
)
returns boolean
language sql
immutable
set search_path to ''
as $function$
  select case
    -- Fail safe by default. Weather is the only exception, and only while BOTH
    -- the scoring-time and current payload explicitly identify it as context
    -- only / non-probability-producing. If either side becomes a certified
    -- probability input, hash changes are material again automatically.
    when upper(coalesce(p_evidence_kind,'')) = 'WEATHER_STATUS'
      and public.wow_v17_weather_payload_is_context_only(p_latest_payload)
      and public.wow_v17_weather_payload_is_context_only(p_scoring_payload)
      then false
    else true
  end;
$function$;

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
    'semantic_hash_version','WOW_V17_WEATHER_SEMANTIC_HASH_V1',
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

  -- IMPORTANT: hash only the semantic weather/model-input contract. Volatile
  -- retrieval time and writer metadata remain in evidence_payload for audit but
  -- cannot manufacture a false material change.
  v_hash := public.wow_v17_weather_semantic_hash(
    v_weather,
    'CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT',
    false
  );

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
    'semantic_hash_version','WOW_V17_WEATHER_SEMANTIC_HASH_V1',
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

create or replace function public.wow_v17_reconcile_probability_only_snapshot(p_event_prediction_id uuid)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  v_scoring_n integer;
  v_mismatch_n integer;
begin
  select * into r
  from public.wow_event_predictions
  where event_prediction_id=p_event_prediction_id
  for update;
  if not found then raise exception 'event prediction not found'; end if;

  select count(*) into v_scoring_n
  from public.wow_event_scoring_evidence
  where event_prediction_id=p_event_prediction_id
    and scoring_snapshot_id=r.scoring_snapshot_id;

  with latest as (
    select distinct on(ee.evidence_kind)
      ee.evidence_kind,
      ee.payload_hash,
      ee.evidence_payload
    from public.wow_event_evidence ee
    where ee.event_prediction_id=p_event_prediction_id
    order by ee.evidence_kind,ee.retrieved_at desc,ee.evidence_timestamp desc
  ), scored as (
    select se.evidence_kind,se.payload_hash,ee.evidence_payload
    from public.wow_event_scoring_evidence se
    left join public.wow_event_evidence ee on ee.evidence_id=se.evidence_id
    where se.event_prediction_id=p_event_prediction_id
      and se.scoring_snapshot_id=r.scoring_snapshot_id
  )
  select count(*) into v_mismatch_n
  from latest l
  left join scored s on s.evidence_kind=l.evidence_kind
  where l.payload_hash is distinct from s.payload_hash
    and public.wow_v17_evidence_change_requires_rescore(
      l.evidence_kind,l.evidence_payload,s.evidence_payload
    );

  if v_scoring_n>=10
     and v_mismatch_n=0
     and r.model_timestamp is not null
     and r.latest_material_update_timestamp is not null
     and r.model_timestamp>=r.latest_material_update_timestamp then
    update public.wow_event_predictions
    set probability_invalidated=false,
        rerun_required=false
    where event_prediction_id=p_event_prediction_id;
    return jsonb_build_object(
      'status','PASS','scoring_evidence_count',v_scoring_n,
      'material_mismatch_count',0,'can_execute',false
    );
  end if;

  return jsonb_build_object(
    'status','HOLD','scoring_evidence_count',v_scoring_n,
    'material_mismatch_count',v_mismatch_n,'can_execute',false
  );
end;
$function$;

create or replace function public.wow_v17_probability_only_final_refresh(
  p_event_prediction_id uuid,
  p_refresh_snapshot_id uuid,
  p_as_of timestamptz default now()
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  k text;
  e record;
  sc record;
  reasons text[]:='{}';
  event_fresh boolean:=true;
  critical_fresh boolean:=true;
  settlement_fresh boolean:=true;
  material_change boolean:=false;
  v_status text;
  age_seconds integer;
  event_age integer;
  critical_age integer:=0;
  settlement_age integer;
  kinds text[]:=array[
    'EVENT_STATUS','HOME_STARTER','AWAY_STARTER','HOME_LINEUP','AWAY_LINEUP',
    'BULLPEN_STATUS','WEATHER_STATUS','INJURY_STATUS','SETTLEMENT'
  ];
begin
  select * into r
  from public.wow_event_predictions
  where event_prediction_id=p_event_prediction_id
  for update;
  if not found then raise exception 'event prediction not found'; end if;

  foreach k in array kinds loop
    select evidence_status,source_grade,evidence_timestamp,freshness_ttl_seconds,
           payload_hash,retrieved_at,evidence_payload
    into e
    from public.wow_event_evidence
    where event_prediction_id=p_event_prediction_id
      and evidence_kind=k
    order by retrieved_at desc,evidence_timestamp desc
    limit 1;

    if not found then
      reasons:=array_append(reasons,k||'_NOT_CALLED');
      if k='EVENT_STATUS' then event_fresh:=false;
      elsif k='SETTLEMENT' then settlement_fresh:=false;
      else critical_fresh:=false;
      end if;
    else
      age_seconds:=greatest(0,extract(epoch from(p_as_of-e.evidence_timestamp))::integer);
      if k='EVENT_STATUS' then event_age:=age_seconds;
      elsif k='SETTLEMENT' then settlement_age:=age_seconds;
      else critical_age:=greatest(critical_age,age_seconds);
      end if;

      if e.evidence_status not in ('RETRIEVED','NOT_APPLICABLE')
         or e.source_grade='PROXY'
         or e.payload_hash is null
         or age_seconds>e.freshness_ttl_seconds then
        reasons:=array_append(reasons,k||'_NOT_FRESH');
        if k='EVENT_STATUS' then event_fresh:=false;
        elsif k='SETTLEMENT' then settlement_fresh:=false;
        else critical_fresh:=false;
        end if;
      end if;

      select se.payload_hash,ee.evidence_payload
      into sc
      from public.wow_event_scoring_evidence se
      left join public.wow_event_evidence ee on ee.evidence_id=se.evidence_id
      where se.event_prediction_id=p_event_prediction_id
        and se.scoring_snapshot_id=r.scoring_snapshot_id
        and se.evidence_kind=k
      limit 1;

      if not found then
        reasons:=array_append(reasons,k||'_SCORING_HASH_MISSING');
        material_change:=true;
      elsif e.payload_hash is distinct from sc.payload_hash
        and public.wow_v17_evidence_change_requires_rescore(
          k,e.evidence_payload,sc.evidence_payload
        ) then
        reasons:=array_append(reasons,k||'_MATERIAL_CHANGE_AFTER_MODEL');
        material_change:=true;
      end if;
    end if;
  end loop;

  if r.event_status<>'SCHEDULED' then
    reasons:=array_append(reasons,'EVENT_NOT_SCHEDULED');
    event_fresh:=false;
  end if;
  if r.source_conflict then
    reasons:=array_append(reasons,'SOURCE_CONFLICT');
    critical_fresh:=false;
  end if;

  v_status:=case
    when material_change then 'RERUN_REQUIRED'
    when cardinality(reasons)>0 then 'FAIL'
    else 'PASS'
  end;

  update public.wow_event_predictions
  set final_refresh_snapshot_id=p_refresh_snapshot_id,
      final_refresh_timestamp=p_as_of,
      final_refresh_status=v_status,
      final_refresh_reasons=reasons,
      event_status_fresh_at_refresh=event_fresh,
      critical_status_fresh_at_refresh=critical_fresh,
      market_fresh_at_refresh=false,
      settlement_fresh_at_refresh=settlement_fresh,
      event_status_age_seconds_at_refresh=event_age,
      critical_status_age_seconds_at_refresh=critical_age,
      market_age_seconds_at_refresh=null,
      settlement_age_seconds_at_refresh=settlement_age,
      probability_invalidated=case when material_change then true else probability_invalidated end,
      rerun_required=case when material_change then true else rerun_required end,
      rank_eligible=case when v_status='PASS' then rank_eligible else false end,
      rank_eligibility_status=case when v_status='PASS' then rank_eligibility_status else 'FAIL' end,
      probability_publishable=false
  where event_prediction_id=p_event_prediction_id;

  return jsonb_build_object(
    'final_refresh_status',v_status,
    'reasons',to_jsonb(reasons),
    'event_status_fresh',event_fresh,
    'critical_status_fresh',critical_fresh,
    'market_fresh',false,
    'market_required',false,
    'settlement_fresh',settlement_fresh,
    'material_change_after_model',material_change,
    'probability_invalidated',material_change,
    'rerun_required',material_change,
    'can_execute',false
  );
end;
$function$;
