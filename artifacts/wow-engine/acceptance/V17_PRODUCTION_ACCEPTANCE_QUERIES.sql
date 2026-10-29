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
