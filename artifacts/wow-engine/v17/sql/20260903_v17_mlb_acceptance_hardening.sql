-- WOW V17 MLB live-acceptance hardening.
-- Persists defects discovered during the 2026-09-03 real pregame acceptance:
-- 1) MLB Warmup is still pregame when codedGameState=P and start time is future.
-- 2) A healthy periodic calibration reassessment must not invalidate a ratified,
--    unchanged certified spec merely because assessed_at advanced.
-- 3) probability-only rank promotion uses the certified MLB V2D calibrator/artifact,
--    not the generic event calibrator table and not sportsbook consensus.
-- 4) per-event immutable source manifests must be rebuilt after the legacy bridge
--    rewrites the slate-wide source snapshot id.
-- 5) repeat refreshes cannot mutate frozen scoring-evidence hashes.
-- can_execute remains false throughout.

-- Warmup normalization in shared environmental evidence.
do $do$
declare d text;
begin
  select pg_get_functiondef('public.wow_v17_hydrate_shared_environmental_evidence(uuid,uuid)'::regprocedure) into d;
  d:=replace(
    d,
    $q$if coalesce(v_status->>'abstractGameState','') not in ('Preview','Pre-Game')
     and coalesce(v_status->>'detailedState','') not in ('Scheduled','Pre-Game') then$q$,
    $q$if coalesce(v_status->>'codedGameState','') <> 'P'
     and coalesce(v_status->>'detailedState','') not in ('Scheduled','Pre-Game','Warmup') then$q$
  );
  execute d;
end;$do$;

-- Ratification remains valid while the same spec is ratified and CURRENT health
-- is PASS. A later FAIL still blocks immediately; timestamp churn alone does not.
create or replace function public.wow_governed_deployment_state()
returns jsonb language sql stable set search_path to '' as $function$
with gate_summary as (
  select count(*) gate_count,count(*) filter(where status='PASS') pass_count,
         coalesce(jsonb_agg(jsonb_build_object('id',gate_id,'status',status,'reason',reason) order by gate_id),'[]'::jsonb) deployment_gates
  from public.wow_governed_deployment_gates
), latest_health as (
  select spec_id,assessed_at,calibration_health_status from public.wow_mlb_v2d_calibration_health order by assessed_at desc limit 1
), active_spec as (
  select fs.spec_id,fs.production_feature_ready legacy_frozen_spec_production_feature_ready
  from public.wow_mlb_v2d_frozen_spec fs where fs.status='RESEARCH_FROZEN' and fs.spec_id=(select spec_id from latest_health) limit 1
), runtime_capability as (
  select capability_status,updated_at,evidence from public.wow_runtime_capabilities where capability_key='MLB_EVENT_PROBABILITY' limit 1
), latest_ratification as (
  select r.* from public.wow_mlb_publication_ratification r where r.spec_id=(select spec_id from active_spec) order by r.created_at desc,r.ratification_id desc limit 1
), state as (
  select s.spec_id active_spec_id,g.deployment_gates,(g.gate_count=11 and g.pass_count=11) deployment_contract_pass,
         coalesce(h.calibration_health_status,'UNAVAILABLE') calibration_health_status,h.assessed_at calibration_health_assessed_at,
         coalesce(rc.capability_status,'UNAVAILABLE') runtime_capability_status,rc.updated_at runtime_capability_updated_at,
         coalesce(lr.decision,'NOT_RATIFIED') ratification_status,lr.ratification_id,lr.created_at ratification_created_at,
         coalesce(lr.production_feature_ready,false) production_feature_ready,coalesce(lr.probability_publishable,false) ratification_probability_publishable,
         coalesce(s.legacy_frozen_spec_production_feature_ready,false) legacy_frozen_spec_production_feature_ready,
         (s.spec_id is not null and g.gate_count=11 and g.pass_count=11
          and coalesce(h.calibration_health_status,'UNAVAILABLE')='PASS'
          and coalesce(rc.capability_status,'UNAVAILABLE')='AVAILABLE'
          and coalesce(lr.decision,'NOT_RATIFIED')='RATIFIED'
          and coalesce(lr.production_feature_ready,false)
          and coalesce(lr.probability_publishable,false)) publishable
  from gate_summary g left join latest_health h on true left join active_spec s on true left join runtime_capability rc on true left join latest_ratification lr on true
)
select jsonb_build_object(
 'active_spec_id',active_spec_id,'governed_probability_capability',case when publishable then 'AVAILABLE' else 'UNAVAILABLE' end,
 'governed_probability_status',case when publishable then 'READY_FOR_PRODUCTION_GATE_REVIEW' else 'NOT_PRODUCED' end,
 'deployment_contract_status',case when deployment_contract_pass then 'PASS' else 'FAIL' end,'deployment_gates',deployment_gates,
 'calibration_health_status',calibration_health_status,'calibration_health_assessed_at',calibration_health_assessed_at,
 'runtime_capability_status',runtime_capability_status,'runtime_capability_updated_at',runtime_capability_updated_at,
 'ratification_status',ratification_status,'ratification_id',ratification_id,'ratification_created_at',ratification_created_at,
 'production_feature_ready',production_feature_ready,'legacy_frozen_spec_production_feature_ready',legacy_frozen_spec_production_feature_ready,
 'probability_publishable',publishable,'can_execute',false
) from state;
$function$;

create or replace function public.wow_v17_refresh_event_source_manifest(p_event_prediction_id uuid)
returns jsonb language plpgsql set search_path to '' as $function$
declare r public.wow_event_predictions%rowtype; v_components jsonb; v_count integer; v_id uuid:=gen_random_uuid(); v_now timestamptz:=clock_timestamp(); v_hash text;
begin
 select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
 if not found then raise exception 'event prediction not found'; end if;
 with latest as (
  select distinct on(evidence_kind) evidence_kind,evidence_id,source_name,source_grade,evidence_status,evidence_timestamp,retrieved_at,payload_hash
  from public.wow_event_evidence where event_prediction_id=p_event_prediction_id order by evidence_kind,retrieved_at desc,evidence_timestamp desc
 ) select count(*),coalesce(jsonb_agg(jsonb_build_object('kind',evidence_kind,'evidence_id',evidence_id,'source_name',source_name,'source_grade',source_grade,'status',evidence_status,'evidence_timestamp',evidence_timestamp,'payload_hash',payload_hash) order by evidence_kind),'[]'::jsonb)
 into v_count,v_components from latest;
 if v_count<10 or r.source_completeness_status<>'PASS' then
  return jsonb_build_object('status','HOLD','code','SOURCE_MANIFEST_COMPONENTS_INCOMPLETE','component_count',v_count,'can_execute',false);
 end if;
 v_components:=jsonb_build_object('schema_version','WOW_V17_EVENT_SOURCE_MANIFEST_V1','event_prediction_id',p_event_prediction_id,'official_event_id',r.official_event_id,'scoring_snapshot_id',r.scoring_snapshot_id,'model_source_snapshot_id',r.source_snapshot_id,'evidence',v_components,'can_execute',false);
 v_hash:=encode(extensions.digest(convert_to(v_components::text,'UTF8'),'sha256'),'hex');
 insert into public.wow_event_source_snapshots(source_snapshot_id,event_prediction_id,captured_at,coverage_status,latest_material_update_timestamp,components,manifest_hash,can_execute)
 values(v_id,p_event_prediction_id,v_now,'COMPLETE',r.latest_material_update_timestamp,v_components,v_hash,false);
 update public.wow_event_predictions set source_snapshot_id=v_id,source_snapshot_timestamp=v_now where event_prediction_id=p_event_prediction_id;
 return jsonb_build_object('status','PASS','source_snapshot_id',v_id,'captured_at',v_now,'coverage_status','COMPLETE','manifest_hash',v_hash,'component_count',v_count,'can_execute',false);
end;$function$;

create or replace function public.wow_v17_reconcile_probability_only_snapshot(p_event_prediction_id uuid)
returns jsonb language plpgsql set search_path to '' as $function$
declare r public.wow_event_predictions%rowtype; v_scoring_n integer; v_mismatch_n integer;
begin
 select * into r from public.wow_event_predictions where event_prediction_id=p_event_prediction_id for update;
 if not found then raise exception 'event prediction not found'; end if;
 select count(*) into v_scoring_n from public.wow_event_scoring_evidence where event_prediction_id=p_event_prediction_id and scoring_snapshot_id=r.scoring_snapshot_id;
 with latest as (
  select distinct on(ee.evidence_kind) ee.evidence_kind,ee.payload_hash from public.wow_event_evidence ee
  where ee.event_prediction_id=p_event_prediction_id order by ee.evidence_kind,ee.retrieved_at desc,ee.evidence_timestamp desc
 ) select count(*) into v_mismatch_n from latest left join public.wow_event_scoring_evidence scored
   on scored.event_prediction_id=p_event_prediction_id and scored.scoring_snapshot_id=r.scoring_snapshot_id and scored.evidence_kind=latest.evidence_kind
 where scored.payload_hash is distinct from latest.payload_hash;
 if v_scoring_n>=10 and v_mismatch_n=0 and r.model_timestamp is not null and r.latest_material_update_timestamp is not null and r.model_timestamp>=r.latest_material_update_timestamp then
  update public.wow_event_predictions set probability_invalidated=false,rerun_required=false where event_prediction_id=p_event_prediction_id;
  return jsonb_build_object('status','PASS','scoring_evidence_count',v_scoring_n,'mismatch_count',0,'can_execute',false);
 end if;
 return jsonb_build_object('status','HOLD','scoring_evidence_count',v_scoring_n,'mismatch_count',v_mismatch_n,'can_execute',false);
end;$function$;

-- Make repeat hydration deterministic and frozen: local variables resolve
-- explicitly; semantic hashes exclude retrieval clock; frozen scoring rows never update.
do $do$
declare d text;
begin
 select pg_get_functiondef('public.wow_v17_hydrate_mlb_event_governance_evidence(uuid,uuid,jsonb,text)'::regprocedure) into d;
 if strpos(d,'#variable_conflict use_variable')=0 then d:=replace(d,'AS $function$'||chr(10)||'DECLARE','AS $function$'||chr(10)||'#variable_conflict use_variable'||chr(10)||'DECLARE'); end if;
 d:=replace(d,$q$coalesce(blockers, '{}'::text[])$q$,$q$coalesce(wow_event_predictions.blockers, '{}'::text[])$q$);
 d:=replace(d,$q$coalesce(blockers,'{}'::text[])$q$,$q$coalesce(wow_event_predictions.blockers,'{}'::text[])$q$);
 d:=replace(d,$q$,
      'retrieved_at', fetched_at$q$,$q$$q$);
 d:=replace(d,$q$,
      'retrieved_at',fetched_at$q$,$q$$q$);
 d:=regexp_replace(d,$q$ON CONFLICT \(event_prediction_id, scoring_snapshot_id, evidence_kind\)[\s\S]*?can_execute = false;$q$,'ON CONFLICT (event_prediction_id, scoring_snapshot_id, evidence_kind) DO NOTHING;','i');
 d:=regexp_replace(d,$q$on conflict\(event_prediction_id,scoring_snapshot_id,evidence_kind\)[\s\S]*?can_execute=false;$q$,'on conflict(event_prediction_id,scoring_snapshot_id,evidence_kind) do nothing;','i');
 execute d;
end;$do$;

-- Probability-only bridge must rebuild per-event manifest AFTER legacy replay and
-- clear stale flags only when all frozen semantic hashes still match.
do $do$
declare d text;
begin
 select pg_get_functiondef('public.wow_v17_mlb_probability_only_governance_bridge(uuid,text,text,text,text,text)'::regprocedure) into d;
 if strpos(d,'wow_v17_reconcile_probability_only_snapshot')=0 then
  d:=replace(d,'identity:=public.wow_evaluate_event_identity_lock(event_id,now());','perform public.wow_v17_reconcile_probability_only_snapshot(event_id); identity:=public.wow_evaluate_event_identity_lock(event_id,now());');
 end if;
 if strpos(d,'wow_v17_refresh_event_source_manifest(event_id)')=0 then
  d:=replace(d,'ready:=public.wow_evaluate_event_model_readiness(event_id);','ready:=public.wow_evaluate_event_model_readiness(event_id); perform public.wow_v17_refresh_event_source_manifest(event_id);');
 end if;
 execute d;
end;$do$;

-- Promotion trigger now honors V17's probability-vs-market separation.
create or replace function public.wow_guard_event_promotion()
returns trigger language plpgsql set search_path to '' as $function$
declare v_cap text; v_specialist text; v_snapshot_ok boolean; v_calibrator_ok boolean; v_required_evidence integer; v_good_evidence integer; v_books integer; v_home numeric; v_away numeric; v_home_fav integer; v_away_fav integer; v_market_max_age integer; v_probability_only boolean;
begin
 v_probability_only:=coalesce(new.decision_intent,'') in ('WINNER','BEST_SIDE');
 if new.rank_eligible=true then
  select capability_status into v_cap from public.wow_runtime_capabilities where capability_key='MLB_EVENT_PROBABILITY'; if v_cap is distinct from 'AVAILABLE' then raise exception 'rank promotion blocked: runtime capability unavailable'; end if;
  select controlling_specialist into v_specialist from public.wow_specialist_registry where sport=new.sport and market_family=new.market_family and active=true; if v_specialist is null or v_specialist<>new.controlling_specialist then raise exception 'rank promotion blocked: specialist registry mismatch'; end if;
  select exists(select 1 from public.wow_event_source_snapshots s where s.source_snapshot_id=new.source_snapshot_id and s.event_prediction_id=new.event_prediction_id and s.coverage_status='COMPLETE' and s.captured_at=new.source_snapshot_timestamp and s.manifest_hash is not null) into v_snapshot_ok; if not v_snapshot_ok then raise exception 'rank promotion blocked: source snapshot manifest missing/incomplete'; end if;
  if v_probability_only then
   select exists(select 1 from public.wow_mlb_forward_score_snapshots ss join public.wow_mlb_v2d_intercept_calibration c on c.calibration_id=ss.calibration_id join public.wow_mlb_v2d_calibration_health h on h.spec_id=ss.spec_id and h.calibration_health_status='PASS' join public.wow_mlb_event_fitted_model_artifacts a on a.active=true and a.promoted=true and a.artifact_payload->>'baseline_spec_id'=ss.spec_id::text and a.specialist_calibration_identity->>'calibration_id'=ss.calibration_id::text where ss.score_snapshot_id=new.scoring_snapshot_id and c.method=new.calibration_method and c.calibration_id::text=new.calibration_version and c.prior_games=new.calibration_training_n) into v_calibrator_ok;
  else
   select exists(select 1 from public.wow_calibrators c where c.calibration_version=new.calibration_version and c.calibration_method=new.calibration_method and coalesce(c.sport,new.sport)=new.sport and coalesce(c.market_family,new.market_family)=new.market_family and c.training_n=new.calibration_training_n and c.active=true and c.promoted=true and c.validation_status='PASS' and c.health_status='PASS' and c.source_data_hash is not null and c.split_hash is not null) into v_calibrator_ok;
  end if;
  if not v_calibrator_ok then raise exception 'rank promotion blocked: certified calibrator missing'; end if;
  with req(kind) as (values ('OFFICIAL_EVENT_ID'),('EVENT_STATUS'),('HOME_STARTER'),('AWAY_STARTER'),('HOME_LINEUP'),('AWAY_LINEUP'),('BULLPEN_STATUS'),('WEATHER_STATUS'),('INJURY_STATUS'),('SETTLEMENT')),latest as (select distinct on(e.evidence_kind) e.evidence_kind,e.source_name,e.source_grade,e.evidence_status,e.evidence_timestamp,e.freshness_ttl_seconds,e.retrieved_at from public.wow_event_evidence e join req on req.kind=e.evidence_kind where e.event_prediction_id=new.event_prediction_id order by e.evidence_kind,e.retrieved_at desc,e.evidence_timestamp desc)
  select 10,count(*) filter(where l.evidence_status in ('RETRIEVED','NOT_APPLICABLE') and l.source_grade<>'PROXY' and (l.evidence_kind<>'OFFICIAL_EVENT_ID' or l.source_grade='OFFICIAL') and clock_timestamp()<=l.evidence_timestamp+make_interval(secs=>l.freshness_ttl_seconds) and exists(select 1 from public.wow_event_source_attempts a where a.event_prediction_id=new.event_prediction_id and a.evidence_kind=l.evidence_kind and a.provider=l.source_name and a.attempt_status='SUCCESS' and a.attempted_at<=l.retrieved_at))::int into v_required_evidence,v_good_evidence from latest l;
  if v_good_evidence<>v_required_evidence then raise exception 'rank promotion blocked: fresh source evidence incomplete'; end if;
  if not v_probability_only then
   v_market_max_age:=coalesce(new.market_ttl_seconds,600);
   with latest_per_book as (select distinct on(sportsbook) sportsbook,no_vig_home_probability,no_vig_away_probability,captured_at from public.wow_event_market_snapshots where event_prediction_id=new.event_prediction_id and valid=true and settlement_basis=new.settlement_basis and captured_at<=clock_timestamp() and captured_at>=clock_timestamp()-make_interval(secs=>v_market_max_age) order by sportsbook,captured_at desc)
   select count(*)::int,avg(no_vig_home_probability),avg(no_vig_away_probability),count(*) filter(where no_vig_home_probability>0.5+new.market_role_even_band)::int,count(*) filter(where no_vig_away_probability>0.5+new.market_role_even_band)::int into v_books,v_home,v_away,v_home_fav,v_away_fav from latest_per_book;
   if coalesce(v_books,0)<2 then raise exception 'rank promotion blocked: insufficient fresh books'; end if; if v_home_fav>0 and v_away_fav>0 then raise exception 'rank promotion blocked: favorite status conflict'; end if; if abs(v_home-0.5)<=new.market_role_even_band then raise exception 'rank promotion blocked: even/micro-dog market role'; end if; if abs(v_home-new.market_prior_home_probability)>0.000001 or abs(v_away-new.market_prior_away_probability)>0.000001 then raise exception 'rank promotion blocked: stored market prior differs from fresh book consensus'; end if;
  end if;
 end if;
 if new.probability_publishable=true then
  if new.rank_eligible<>true then raise exception 'publication blocked: row not rank eligible'; end if; if new.final_refresh_status<>'PASS' or new.final_refresh_snapshot_id is null or new.final_refresh_timestamp is null then raise exception 'publication blocked: final refresh missing'; end if; if new.probability_invalidated or new.rerun_required then raise exception 'publication blocked: model invalidated/rerun required'; end if;
  if new.event_status_fresh_at_refresh<>true or new.critical_status_fresh_at_refresh<>true or new.settlement_fresh_at_refresh<>true or (not v_probability_only and new.market_fresh_at_refresh<>true) then raise exception 'publication blocked: final refresh freshness not pass'; end if;
 end if;
 return new;
end;$function$;
