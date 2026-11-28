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
-- NCAAF is the exception. Its artifact table can be empty, or contain rows
-- with none active, and those are different gaps in the evidence that a zero-row
-- result cannot tell apart. Query 3 therefore always returns exactly one row,
-- carrying `verdict` and `reason_code`; when it is UNVERIFIED, model_certified
-- is NULL -- never false.
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
--    The calibrator half of the gate is NOT re-derived here. It reads the
--    governed `wow_runtime_capabilities` row that
--    `wow_v17_refresh_nfl_event_capability()` maintains, and requires
--    evidence.calibrator_ready to be present. If that row, or that key, is
--    missing the whole query returns NO ROW -- UNVERIFIED -- so an omitted
--    check can never masquerade as `model_certified = false`. The calibrator
--    predicate itself lives in wow_calibrators.sport / market_family /
--    validation_status / health_status, none of which any create table or
--    alter table in this repository defines (schema.sql's calibrator block
--    predates them and is truncated mid-constraint); reading the capability
--    row avoids re-deriving a gate whose inputs are not in this repo.
--
--    The ledger's graded_n is a global snapshot, so a passing ledger alone must
--    not authorize a champion that lacks its own grades: the cohort CTE recounts
--    distinct official_event_id rows for the exact selected model version.
-- ---------------------------------------------------------------------------
with capability as (
    select c.capability_status,
           (c.evidence ->> 'calibrator_ready')::boolean as calibrator_ready,
           c.updated_at as capability_updated_at
      from public.wow_runtime_capabilities c
     where c.capability_key = 'NFL_EVENT_PROBABILITY'
       and c.evidence ->> 'calibrator_ready' is not null
),
artifact as (
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
    cap.capability_status,
    cap.calibrator_ready,
    cap.capability_updated_at,
    h.status,
    h.certification_recommendation,
    h.graded_n as ledger_graded_n,
    cohort.graded_n as cohort_graded_n,
    h.minimum_forward_required,
    h.blockers,
    (
        cap.capability_status = 'AVAILABLE'
        and cap.calibrator_ready
        and h.status = 'PASS'
        and h.certification_recommendation = 'FORWARD_EVIDENCE_PASS'
        and h.blockers = '[]'::jsonb
        and h.graded_n >= h.minimum_forward_required
        and cohort.graded_n >= h.minimum_forward_required
        and h.minimum_forward_required >= 100   -- MIN_FORWARD_GRADED floor
        and exists (select 1 from champion where certified)
    ) as model_certified
  from public.wow_nfl_forward_calibration_health h
 cross join capability cap
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
--
--    Unlike the other two, this ALWAYS returns one row, so an empty registry and
--    a registry with no active artifact stay distinguishable:
--      total_rows = 0        -> UNVERIFIED / NO_ARTIFACT_RECORDED
--      total_rows > 0, none active -> UNVERIFIED / NO_ACTIVE_ARTIFACT
--      active artifact fails the predicate -> MODEL_HOLD
--      active artifact passes              -> MODEL_CERTIFIED
--
--    model_certified is NULL only in the two UNVERIFIED branches. The held
--    branch says false outright: `picked.certified` is a three-valued AND, so
--    a NULL operand would otherwise yield NULL there while `verdict` fell
--    through to MODEL_HOLD -- a recorded failure reading as missing evidence.
-- ---------------------------------------------------------------------------
with counts as (
    select count(*) as total_rows
      from public.wow_ncaaf_fitted_model_artifacts
),
ranked as (
    select a.artifact_id,
           a.feature_schema_version,
           a.lifecycle_state,
           a.probability_publishable,
           a.calibrator_version,
           a.calibration_training_n,
           a.promoted,
           a.active,
           row_number() over (
               order by case when a.lifecycle_state = 'CHAMPION' then 0 else 1 end,
                        a.created_at desc
           ) as rn
      from public.wow_ncaaf_fitted_model_artifacts a
     where a.active
),
picked as (
    select p.artifact_id,
           p.feature_schema_version,
           p.lifecycle_state,
           p.probability_publishable,
           p.calibrator_version,
           p.calibration_training_n,
           (
               p.probability_publishable
               and p.promoted
               and p.active
               and p.lifecycle_state in ('PROSPECTIVE_CERTIFIED', 'CHAMPION')
               and p.calibrator_version is not null
               and p.calibration_training_n > 0
           ) as certified
      from ranked p
     where p.rn = 1
)
select
    'NCAAF' as sport,
    counts.total_rows,
    picked.feature_schema_version,
    picked.artifact_id,
    picked.lifecycle_state,
    picked.probability_publishable,
    picked.calibrator_version,
    picked.calibration_training_n,
    case
        when counts.total_rows = 0 then null
        when picked.artifact_id is null then null
        when picked.certified then true
        else false
    end as model_certified,
    case
        when counts.total_rows = 0 then 'UNVERIFIED'
        when picked.artifact_id is null then 'UNVERIFIED'
        when picked.certified then 'MODEL_CERTIFIED'
        else 'MODEL_HOLD'
    end as verdict,
    case
        when counts.total_rows = 0 then 'NO_ARTIFACT_RECORDED'
        when picked.artifact_id is null then 'NO_ACTIVE_ARTIFACT'
        when picked.certified then 'CERTIFIED_ARTIFACT_PUBLISHABLE'
        else 'ACTIVE_ARTIFACT_NOT_PUBLISHABLE'
    end as reason_code
  from counts
  left join picked on true;
