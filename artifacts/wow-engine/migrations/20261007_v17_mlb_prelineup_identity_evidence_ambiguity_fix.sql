-- Follow-up for #1459 / #1460: remove PL/pgSQL variable-column ambiguity.
-- Class B evidence-composition only. No probability math or terminal override.

create or replace function public.wow_v17_hydrate_mlb_prelineup_identity_evidence(
  p_event_prediction_id uuid,
  p_score_snapshot_id uuid
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  s public.wow_mlb_forward_score_snapshots%rowtype;
  se public.wow_mlb_forward_shadow_events%rowtype;
  v_source_name constant text := 'MLB_STATS_API_CANONICAL_LEDGER';
  v_source_ref text;
  v_payload jsonb;
  v_payload_hash text;
  v_evidence_id uuid;
  v_evidence_time timestamptz;
  v_next_attempt integer;
begin
  select * into r
  from public.wow_event_predictions
  where event_prediction_id = p_event_prediction_id
  for update;

  if not found then
    return jsonb_build_object(
      'status','HOLD',
      'code','EVENT_PREDICTION_NOT_FOUND',
      'identity_evidence_hydrated',false,
      'can_execute',false
    );
  end if;

  select * into s
  from public.wow_mlb_forward_score_snapshots
  where score_snapshot_id = p_score_snapshot_id;

  if not found then
    return jsonb_build_object(
      'status','HOLD',
      'code','MLB_SCORE_SNAPSHOT_NOT_FOUND',
      'identity_evidence_hydrated',false,
      'can_execute',false
    );
  end if;

  select * into se
  from public.wow_mlb_forward_shadow_events
  where shadow_event_id = s.shadow_event_id;

  if not found
     or nullif(btrim(r.official_event_id),'') is null
     or se.official_event_id is distinct from r.official_event_id
     or se.source_game_json->>'gamePk' is distinct from r.official_event_id
     or se.home_team is distinct from r.home_team
     or se.away_team is distinct from r.away_team
     or se.event_start_time is distinct from r.event_start_time then
    return jsonb_build_object(
      'status','HOLD',
      'code','MLB_CANONICAL_IDENTITY_NOT_PROVEN',
      'identity_evidence_hydrated',false,
      'can_execute',false
    );
  end if;

  v_source_ref := se.snapshot_id::text;
  v_evidence_time := coalesce(se.snapshot_timestamp, s.model_timestamp, clock_timestamp());
  v_payload := jsonb_build_object(
    'official_event_id', r.official_event_id,
    'provider', 'MLB_STATS_API',
    'canonical_source', 'WOW_MLB_FORWARD_SHADOW_EVENTS',
    'canonical_source_snapshot_id', se.snapshot_id,
    'score_snapshot_id', p_score_snapshot_id,
    'game_pk', se.source_game_json->>'gamePk',
    'official_date', se.official_date,
    'event_start_time', se.event_start_time,
    'home_team', se.home_team,
    'away_team', se.away_team,
    'model_role', 'IDENTITY_LOCK',
    'lineup_evidence_claimed', false,
    'probability_authority', false,
    'can_execute', false
  );
  v_payload_hash := encode(
    extensions.digest(convert_to(v_payload::text,'UTF8'),'sha256'),
    'hex'
  );

  if not exists (
    select 1
    from public.wow_event_source_attempts a
    where a.event_prediction_id = p_event_prediction_id
      and a.evidence_kind = 'OFFICIAL_EVENT_ID'
      and a.provider = v_source_name
      and a.attempt_status = 'SUCCESS'
      and a.source_ref = v_source_ref
  ) then
    select coalesce(max(a.attempt_order),0)+1
      into v_next_attempt
    from public.wow_event_source_attempts a
    where a.event_prediction_id = p_event_prediction_id
      and a.evidence_kind = 'OFFICIAL_EVENT_ID';

    insert into public.wow_event_source_attempts(
      source_attempt_id,event_prediction_id,evidence_kind,provider,
      attempt_order,attempted_at,attempt_status,source_ref,can_execute
    ) values (
      gen_random_uuid(),p_event_prediction_id,'OFFICIAL_EVENT_ID',v_source_name,
      v_next_attempt,v_evidence_time,'SUCCESS',v_source_ref,false
    );
  end if;

  select ee.evidence_id into v_evidence_id
  from public.wow_event_evidence ee
  where ee.event_prediction_id = p_event_prediction_id
    and ee.evidence_kind = 'OFFICIAL_EVENT_ID'
    and ee.source_name = v_source_name
    and ee.payload_hash = v_payload_hash
  order by ee.retrieved_at desc
  limit 1;

  if v_evidence_id is null then
    v_evidence_id := gen_random_uuid();
    insert into public.wow_event_evidence(
      evidence_id,event_prediction_id,evidence_kind,subject_side,
      source_name,source_ref,source_grade,evidence_status,
      evidence_timestamp,retrieved_at,freshness_ttl_seconds,
      payload_hash,evidence_value,evidence_payload,can_execute
    ) values (
      v_evidence_id,p_event_prediction_id,'OFFICIAL_EVENT_ID',null,
      v_source_name,v_source_ref,'OFFICIAL','RETRIEVED',
      v_evidence_time,clock_timestamp(),604800,
      v_payload_hash,r.official_event_id,v_payload,false
    );
  end if;

  insert into public.wow_event_scoring_evidence(
    scoring_evidence_id,event_prediction_id,scoring_snapshot_id,evidence_kind,
    evidence_id,payload_hash,evidence_timestamp,retrieved_at,model_timestamp,can_execute
  ) values (
    gen_random_uuid(),p_event_prediction_id,p_score_snapshot_id,'OFFICIAL_EVENT_ID',
    v_evidence_id,v_payload_hash,v_evidence_time,clock_timestamp(),s.model_timestamp,false
  )
  on conflict(event_prediction_id,scoring_snapshot_id,evidence_kind) do nothing;

  return jsonb_build_object(
    'status','PASS',
    'code','MLB_PRELINEUP_OFFICIAL_IDENTITY_EVIDENCE_HYDRATED',
    'official_event_id',r.official_event_id,
    'source_name',v_source_name,
    'source_ref',v_source_ref,
    'identity_evidence_hydrated',true,
    'lineup_evidence_claimed',false,
    'probability_authority',false,
    'can_execute',false
  );
exception
  when others then
    return jsonb_build_object(
      'status','HOLD',
      'code','MLB_PRELINEUP_IDENTITY_EVIDENCE_UNAVAILABLE',
      'identity_evidence_hydrated',false,
      'error_type',sqlstate,
      'can_execute',false
    );
end;
$function$;

do $verify$
declare
  helper_ddl text;
  bridge_ddl text;
begin
  helper_ddl := pg_get_functiondef(
    'public.wow_v17_hydrate_mlb_prelineup_identity_evidence(uuid,uuid)'::regprocedure
  );
  bridge_ddl := pg_get_functiondef(
    'public.wow_v17_mlb_probability_only_governance_bridge(uuid,text,text,text,text,text)'::regprocedure
  );

  if position('v_source_name' in helper_ddl) = 0
     or position('v_source_ref' in helper_ddl) = 0
     or position('v_evidence_id' in helper_ddl) = 0 then
    raise exception 'V17_REPAIR_VERIFY_FAILED: collision-proof variables missing';
  end if;
  if position('wow_v17_hydrate_mlb_prelineup_identity_evidence' in bridge_ddl) = 0 then
    raise exception 'V17_REPAIR_VERIFY_FAILED: bridge lost pre-lineup identity handoff';
  end if;
end;
$verify$;
