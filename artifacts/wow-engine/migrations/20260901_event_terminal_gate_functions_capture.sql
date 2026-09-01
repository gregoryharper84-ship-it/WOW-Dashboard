-- WOW v16/v17: capture canonical source for the live event postmodel/final
-- gate function chain as a repository migration.
--
-- Context (V17 Phase-A convergence, A2 remediation): the v17 candidate
-- governance bridge (artifacts/wow-engine/v17/sql/20260831_v17_mlb_event_governance_bridge.sql)
-- calls public.wow_run_event_postmodel_gates(...) and
-- public.wow_run_event_final_gates(...) as pre-existing functions. Neither
-- function, nor the seven functions they call, had a migration file in this
-- repository -- they existed only as live objects in the
-- "wow-engine-validation" Supabase project (schema drift: applied directly,
-- never captured in git). This migration closes that gap by recording the
-- exact live definitions (via pg_get_functiondef, verified 2026-09-01) as
-- `create or replace function`, so the repository becomes the source of
-- truth going forward. This does not change behavior: every body below is a
-- byte-for-byte copy of what is already running.
--
-- Deliberately NOT in scope here: the underlying tables these functions read
-- and write (public.wow_event_predictions, public.wow_calibrators,
-- public.wow_event_evidence, public.wow_event_scoring_evidence) also predate
-- every migration file in this repository and are themselves live-only --
-- this is the same DATABASE_SCHEMA_BOOTSTRAP_GAP category named in this
-- repo's engineering contract, not something this migration should backfill
-- from introspection alone (risk of silently getting a column type,
-- default, or constraint wrong). That gap is a separate, larger piece of
-- work and is called out again in the A1/A2 remediation packet. Because
-- those tables are not created here, this migration applies cleanly to the
-- existing live database (where the tables already exist) but is NOT
-- sufficient on its own to bootstrap a fresh/empty database or ephemeral CI
-- Postgres instance.
--
-- Not applied to production by this change -- capture only.

create or replace function public.wow_audit_event_probability_card(p_event_prediction_id uuid)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  reasons text[] := '{}';
  v_market_status text;
  v_audit text;
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if r.raw_home_probability is null or r.raw_away_probability is null then reasons:=array_append(reasons,'RAW_PROBABILITY_MISSING'); end if;
  if r.independent_home_probability is null or r.independent_away_probability is null then reasons:=array_append(reasons,'INDEPENDENT_PROBABILITY_MISSING'); end if;
  if r.market_prior_available<>true or r.market_prior_home_probability is null or r.market_prior_away_probability is null or r.market_prior_weight is null then reasons:=array_append(reasons,'MARKET_PRIOR_INCOMPLETE'); end if;
  if r.independent_model_weight is null then reasons:=array_append(reasons,'INDEPENDENT_MODEL_WEIGHT_MISSING'); end if;
  if r.calibrated_home_probability is null or r.calibrated_away_probability is null or r.calibrated_home_lower_bound is null or r.calibrated_away_lower_bound is null or r.calibrated_home_upper_bound is null or r.calibrated_away_upper_bound is null then reasons:=array_append(reasons,'CALIBRATED_RANGE_INCOMPLETE'); end if;
  if r.calibration_method is null or r.calibration_version is null or r.bounds_method_version is null then reasons:=array_append(reasons,'CALIBRATION_PROVENANCE_INCOMPLETE'); end if;
  if r.uncertainty_components is null then reasons:=array_append(reasons,'UNCERTAINTY_COMPONENTS_MISSING'); end if;
  if r.model_version is null or r.model_timestamp is null then reasons:=array_append(reasons,'MODEL_PROVENANCE_INCOMPLETE'); end if;
  if r.source_snapshot_id is null or r.source_snapshot_timestamp is null then reasons:=array_append(reasons,'SOURCE_SNAPSHOT_PROVENANCE_INCOMPLETE'); end if;
  if r.source_coverage_status<>'COMPLETE' then reasons:=array_append(reasons,'SOURCE_COVERAGE_NOT_COMPLETE'); end if;
  if r.source_conflict then reasons:=array_append(reasons,'SOURCE_CONFLICT'); end if;
  if not r.model_valid_after_latest_update or r.probability_invalidated or r.rerun_required then reasons:=array_append(reasons,'STALE_MODEL_INVALIDATED'); end if;
  if r.market_prior_weight>0.50 then v_market_status:='MARKET_DEPENDENT_MODEL'; reasons:=array_append(reasons,'MARKET_DEPENDENT_MODEL');
  elsif r.independent_model_weight<=0 then v_market_status:='FAIL'; reasons:=array_append(reasons,'NO_INDEPENDENT_SUPPORT');
  else v_market_status:='PASS'; end if;
  v_audit:=case when cardinality(reasons)=0 then 'PASS_PROBABILITY_AUDIT' else 'PROBABILITY_AUDIT_FAILURE' end;
  update public.wow_event_predictions set
    market_independence_status=v_market_status,
    probability_audit_result=v_audit,
    rank_eligible=false,
    rank_eligibility_status='NOT_EVALUATED',
    probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('probability_audit_result',v_audit,'market_independence_status',v_market_status,'reasons',to_jsonb(reasons));
end;
$function$
;

create or replace function public.wow_assess_event_calibration_health(p_event_prediction_id uuid)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  c record;
  v_status text;
  reasons text[] := '{}';
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if r.calibration_method is null or r.calibration_version is null or r.calibration_training_n is null then
    v_status:='UNAVAILABLE'; reasons:=array_append(reasons,'CALIBRATION_CARD_INCOMPLETE');
  else
    select calibrator_id,training_n,validation_status,health_status,active,promoted,source_data_hash,split_hash,brier_score,log_loss,calibration_error
      into c from public.wow_calibrators
     where calibration_version=r.calibration_version
       and calibration_method=r.calibration_method
       and coalesce(sport,r.sport)=r.sport
       and coalesce(market_family,r.market_family)=r.market_family
     order by fitted_at desc limit 1;
    if not found then
      v_status:='UNAVAILABLE'; reasons:=array_append(reasons,'EVENT_CALIBRATOR_UNAVAILABLE');
    else
      if not c.active or not c.promoted then reasons:=array_append(reasons,'CALIBRATOR_NOT_ACTIVE_PROMOTED'); end if;
      if c.validation_status<>'PASS' then reasons:=array_append(reasons,'CALIBRATOR_VALIDATION_NOT_PASS'); end if;
      if c.health_status<>'PASS' then reasons:=array_append(reasons,'CALIBRATOR_HEALTH_NOT_PASS'); end if;
      if c.training_n<>r.calibration_training_n then reasons:=array_append(reasons,'CALIBRATION_TRAINING_N_MISMATCH'); end if;
      if c.source_data_hash is null or c.split_hash is null then reasons:=array_append(reasons,'CALIBRATOR_PROVENANCE_INCOMPLETE'); end if;
      v_status:=case when cardinality(reasons)=0 then 'PASS' else 'FAIL' end;
    end if;
  end if;
  update public.wow_event_predictions set
    calibration_health_status=v_status,
    rank_eligible=false,
    rank_eligibility_status='NOT_EVALUATED',
    probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('calibration_health_status',v_status,'reasons',to_jsonb(reasons));
end;
$function$
;

create or replace function public.wow_apply_event_decision_governor(p_event_prediction_id uuid, p_min_lower_bound_gap numeric DEFAULT 0.04)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  v_gap numeric;
  v_selected text;
  v_role text;
  v_decision text;
  v_reason text;
begin
  if p_min_lower_bound_gap<0 or p_min_lower_bound_gap>0.25 then raise exception 'invalid lower-bound gap'; end if;
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if r.probability_audit_result is distinct from 'PASS_PROBABILITY_AUDIT' or r.calibration_health_status<>'PASS' or r.governed_probability_capability<>'AVAILABLE' then
    v_decision:='NO_PICK_UNCALIBRATED'; v_selected:=null; v_role:=null; v_reason:='PROBABILITY_OR_CALIBRATION_NOT_GOVERNED'; v_gap:=null;
  elsif r.market_role_status<>'LOCKED' or r.market_role_consensus_status<>'PASS' then
    v_decision:='NO_PICK_DATA_CONFLICT'; v_selected:=null; v_role:=null; v_reason:='MARKET_ROLE_NOT_LOCKED'; v_gap:=null;
  elsif r.calibrated_home_lower_bound is null or r.calibrated_away_lower_bound is null then
    v_decision:='NO_PICK_UNCALIBRATED'; v_selected:=null; v_role:=null; v_reason:='LOWER_BOUNDS_MISSING'; v_gap:=null;
  else
    v_gap:=abs(r.calibrated_home_lower_bound-r.calibrated_away_lower_bound);
    if v_gap<p_min_lower_bound_gap then
      v_decision:='NO_PICK_CLOSE_GAME'; v_selected:=null; v_role:=null; v_reason:='LOWER_BOUND_GAP_BELOW_MINIMUM';
    elsif r.calibrated_home_lower_bound>r.calibrated_away_lower_bound then
      v_selected:=r.home_team;
      v_role:=case when r.favorite_side='HOME' then 'FAVORITE' else 'UNDERDOG' end;
      v_decision:='SELECTED'; v_reason:='HOME_HIGHER_CALIBRATED_LOWER_BOUND';
    else
      v_selected:=r.away_team;
      v_role:=case when r.favorite_side='AWAY' then 'FAVORITE' else 'UNDERDOG' end;
      v_decision:='SELECTED'; v_reason:='AWAY_HIGHER_CALIBRATED_LOWER_BOUND';
    end if;
  end if;
  update public.wow_event_predictions set
    event_decision=v_decision,
    selected_participant=v_selected,
    selected_market_role=v_role,
    lower_bound_gap=v_gap,
    event_mutex_status='PASS',
    rank_eligible=false,
    rank_eligibility_status='NOT_EVALUATED',
    rank_eligibility_reasons=case when v_selected is null then array[v_decision] else '{}'::text[] end,
    probability_publishable=false
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('event_decision',v_decision,'selected_participant',v_selected,'selected_market_role',v_role,'lower_bound_gap',v_gap,'minimum_required_lower_bound_gap',p_min_lower_bound_gap,'decision_reason',v_reason,'selected_participant_count',case when v_selected is null then 0 else 1 end,'event_mutex_status','PASS','can_execute',false);
end;
$function$
;

create or replace function public.wow_evaluate_event_rank_eligibility(p_event_prediction_id uuid)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  reasons text[] := '{}';
  v_pass boolean;
  v_scoring_count integer;
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if not r.model_ready or r.model_readiness_status<>'PASS' then reasons:=array_append(reasons,'MODEL_NOT_READY'); end if;
  if r.scoring_snapshot_id is null then reasons:=array_append(reasons,'SCORING_EVIDENCE_SNAPSHOT_MISSING');
  else
    select count(*) into v_scoring_count from public.wow_event_scoring_evidence where event_prediction_id=p_event_prediction_id and scoring_snapshot_id=r.scoring_snapshot_id;
    if v_scoring_count<10 then reasons:=array_append(reasons,'SCORING_EVIDENCE_SNAPSHOT_INCOMPLETE'); end if;
  end if;
  if r.probability_audit_result is distinct from 'PASS_PROBABILITY_AUDIT' then reasons:=array_append(reasons,'PROBABILITY_AUDIT_NOT_PASS'); end if;
  if r.raw_home_probability is null or r.raw_away_probability is null or r.independent_home_probability is null or r.independent_away_probability is null then reasons:=array_append(reasons,'MODEL_PROBABILITY_LEDGER_INCOMPLETE'); end if;
  if r.calibrated_home_probability is null or r.calibrated_away_probability is null or r.calibrated_home_lower_bound is null or r.calibrated_away_lower_bound is null or r.calibrated_home_upper_bound is null or r.calibrated_away_upper_bound is null then reasons:=array_append(reasons,'CALIBRATED_BOUNDS_MISSING'); end if;
  if r.calibration_health_status<>'PASS' then reasons:=array_append(reasons,'CALIBRATION_HEALTH_NOT_PASS'); end if;
  if r.governed_probability_capability<>'AVAILABLE' then reasons:=array_append(reasons,'GOVERNED_PROBABILITY_CAPABILITY_UNAVAILABLE'); end if;
  if not r.market_prior_available or r.market_prior_home_probability is null or r.market_prior_away_probability is null or r.market_timestamp is null then reasons:=array_append(reasons,'MARKET_SNAPSHOT_MISSING'); end if;
  if r.market_role_status<>'LOCKED' then reasons:=array_append(reasons,'MARKET_ROLE_NOT_LOCKED'); end if;
  if r.market_role_consensus_status<>'PASS' or coalesce(r.market_role_consensus_book_count,0)<2 then reasons:=array_append(reasons,'MARKET_ROLE_CONSENSUS_FAIL'); end if;
  if r.selected_participant is null or r.selected_market_role not in ('FAVORITE','UNDERDOG') then reasons:=array_append(reasons,'SELECTED_SIDE_ROLE_UNRESOLVED'); end if;
  if r.model_timestamp is null or not r.model_valid_after_latest_update or r.probability_invalidated or r.rerun_required then reasons:=array_append(reasons,'MODEL_STALE_OR_INVALIDATED'); end if;
  v_pass:=cardinality(reasons)=0;
  update public.wow_event_predictions set
    rank_eligibility_status=case when v_pass then 'PASS' else 'FAIL' end,
    rank_eligibility_reasons=reasons,
    rank_eligible=v_pass,
    probability_publishable=case when v_pass then probability_publishable else false end
  where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('rank_eligible',v_pass,'rank_eligibility_status',case when v_pass then 'PASS' else 'FAIL' end,'reasons',to_jsonb(reasons));
end;
$function$
;

create or replace function public.wow_reduce_event_terminal_label(p_event_prediction_id uuid, p_as_of timestamp with time zone DEFAULT now())
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  v_label text;
  reasons text[] := '{}';
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if r.event_status in ('STARTED','FINAL','POSTPONED','CANCELED') or r.event_start_time<=p_as_of then
    v_label:='SLATE_PURGE'; reasons:=array_append(reasons,'EVENT_NOT_ELIGIBLE_PREGAME');
  elsif r.identity_lock_status='FAIL' then
    v_label:='SLATE_PURGE'; reasons:=array_append(reasons,'IDENTITY_LOCK_FAIL');
  elsif r.source_conflict or r.source_coverage_status='SOURCE_CONFLICT' then
    v_label:='REJECT_DATA_QUALITY'; reasons:=array_append(reasons,'SOURCE_CONFLICT');
  elsif r.model_readiness_status='FAIL' or r.source_completeness_status='FAIL' then
    v_label:='DATA_CONTRACT_FAIL'; reasons:=array_cat(reasons,coalesce(r.model_readiness_reasons,'{}'));
  elsif r.governed_probability_capability<>'AVAILABLE' then
    v_label:='MODEL_UNAVAILABLE'; reasons:=array_append(reasons,'GOVERNED_PROBABILITY_CAPABILITY_UNAVAILABLE');
  elsif r.event_decision in ('NO_PICK_CLOSE_GAME','NO_PICK_DATA_CONFLICT','NO_PICK_STATUS_UNRESOLVED') then
    v_label:='NO_PLAY'; reasons:=array_append(reasons,r.event_decision);
  elsif r.model_ready=false then
    v_label:='RESEARCH_INTEREST'; reasons:=array_append(reasons,'MODEL_NOT_READY');
  elsif r.probability_audit_result is distinct from 'PASS_PROBABILITY_AUDIT' or r.calibration_health_status<>'PASS' then
    v_label:='MODEL_QUALIFIED_HOLD'; reasons:=array_append(reasons,'PROBABILITY_OR_CALIBRATION_GATE_NOT_PASS');
  elsif r.rank_eligible=false then
    v_label:='MODEL_QUALIFIED_HOLD'; reasons:=array_cat(reasons,coalesce(r.rank_eligibility_reasons,'{}'));
  elsif r.final_refresh_status<>'PASS' then
    v_label:='MODEL_QUALIFIED_HOLD'; reasons:=array_cat(reasons,coalesce(r.final_refresh_reasons,'{}'));
  elsif r.probability_publishable=true then
    v_label:='FINAL_APPROVED';
  else
    v_label:='MODEL_QUALIFIED_HOLD'; reasons:=array_append(reasons,'PUBLICATION_GATE_NOT_PASS');
  end if;
  update public.wow_event_predictions set terminal_label=v_label,terminal_ceiling=v_label,terminal_reasons=reasons,terminal_reduced_at=p_as_of where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('terminal_label',v_label,'terminal_ceiling',v_label,'terminal_reasons',to_jsonb(reasons),'can_execute',false);
end;
$function$
;

create or replace function public.wow_run_event_final_refresh(p_event_prediction_id uuid, p_refresh_snapshot_id uuid, p_as_of timestamp with time zone DEFAULT now(), p_market_max_age_seconds integer DEFAULT 600)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
  k text;
  e record;
  s record;
  reasons text[] := '{}';
  v_event_fresh boolean := true;
  v_critical_fresh boolean := true;
  v_settlement_fresh boolean := true;
  v_market_fresh boolean := true;
  v_material_change boolean := false;
  v_status text;
  v_age integer;
  v_event_age integer;
  v_critical_max_age integer := 0;
  v_settlement_age integer;
  v_market_age integer;
  v_evidence_kinds text[] := array['EVENT_STATUS','HOME_STARTER','AWAY_STARTER','HOME_LINEUP','AWAY_LINEUP','BULLPEN_STATUS','WEATHER_STATUS','INJURY_STATUS','SETTLEMENT'];
begin
  if p_market_max_age_seconds<0 then raise exception 'invalid market max age'; end if;
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;

  if r.model_timestamp is not null and r.scoring_snapshot_id is null then
    reasons:=array_append(reasons,'SCORING_EVIDENCE_SNAPSHOT_MISSING');
    v_material_change:=true;
  end if;

  foreach k in array v_evidence_kinds loop
    select evidence_status,source_grade,evidence_timestamp,freshness_ttl_seconds,source_name,source_ref,payload_hash,retrieved_at
      into e from public.wow_event_evidence
     where event_prediction_id=p_event_prediction_id and evidence_kind=k
     order by retrieved_at desc,evidence_timestamp desc limit 1;
    if not found then
      reasons:=array_append(reasons,k||'_NOT_CALLED');
      if k='EVENT_STATUS' then v_event_fresh:=false;
      elsif k='SETTLEMENT' then v_settlement_fresh:=false;
      else v_critical_fresh:=false; end if;
    else
      v_age:=greatest(0,extract(epoch from (p_as_of-e.evidence_timestamp))::integer);
      if k='EVENT_STATUS' then v_event_age:=v_age;
      elsif k='SETTLEMENT' then v_settlement_age:=v_age;
      else v_critical_max_age:=greatest(v_critical_max_age,v_age); end if;
      if e.evidence_status not in ('RETRIEVED','NOT_APPLICABLE') or e.source_grade='PROXY' or e.payload_hash is null or v_age>e.freshness_ttl_seconds then
        reasons:=array_append(reasons,k||'_NOT_FRESH');
        if k='EVENT_STATUS' then v_event_fresh:=false;
        elsif k='SETTLEMENT' then v_settlement_fresh:=false;
        else v_critical_fresh:=false; end if;
      end if;

      if r.model_timestamp is not null and r.scoring_snapshot_id is not null then
        select payload_hash into s
          from public.wow_event_scoring_evidence
         where event_prediction_id=p_event_prediction_id
           and scoring_snapshot_id=r.scoring_snapshot_id
           and evidence_kind=k
         limit 1;
        if not found then
          reasons:=array_append(reasons,k||'_SCORING_HASH_MISSING');
          v_material_change:=true;
        elsif e.payload_hash is distinct from s.payload_hash then
          v_material_change:=true;
          reasons:=array_append(reasons,k||'_MATERIAL_CHANGE_AFTER_MODEL');
        end if;
      end if;
    end if;
  end loop;

  if r.market_timestamp is null then
    v_market_fresh:=false; reasons:=array_append(reasons,'MARKET_NOT_CALLED');
  else
    v_market_age:=greatest(0,extract(epoch from (p_as_of-r.market_timestamp))::integer);
    if v_market_age>p_market_max_age_seconds or r.market_role_status<>'LOCKED' or r.market_role_consensus_status<>'PASS' or coalesce(r.market_role_consensus_book_count,0)<2 then
      v_market_fresh:=false; reasons:=array_append(reasons,'MARKET_NOT_FRESH_OR_LOCKED');
    end if;
  end if;

  if r.event_status<>'SCHEDULED' then reasons:=array_append(reasons,'EVENT_NOT_SCHEDULED'); v_event_fresh:=false; end if;
  if r.source_conflict then reasons:=array_append(reasons,'SOURCE_CONFLICT'); v_critical_fresh:=false; end if;

  if v_material_change then v_status:='RERUN_REQUIRED';
  elsif cardinality(reasons)>0 then v_status:='FAIL';
  else v_status:='PASS'; end if;

  update public.wow_event_predictions set
    final_refresh_snapshot_id=p_refresh_snapshot_id,
    final_refresh_timestamp=p_as_of,
    final_refresh_status=v_status,
    final_refresh_reasons=reasons,
    event_status_fresh_at_refresh=v_event_fresh,
    critical_status_fresh_at_refresh=v_critical_fresh,
    market_fresh_at_refresh=v_market_fresh,
    settlement_fresh_at_refresh=v_settlement_fresh,
    event_status_age_seconds_at_refresh=v_event_age,
    critical_status_age_seconds_at_refresh=v_critical_max_age,
    market_age_seconds_at_refresh=v_market_age,
    settlement_age_seconds_at_refresh=v_settlement_age,
    market_ttl_seconds=p_market_max_age_seconds,
    probability_invalidated=case when v_material_change then true else probability_invalidated end,
    rerun_required=case when v_material_change then true else rerun_required end,
    model_ready=case when v_material_change then false else model_ready end,
    model_readiness_status=case when v_material_change then 'FAIL' else model_readiness_status end,
    rank_eligible=case when v_status='PASS' then rank_eligible else false end,
    rank_eligibility_status=case when v_status='PASS' then rank_eligibility_status else 'FAIL' end,
    probability_publishable=case when v_status='PASS' then probability_publishable else false end
  where event_prediction_id=p_event_prediction_id;

  return jsonb_build_object(
    'final_refresh_status',v_status,
    'reasons',to_jsonb(reasons),
    'event_status_fresh',v_event_fresh,
    'critical_status_fresh',v_critical_fresh,
    'market_fresh',v_market_fresh,
    'settlement_fresh',v_settlement_fresh,
    'material_change_after_model',v_material_change,
    'probability_invalidated',case when v_material_change then true else r.probability_invalidated end,
    'rerun_required',case when v_material_change then true else r.rerun_required end,
    'market_max_age_seconds',p_market_max_age_seconds
  );
end;
$function$
;

create or replace function public.wow_mark_event_publishable(p_event_prediction_id uuid)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  r public.wow_event_predictions%rowtype;
begin
  select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
  if not found then raise exception 'event prediction not found'; end if;
  if not r.rank_eligible then return jsonb_build_object('probability_publishable',false,'status','BLOCKED','reason','RANK_ELIGIBILITY_NOT_PASS','can_execute',false); end if;
  if r.final_refresh_status<>'PASS' then return jsonb_build_object('probability_publishable',false,'status','BLOCKED','reason','FINAL_REFRESH_NOT_PASS','can_execute',false); end if;
  if r.governed_probability_capability<>'AVAILABLE' or r.calibration_health_status<>'PASS' then return jsonb_build_object('probability_publishable',false,'status','BLOCKED','reason','GOVERNANCE_OR_CALIBRATION_NOT_PASS','can_execute',false); end if;
  update public.wow_event_predictions set probability_publishable=true where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('probability_publishable',true,'status','PASS','can_execute',false);
end;
$function$
;

create or replace function public.wow_run_event_postmodel_gates(p_event_prediction_id uuid, p_min_lower_bound_gap numeric DEFAULT 0.04)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  v_audit jsonb;
  v_cal jsonb;
  v_decision jsonb;
  v_rank jsonb;
  v_terminal jsonb;
begin
  v_audit:=public.wow_audit_event_probability_card(p_event_prediction_id);
  v_cal:=public.wow_assess_event_calibration_health(p_event_prediction_id);
  v_decision:=public.wow_apply_event_decision_governor(p_event_prediction_id,p_min_lower_bound_gap);
  v_rank:=public.wow_evaluate_event_rank_eligibility(p_event_prediction_id);
  v_terminal:=public.wow_reduce_event_terminal_label(p_event_prediction_id,now());
  return jsonb_build_object('probability_audit',v_audit,'calibration_health',v_cal,'event_decision',v_decision,'rank_eligibility',v_rank,'terminal',v_terminal,'can_execute',false);
end;
$function$
;

create or replace function public.wow_run_event_final_gates(p_event_prediction_id uuid, p_refresh_snapshot_id uuid, p_as_of timestamp with time zone DEFAULT now(), p_market_max_age_seconds integer DEFAULT 600)
 returns jsonb
 language plpgsql
 set search_path to ''
as $function$
declare
  v_refresh jsonb;
  v_publish jsonb;
  v_terminal jsonb;
begin
  v_refresh:=public.wow_run_event_final_refresh(p_event_prediction_id,p_refresh_snapshot_id,p_as_of,p_market_max_age_seconds);
  v_publish:=public.wow_mark_event_publishable(p_event_prediction_id);
  v_terminal:=public.wow_reduce_event_terminal_label(p_event_prediction_id,p_as_of);
  return jsonb_build_object('final_refresh',v_refresh,'publication',v_publish,'terminal',v_terminal,'can_execute',false);
end;
$function$
;

revoke all on function public.wow_audit_event_probability_card(uuid) from anon, authenticated;
revoke all on function public.wow_assess_event_calibration_health(uuid) from anon, authenticated;
revoke all on function public.wow_apply_event_decision_governor(uuid, numeric) from anon, authenticated;
revoke all on function public.wow_evaluate_event_rank_eligibility(uuid) from anon, authenticated;
revoke all on function public.wow_reduce_event_terminal_label(uuid, timestamptz) from anon, authenticated;
revoke all on function public.wow_run_event_final_refresh(uuid, uuid, timestamptz, integer) from anon, authenticated;
revoke all on function public.wow_mark_event_publishable(uuid) from anon, authenticated;
revoke all on function public.wow_run_event_postmodel_gates(uuid, numeric) from anon, authenticated;
revoke all on function public.wow_run_event_final_gates(uuid, uuid, timestamptz, integer) from anon, authenticated;
