-- WOW V17 MLB forward-shadow grade selected-side probability repair
-- Class B data-contract repair, 2026-09-21.
-- Future writes use selected-side probability; historical immutable grades are never rewritten.
begin;
create or replace function public.wow_mlb_forward_grade_shadow_event(p_shadow_event_id uuid,p_official_winner text,p_home_score integer,p_away_score integer,p_settlement_source text default null) returns jsonb
language plpgsql set search_path = '' as $function$
declare e public.wow_mlb_forward_shadow_events%rowtype; s public.wow_mlb_forward_score_snapshots%rowtype; v_home_win boolean; v_predicted_side text; v_prediction_probability double precision; v_actual_outcome boolean; v_brier double precision; v_logloss double precision; v_grade_id uuid;
begin
  if p_official_winner not in ('HOME','AWAY') then raise exception 'official_winner must be HOME or AWAY'; end if;
  if p_home_score is null or p_away_score is null or p_home_score < 0 or p_away_score < 0 then raise exception 'scores must be non-negative'; end if;
  select * into e from public.wow_mlb_forward_shadow_events where shadow_event_id=p_shadow_event_id;
  if not found then raise exception 'shadow event not found'; end if;
  if now() < e.event_start_time then return jsonb_build_object('status','BLOCKED','reason','EVENT_NOT_YET_STARTED','can_execute',false); end if;
  select * into s from public.wow_mlb_forward_score_snapshots where shadow_event_id=p_shadow_event_id order by created_at asc limit 1;
  if not found then return jsonb_build_object('status','BLOCKED','reason','NO_FROZEN_PREDICTION_TO_GRADE','can_execute',false); end if;
  if exists (select 1 from public.wow_mlb_forward_shadow_grades where score_snapshot_id=s.score_snapshot_id) then return jsonb_build_object('status','BLOCKED','reason','ALREADY_GRADED','can_execute',false); end if;
  v_home_win := (p_official_winner='HOME');
  v_predicted_side := case when s.calibrated_home_probability >= s.calibrated_away_probability then 'HOME' else 'AWAY' end;
  v_prediction_probability := case
    when v_predicted_side = 'HOME' then s.calibrated_home_probability
    else s.calibrated_away_probability
  end;
  v_actual_outcome := (v_predicted_side=p_official_winner);
  v_brier := power((case when v_actual_outcome then 1.0 else 0.0 end)-v_prediction_probability,2);
  if v_prediction_probability > 0 and v_prediction_probability < 1 then v_logloss := -((case when v_actual_outcome then 1.0 else 0.0 end)*ln(v_prediction_probability)+(case when v_actual_outcome then 0.0 else 1.0 end)*ln(1-v_prediction_probability)); else v_logloss := null; end if;
  insert into public.wow_mlb_forward_shadow_grades (shadow_event_id,score_snapshot_id,spec_id,prediction_probability,predicted_side,official_winner,actual_outcome,home_score,away_score,brier_contribution,log_loss_contribution,prediction_timestamp,outcome_timestamp,model_spec_id,settlement_source) values (
    p_shadow_event_id,s.score_snapshot_id,s.spec_id,
    v_prediction_probability,
    v_predicted_side,p_official_winner,v_actual_outcome,p_home_score,p_away_score,v_brier,v_logloss,s.model_timestamp,now(),s.spec_id,p_settlement_source) returning grade_id into v_grade_id;
  update public.wow_mlb_forward_shadow_events set result_status='GRADED',home_win=v_home_win where shadow_event_id=p_shadow_event_id;
  return jsonb_build_object('status','GRADED','grade_id',v_grade_id,'predicted_side',v_predicted_side,'prediction_probability',v_prediction_probability,'actual_outcome',v_actual_outcome,'brier_contribution',v_brier,'log_loss_contribution',v_logloss,'can_execute',false);
end;$function$;
create or replace view public.wow_mlb_forward_shadow_grades_selected_side_v as
select g.*,case when g.predicted_side='HOME' then s.calibrated_home_probability when g.predicted_side='AWAY' then s.calibrated_away_probability end as selected_side_probability,case when g.predicted_side='HOME' then s.home_lower_bound when g.predicted_side='AWAY' then s.away_lower_bound end as selected_side_lower_bound,abs(g.prediction_probability-case when g.predicted_side='HOME' then s.calibrated_home_probability when g.predicted_side='AWAY' then s.calibrated_away_probability end)<=0.000000000001 as stored_prediction_probability_matches_side from public.wow_mlb_forward_shadow_grades g join public.wow_mlb_forward_score_snapshots s on s.score_snapshot_id=g.score_snapshot_id;
comment on view public.wow_mlb_forward_shadow_grades_selected_side_v is 'Side-consistent read interface for immutable MLB forward-shadow grades. selected_side_probability/lower_bound are derived from the frozen score snapshot; historical grade rows are never rewritten.';
commit;
