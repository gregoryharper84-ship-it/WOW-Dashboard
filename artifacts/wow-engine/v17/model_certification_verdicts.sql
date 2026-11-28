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
-- 1. NFL: the champion exactly as
--    `public.wow_nfl_event_certified_model_artifact('NFL_EVENT_PREGAME_PRIOR_V1')`
--    selects it, plus the latest forward calibration-health row cross-checked
--    against the champion's own prospective cohort.
--
--    The champion is read THROUGH the governed RPC, not re-selected from the
--    table here, so the selection rule (CHAMPION first, then promoted_at, then
--    created_at) cannot drift from the function the runtime actually calls.
--
--    Do NOT add `a.probability_publishable` here. That column exists but is
--    pinned false by `wow_nfl_event_fitted_registry_not_probability_publication`,
--    which no later migration drops; filtering on it returns zero rows and the
--    verdict would read as UNVERIFIED forever. NFL publication authority is the
--    certified-artifact RPC, not this column.
--
--    Omitted deliberately: `wow_v17_refresh_nfl_event_capability()` additionally
--    requires an active PASS calibrator row. That predicate reads
--    wow_calibrators.sport / market_family / model_family / validation_status /
--    health_status, none of which is defined by any create table or alter table
--    in this repository (schema.sql's calibrator block predates them and is
--    truncated mid-constraint). Referencing them here would risk SQLSTATE 42703,
--    so this query does not: a true verdict below is necessary but not
--    sufficient, and the calibrator half must be read from
--    `wow_v17_refresh_nfl_event_capability()`.
--
--    The ledger's graded_n is a global snapshot, so a passing ledger alone must
--    not authorize a champion that lacks its own grades: the cohort CTE recounts
--    distinct official_event_id rows for the exact selected model version.
-- ---------------------------------------------------------------------------
with artifact as (
    select public.wow_nfl_event_certified_model_artifact('NFL_EVENT_PREGAME_PRIOR_V1') as state
),
champion as (
    select a.state ->> 'model_artifact_version' as model_artifact_version,
           coalesce((a.state ->> 'ok')::boolean, false) as certified
      from artifact a
),
cohort as (
    select count(distinct g.official_event_id) as graded_n
      from public.wow_nfl_forward_shadow_grades g
      join champion ch on ch.model_artifact_version = g.model_version
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
        and exists (select 1 from champion where certified)
    ) as model_certified
  from public.wow_nfl_forward_calibration_health h
 cross join cohort
 order by h.generated_at desc
 limit 1;

-- ---------------------------------------------------------------------------
-- 2. NBA: the certification row, joined to a live artifact.
--
--    Mirrors `public.wow_basketball_certified_model_artifact('NBA')`: the
--    certification must name the artifact version that is actually active and
--    promoted. capability_status alone is not enough. calibrator_version is
--    reported but deliberately NOT required -- the governed function does not
--    test it, and adding it here would be a stricter verdict than the database
--    records.
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
        and exists (
            select 1
              from public.wow_basketball_team_event_model_artifacts a
             where a.sport = c.sport
               and a.model_artifact_version = c.model_artifact_version
               and a.promoted
               and a.active
        )
    ) as model_certified
  from public.wow_basketball_team_event_certification c
 where c.sport = 'NBA';

-- ---------------------------------------------------------------------------
-- 3. NCAAF: the artifact row carries its own publication gate.
--
--    Mirrors `public.wow_ncaaf_certified_model_artifact(feature_schema_version)`,
--    including its champion-first ordering. The table's check constraint ties
--    probability_publishable to promotion, a certified lifecycle and a fitted
--    calibrator; this query re-states the whole predicate so a row edited around
--    the constraint still cannot certify.
-- ---------------------------------------------------------------------------
select
    'NCAAF' as sport,
    a.feature_schema_version,
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
 order by case when a.lifecycle_state = 'CHAMPION' then 0 else 1 end,
          a.created_at desc
 limit 1;
