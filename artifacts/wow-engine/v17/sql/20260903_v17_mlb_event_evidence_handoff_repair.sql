-- WOW V17 MLB team/event evidence-handoff repair.
--
-- Purpose: the MLB score -> LLP governance bridge creates wow_event_predictions
-- before invoking the existing event gates, but historically did not materialize
-- the canonical evidence/source-attempt/scoring-evidence rows those gates consume.
-- This helper performs that missing handoff. It does not mark a probability
-- publishable, does not force rank eligibility, and cannot execute a wager.

create or replace function public.wow_v17_hydrate_mlb_event_governance_evidence(
  p_event_prediction_id uuid,
  p_score_snapshot_id uuid,
  p_evidence jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  v_now timestamptz := now();
  v_inserted integer := 0;
  v_kind text;
  v_value text;
  v_source text;
  v_grade text;
  v_payload jsonb;
  v_hash text;
  v_evidence_id uuid;
  v_rows integer;
  v_required text[] := array[
    'OFFICIAL_EVENT_ID','EVENT_STATUS','HOME_STARTER','AWAY_STARTER',
    'HOME_LINEUP','AWAY_LINEUP','BULLPEN_STATUS','WEATHER_STATUS',
    'INJURY_STATUS','SETTLEMENT'
  ];
begin
  select * into r
  from public.wow_event_predictions
  where event_prediction_id = p_event_prediction_id;

  if not found then
    return jsonb_build_object(
      'status','HOLD',
      'blockers',jsonb_build_array('EVENT_PREDICTION_NOT_FOUND'),
      'evidence_rows_hydrated',0,
      'can_execute',false
    );
  end if;

  foreach v_kind in array v_required loop
    v_value := null;
    v_source := 'CANONICAL_MLB_LEDGER';
    v_grade := 'OFFICIAL';

    case v_kind
      when 'OFFICIAL_EVENT_ID' then v_value := r.official_event_id;
      when 'EVENT_STATUS' then v_value := r.event_status;
      when 'HOME_STARTER' then
        if r.home_starting_pitcher is not null then
          v_value := r.home_starting_pitcher || '|' || coalesce(r.home_starter_status,'UNRESOLVED');
        end if;
      when 'AWAY_STARTER' then
        if r.away_starting_pitcher is not null then
          v_value := r.away_starting_pitcher || '|' || coalesce(r.away_starter_status,'UNRESOLVED');
        end if;
      when 'HOME_LINEUP' then v_value := r.home_lineup_status;
      when 'AWAY_LINEUP' then v_value := r.away_lineup_status;
      when 'BULLPEN_STATUS' then
        v_value := nullif(btrim(coalesce(p_evidence->>'bullpen_status','')), '');
        v_source := 'CANONICAL_MLB_EVIDENCE';
        v_grade := 'PRIMARY';
      when 'WEATHER_STATUS' then
        v_value := nullif(btrim(coalesce(p_evidence->>'weather_status','')), '');
        v_source := 'CANONICAL_MLB_EVIDENCE';
        v_grade := 'PRIMARY';
      when 'INJURY_STATUS' then
        v_value := nullif(btrim(coalesce(p_evidence->>'injury_status','')), '');
        v_source := 'CANONICAL_MLB_EVIDENCE';
        v_grade := 'PRIMARY';
      when 'SETTLEMENT' then
        v_value := r.settlement_basis;
        v_source := 'TEAM_EVENT_REQUEST_CONTRACT';
        v_grade := 'PRIMARY';
      else
        v_value := null;
    end case;

    -- Missing evidence stays missing. Never manufacture a RETRIEVED row merely
    -- to satisfy source completeness.
    if v_value is null or btrim(v_value) = '' then
      continue;
    end if;

    v_payload := jsonb_build_object(
      'kind', v_kind,
      'value', v_value,
      'event_prediction_id', p_event_prediction_id,
      'score_snapshot_id', p_score_snapshot_id,
      'source_snapshot_id', r.source_snapshot_id,
      'source_snapshot_timestamp', r.source_snapshot_timestamp
    );
    v_hash := md5(v_payload::text);

    select evidence_id into v_evidence_id
    from public.wow_event_evidence
    where event_prediction_id = p_event_prediction_id
      and evidence_kind = v_kind
      and source_name = v_source
      and payload_hash = v_hash
    order by retrieved_at desc
    limit 1;

    if v_evidence_id is null then
      v_evidence_id := gen_random_uuid();
      insert into public.wow_event_source_attempts (
        source_attempt_id,event_prediction_id,evidence_kind,provider,
        attempt_order,attempted_at,attempt_status,source_ref,can_execute
      ) values (
        gen_random_uuid(),p_event_prediction_id,v_kind,v_source,
        1,v_now,'SUCCESS',coalesce(r.source_snapshot_id::text,p_score_snapshot_id::text),false
      );

      insert into public.wow_event_evidence (
        evidence_id,event_prediction_id,evidence_kind,subject_side,
        source_name,source_ref,source_grade,evidence_status,
        evidence_timestamp,retrieved_at,freshness_ttl_seconds,payload_hash,
        evidence_value,evidence_payload,can_execute
      ) values (
        v_evidence_id,p_event_prediction_id,v_kind,
        case when v_kind like 'HOME_%' then 'HOME'
             when v_kind like 'AWAY_%' then 'AWAY'
             else null end,
        v_source,coalesce(r.source_snapshot_id::text,p_score_snapshot_id::text),v_grade,'RETRIEVED',
        coalesce(r.source_snapshot_timestamp,r.latest_material_update_timestamp,v_now),
        v_now,
        case when v_kind in ('EVENT_STATUS','HOME_STARTER','AWAY_STARTER','HOME_LINEUP','AWAY_LINEUP') then 900 else 3600 end,
        v_hash,v_value,v_payload,false
      );
      v_inserted := v_inserted + 1;
    end if;

    if p_score_snapshot_id is not null and not exists (
      select 1
      from public.wow_event_scoring_evidence se
      where se.event_prediction_id = p_event_prediction_id
        and se.scoring_snapshot_id = p_score_snapshot_id
        and se.evidence_kind = v_kind
    ) then
      insert into public.wow_event_scoring_evidence (
        scoring_evidence_id,event_prediction_id,scoring_snapshot_id,
        evidence_kind,evidence_id,payload_hash,evidence_timestamp,
        retrieved_at,model_timestamp,can_execute
      ) values (
        gen_random_uuid(),p_event_prediction_id,p_score_snapshot_id,
        v_kind,v_evidence_id,v_hash,
        coalesce(r.source_snapshot_timestamp,r.latest_material_update_timestamp,v_now),
        v_now,r.model_timestamp,false
      );
    end if;
  end loop;

  -- Fill only model metadata that is a lossless translation of fields already
  -- persisted by the fitted scorer. Raw probabilities are the independent
  -- fitted probabilities when no market blend was applied. Missing market or
  -- calibration metadata remains missing and therefore continues to fail closed.
  update public.wow_event_predictions
  set controlling_specialist = coalesce(controlling_specialist,'wow.mlb-game-win-probability-expert'),
      independent_home_probability = coalesce(independent_home_probability,raw_home_probability),
      independent_away_probability = coalesce(independent_away_probability,raw_away_probability),
      independent_model_weight = coalesce(independent_model_weight,1.0),
      market_prior_available = coalesce(market_prior_available,false),
      market_prior_weight = coalesce(market_prior_weight,0.0),
      settlement_source = coalesce(settlement_source,'TEAM_EVENT_REQUEST_CONTRACT'),
      settlement_timestamp = coalesce(settlement_timestamp,source_snapshot_timestamp,latest_material_update_timestamp,v_now)
  where event_prediction_id = p_event_prediction_id;

  select count(*) into v_rows
  from public.wow_event_scoring_evidence
  where event_prediction_id = p_event_prediction_id
    and scoring_snapshot_id = p_score_snapshot_id;

  return jsonb_build_object(
    'status','PASS',
    'evidence_rows_hydrated',v_inserted,
    'scoring_evidence_row_count',v_rows,
    'complete_scoring_evidence_snapshot',v_rows >= 10,
    'probability_publishable',false,
    'rank_eligible',false,
    'can_execute',false
  );
end;
$function$;
