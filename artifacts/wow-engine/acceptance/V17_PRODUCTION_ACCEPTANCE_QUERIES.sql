-- V17 production acceptance evidence queries (#1935 / #2257). READ-ONLY: SELECT only. No DDL/DML, no function calls with side effects.
-- Run against the production binding Supabase project; capture results into V17_PRODUCTION_ACCEPTANCE_EVIDENCE_V1.
-- can_execute=false; non-authorizing evidence.

-- Q1. Registry flag is constrained false by design (expect: CHECK ((probability_publishable = false))).
select conname, pg_get_constraintdef(oid) from pg_constraint
 where conrelid='public.wow_nfl_event_fitted_model_artifacts'::regclass and conname like '%publi%';

-- Q2. NFL moneyline registry by lifecycle (expect exactly one active+promoted CHAMPION).
select lifecycle_state, active, promoted, probability_publishable, count(*) from public.wow_nfl_event_fitted_model_artifacts group by 1,2,3,4;

-- Q3. #2259 reproduction: rows reachable by the proposed Scout join vs the reducer-aligned champion gate.
select count(*) as rows_matching_2259_join
  from public.wow_nfl_event_predictions p
  join public.wow_nfl_event_fitted_model_artifacts a on a.artifact_id=p.model_artifact_id and a.probability_publishable=true and a.can_execute=false
 where p.model_probability_publishable=true and p.can_execute=false;                                   -- observed 0
select count(*) as rows_matching_champion_gate
  from public.wow_nfl_event_predictions p
  join public.wow_nfl_event_fitted_model_artifacts a on a.artifact_id=p.model_artifact_id and a.active and a.promoted and a.lifecycle_state='CHAMPION' and a.can_execute=false
 where p.model_probability_publishable=true and p.can_execute=false;                                   -- observed 1261 of 1263
select model_probability_publishable, count(*) from public.wow_nfl_event_predictions group by 1;

-- Q4. Calibrators (only NFL OUTRIGHT_WINNER exists; props/NBA/NCAAF have none).
select sport, market_family, active, promoted, validation_status, health_status, count(*), max(training_n), min(brier_score)
  from public.wow_calibrators group by 1,2,3,4,5,6;

-- Q5. NCAAF (observed: all zero).
select 'fitted' t, count(*) from public.wow_ncaaf_fitted_model_artifacts
union all select 'calibrator_artifacts', count(*) from public.wow_ncaaf_calibrator_artifacts
union all select 'predictions', count(*) from public.wow_ncaaf_predictions
union all select 'calibration_ledger', count(*) from public.wow_ncaaf_calibration_ledger;

-- Q6. Prop registry by sport/stat (NBA: all CANDIDATE; NFL: 4 stat types active+promoted PROSPECTIVE_CERTIFIED).
select sport, stat_type, lifecycle_state, active, promoted, probability_publishable, candidate_research_active, count(*), max(training_rows)
  from public.wow_prop_fitted_model_artifacts where sport in ('NBA','NFL','WNBA') group by 1,2,3,4,5,6,7 order by 1,2;

-- Q7. Prop predictions per sport/lifecycle/calibration (NBA: 0 rows; NFL: PRECALIBRATION_SHRINKAGE).
select sport, controlling_specialist, model_artifact_lifecycle_state, calibration_status, probability_publishable,
       count(*) n, count(*) filter (where created_at > now() - interval '7 days') n7d, max(created_at)
  from public.wow_predictions where sport in ('NBA','NFL','NCAAF','WNBA') group by 1,2,3,4,5;

-- Q8. NFL forward calibration health (certification gate).
select * from public.wow_nfl_forward_calibration_health order by generated_at desc limit 1;

-- Q9. Agent-runtime tables the #2259 gate depends on (observed 2026-10-29: all 0 -> no NFL producer writes them).
select 'agent_runs' t, count(*) from public.wow_agent_runs
union all select 'run_candidates', count(*) from public.wow_agent_run_candidates
union all select 'terminal_decisions', count(*) from public.wow_agent_terminal_decisions
union all select 'candidate_payload_has_event_prediction_id', count(*) from public.wow_agent_run_candidates where candidate_payload ? 'event_prediction_id';

-- Q10. Where the NFL moneyline terminal is actually persisted: daily-run row detail (detail.result.*). `upstream_terminal_label`
-- is a PRE-reducer value: 42 rows carry upstream FINAL_APPROVED but final terminal MODEL_QUALIFIED_HOLD / publishable=false.
select row_status, detail->'result'->>'terminal_label' terminal_label, detail->'result'->>'terminal_ceiling' terminal_ceiling,
       detail->'result'->>'upstream_terminal_label' upstream, detail->'result'->>'probability_publishable' pp, detail->'result'->>'rank_eligible' rank_eligible,
       count(*) n, min(captured_at)::date first_seen, max(captured_at)::date last_seen, count(*) filter (where detail->'result'->>'event_prediction_id' is not null) with_epid
  from public.wow_v17_daily_run_row_detail where lane='MONEYLINE' and identity->>'sport'='NFL' group by 1,2,3,4,5,6 order by n desc;

-- Q11. Genuine end-to-end approved fixtures (observed: 153 rows / 31 events, 2026-09-30..10-08; all 153 join to a row-publishable prediction on an
-- active+promoted CHAMPION artifact with matching official_event_id) and the supersession race (observed: 84 of 153 later superseded by a non-approved row).
with fa as (select d.run_id,d.row_index,d.captured_at,d.identity->>'official_event_id' oeid,d.detail->'result'->>'event_prediction_id' epid
  from public.wow_v17_daily_run_row_detail d where d.lane='MONEYLINE' and d.identity->>'sport'='NFL' and d.row_status='COMPLETED'
   and d.detail->'result'->>'terminal_label'='FINAL_APPROVED' and d.detail->'result'->>'terminal_ceiling'='FINAL_APPROVED'
   and d.detail->'result'->>'probability_publishable'='true' and d.detail->'result'->>'rank_eligible'='true' and d.can_execute=false)
select count(*) fa_rows, count(*) filter (where p.model_probability_publishable) joined_pp_row, count(*) filter (where a.artifact_id is not null) joined_champion,
       count(*) filter (where p.official_event_id=fa.oeid) identity_match,
       count(*) filter (where exists (select 1 from public.wow_v17_daily_run_row_detail l where l.lane='MONEYLINE' and l.identity->>'sport'='NFL'
            and l.identity->>'official_event_id'=fa.oeid and l.captured_at>fa.captured_at and coalesce(l.detail->'result'->>'terminal_label','HELD')<>'FINAL_APPROVED')) superseded_by_later_nonapproved,
       count(distinct fa.oeid) distinct_events, max(p.event_start_time_utc) latest_event_start
  from fa left join public.wow_nfl_event_predictions p on p.event_prediction_id::text=fa.epid
  left join public.wow_nfl_event_fitted_model_artifacts a on a.artifact_id=p.model_artifact_id and a.active and a.promoted and a.lifecycle_state='CHAMPION';

-- Q12. NBA/WNBA team-event specialist registry (observed: NBA 90 artifacts, 6992 train / 1755 holdout, best Brier 0.2268, none active/promoted/certified).
select sport, model_family, active, promoted, (certified_at is not null) certified, count(*) n, max(training_row_count) train_n, max(holdout_row_count) holdout_n,
       round(min(holdout_brier)::numeric,4) min_brier, max(created_at)::date latest
  from public.wow_basketball_team_event_model_artifacts group by 1,2,3,4,5 order by 1;
