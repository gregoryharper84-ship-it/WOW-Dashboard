-- WOW V17 MODEL_CERTIFIED verdicts: read-only, one query per governed sport.
--
-- These replace the Python certification axis that was removed from
-- v17/restoration_status_verifier.py. Each query reads the verdict the governed
-- layer already recorded and returns a boolean `model_certified`; none of them
-- promotes, updates, or authorizes anything, and none can enable execution.
--
-- Run as a read-only role. Report the row plus the boolean; never replace a
-- missing relation with `false`:
--   * PostgreSQL raises SQLSTATE 42P01 (undefined_table) when a relation is
--     absent. That is UNVERIFIED, not MODEL_HOLD. Do not swallow it.
--   * SQLSTATE 42703 (undefined_column, "column x does not exist") is schema
--     drift and must also surface as a failure, never as absence.
--
-- NHL has no governed team-event model registry, so its MODEL_CERTIFIED is
-- always false with reason NO_GOVERNED_RELATION. It is intentionally absent
-- here rather than invented.
--
-- MODEL_CERTIFIED is not one of the five required CI checks and is not a
-- promotion mechanism. It answers one question: does the governed row say this
-- sport may publish a probability today?
--
-- A query that returns NO ROW is UNVERIFIED. It is never MODEL_HOLD and never
-- `model_certified = false`. No row means the governed layer recorded no
-- verdict for that sport, which is missing evidence, not a negative finding.
-- Reading an empty ledger as "not certified" would turn an unread table into a
-- decision nobody made. Do not collapse the two states:
--
--   row with model_certified = false -> the verdict was recorded and failed
--                                       (MODEL_HOLD), with its reason visible
--   no row at all                    -> nothing was recorded (UNVERIFIED),
--                                       and the missing relation or row must
--                                       be named in the report
--
-- Read these verdicts SIDE BY SIDE with the runtime report. No single artifact
-- combines the two axes any more: v17/restoration_status_verifier.py answers
-- RUNTIME_RESTORED, and this file answers MODEL_CERTIFIED. Report both, keep
-- them distinct, and never let a passing runtime report imply a model verdict
-- (or the reverse).

-- ---------------------------------------------------------------------------
-- 1. NFL: the latest forward calibration-health row, cross-checked against the
--    champion model's own prospective cohort.
--
--    Mirrors `_forward_certification_proven` in v17/nfl_daily_stage_receipts.py:
--    the ledger's graded_n is a global snapshot, so a passing ledger alone must
--    not authorize a champion that lacks its own grades. The cohort CTE recounts
--    distinct official_event_id rows for the exact active+promoted+publishable
--    model version, and `champion_count = 1` refuses an ambiguous champion set.
-- ---------------------------------------------------------------------------
with champion as (
    select a.model_artifact_version
      from public.wow_nfl_event_fitted_model_artifacts a
     where a.active
       and a.promoted
       and a.probability_publishable
),
champion_count as (
    select count(*) as n from champion
),
cohort as (
    select count(distinct g.official_event_id) as graded_n
      from public.wow_nfl_forward_shadow_grades g
      join champion c on c.model_artifact_version = g.model_version
)
select
    'NFL' as sport,
    h.status,
    h.certification_recommendation,
    h.graded_n as ledger_graded_n,
    cohort.graded_n as cohort_graded_n,
    h.minimum_forward_required,
    h.blockers,
    (
        h.status = 'PASS'
        and h.certification_recommendation = 'FORWARD_EVIDENCE_PASS'
        and h.blockers = '[]'::jsonb
        and h.graded_n >= h.minimum_forward_required
        and cohort.graded_n >= h.minimum_forward_required
        and h.minimum_forward_required >= 100   -- MIN_FORWARD_GRADED floor
        and champion_count.n = 1
    ) as model_certified
  from public.wow_nfl_forward_calibration_health h
 cross join cohort
 cross join champion_count
 order by h.generated_at desc
 limit 1;

-- ---------------------------------------------------------------------------
-- 2. NBA: the certification row, joined to a live artifact.
--
--    Mirrors `wow_basketball_certified_model_artifact()`: the certification
--    must name the artifact version that is actually active and promoted, and
--    must carry a calibrator version. capability_status alone is not enough.
-- ---------------------------------------------------------------------------
select
    'NBA' as sport,
    c.capability_status,
    c.reason_code,
    c.model_artifact_version,
    c.calibrator_version,
    c.corpus_rows,
    c.settled_rows,
    (
        c.capability_status = 'CERTIFIED'
        and c.model_artifact_version is not null
        and c.calibrator_version is not null
        and exists (
            select 1
              from public.wow_basketball_team_event_model_artifacts a
             where a.sport = 'NBA'
               and a.active
               and a.promoted
               and a.model_artifact_version = c.model_artifact_version
        )
    ) as model_certified
  from public.wow_basketball_team_event_certification c
 where c.sport = 'NBA';

-- ---------------------------------------------------------------------------
-- 3. NCAAF: the artifact row carries its own publication gate.
--
--    Mirrors `wow_ncaaf_certified_model_artifact()`. The table's check
--    constraint ties probability_publishable to promotion, a certified
--    lifecycle and a fitted calibrator; this query re-states the whole
--    predicate so a row edited around the constraint still cannot certify.
-- ---------------------------------------------------------------------------
select
    'NCAAF' as sport,
    a.artifact_id,
    a.lifecycle_state,
    a.probability_publishable,
    a.calibrator_version,
    a.calibration_training_n,
    (
        a.probability_publishable
        and a.promoted
        and a.active
        and a.lifecycle_state in ('PROSPECTIVE_CERTIFIED', 'CHAMPION')
        and a.calibrator_version is not null
        and a.calibration_training_n > 0
    ) as model_certified
  from public.wow_ncaaf_fitted_model_artifacts a
 where a.active
 order by a.created_at desc
 limit 1;
