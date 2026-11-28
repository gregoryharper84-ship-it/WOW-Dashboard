-- WOW V17 MODEL_CERTIFIED verdicts: read-only, one query per governed sport.
--
-- These replace the Python certification axis that was removed from
-- v17/restoration_status_verifier.py. Each query reads the verdict the governed
-- layer already recorded; none of them promotes, updates, or authorizes
-- anything, and none can enable execution.
--
-- Run as a read-only role. Every query returns EXACTLY ONE ROW, and all three
-- share the same leading columns, so they can be read side by side or unioned:
--
--   sport | verdict | reason_code | model_certified | <sport-specific detail>
--
-- verdict is one of:
--   MODEL_CERTIFIED -> the governed rows say this sport may publish today
--   MODEL_HOLD      -> a verdict was recorded and failed; reason_code names it
--   UNVERIFIED      -> the evidence needed to decide was never recorded;
--                      reason_code names the missing row or key
--
-- model_certified is NULL only for UNVERIFIED and is derived from `verdict`
-- itself, so the boolean and the label cannot disagree. It is never false on an
-- unverified row: a recorded failure and a missing record must not read alike.
--
-- A relation or column that does not exist still fails loudly with SQLSTATE
-- 42P01 or 42703 instead of returning a row. Treat that as UNVERIFIED too, but
-- surface it; never let it degrade into a verdict.
--
-- NHL has no governed team-event model registry, so its MODEL_CERTIFIED is
-- always false with reason NO_GOVERNED_RELATION. It is intentionally absent
-- here rather than invented.
--
-- MODEL_CERTIFIED is not one of the five required CI checks and is not a
-- promotion mechanism. It answers one question: does the governed layer say
-- this sport may publish a probability today?
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
--    evidence.calibrator_ready to be present. When that row or that key is
--    missing the verdict is UNVERIFIED / NO_CALIBRATOR_EVIDENCE -- never false,
--    so an omitted check cannot masquerade as a recorded failure.
--
--    The ledger's graded_n is a global snapshot, so a passing ledger alone must
--    not authorize a champion that lacks its own grades: the cohort CTE recounts
--    distinct official_event_id rows for the exact selected model version.
-- ---------------------------------------------------------------------------
with capability as (
    select c.capability_key,
           c.capability_status,
           (c.evidence ->> 'calibrator_ready')::boolean as calibrator_ready,
           c.evidence ->> 'calibrator_ready' is not null as evidence_present,
           c.updated_at as capability_updated_at
      from public.wow_runtime_capabilities c
     where c.capability_key = 'NFL_EVENT_PROBABILITY'
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
),
health as (
    select h.health_id,
           h.status,
           h.certification_recommendation,
           h.graded_n,
           h.minimum_forward_required,
           h.blockers,
           h.generated_at
      from public.wow_nfl_forward_calibration_health h
     order by h.generated_at desc
     limit 1
),
scored as (
    select capability.capability_key,
           capability.evidence_present,
           capability.capability_status,
           capability.calibrator_ready,
           capability.capability_updated_at,
           champion.model_artifact_version,
           champion.certified as certified_artifact_ready,
           health.health_id,
           health.status,
           health.certification_recommendation,
           health.graded_n,
           health.minimum_forward_required,
           health.blockers,
           health.generated_at,
           cohort.graded_n as cohort_graded_n,
           (
               capability.capability_status = 'AVAILABLE'
               and capability.calibrator_ready
               and champion.certified
               and health.status = 'PASS'
               and health.certification_recommendation = 'FORWARD_EVIDENCE_PASS'
               and health.blockers = '[]'::jsonb
               and health.graded_n >= health.minimum_forward_required
               and cohort.graded_n >= health.minimum_forward_required
               and health.minimum_forward_required >= 100   -- MIN_FORWARD_GRADED floor
           ) as gates_pass
      from (select 1) anchor
      left join capability on true
      left join health on true
     cross join cohort
     cross join champion
),
judged as (
    select scored.*,
           case
               when capability_key is null then 'UNVERIFIED'
               when not evidence_present then 'UNVERIFIED'
               when health_id is null then 'UNVERIFIED'
               when gates_pass then 'MODEL_CERTIFIED'
               else 'MODEL_HOLD'
           end as verdict,
           case
               when capability_key is null then 'NO_CAPABILITY_RECORD'
               when not evidence_present then 'NO_CALIBRATOR_EVIDENCE'
               when health_id is null then 'NO_CALIBRATION_HEALTH_RECORD'
               when capability_status <> 'AVAILABLE' then 'CAPABILITY_NOT_AVAILABLE'
               when not calibrator_ready then 'CALIBRATOR_NOT_READY'
               when not certified_artifact_ready then 'CERTIFIED_ARTIFACT_UNAVAILABLE'
               when status <> 'PASS'
                 or certification_recommendation <> 'FORWARD_EVIDENCE_PASS' then 'LEDGER_NOT_PASS'
               when blockers <> '[]'::jsonb then 'LEDGER_BLOCKERS_PRESENT'
               when minimum_forward_required < 100 then 'FORWARD_FLOOR_BELOW_GOVERNED_MINIMUM'
               when graded_n < minimum_forward_required then 'LEDGER_COHORT_BELOW_MINIMUM'
               when cohort_graded_n < minimum_forward_required then 'CHAMPION_COHORT_BELOW_MINIMUM'
               else 'CERTIFIED_ARTIFACT_PUBLISHABLE'
           end as reason_code
      from scored
)
select
    'NFL' as sport,
    judged.verdict,
    judged.reason_code,
    case
        when judged.verdict = 'UNVERIFIED' then null
        when judged.verdict = 'MODEL_CERTIFIED' then true
        else false
    end as model_certified,
    judged.capability_status,
    judged.calibrator_ready,
    judged.capability_updated_at,
    judged.model_artifact_version,
    judged.certified_artifact_ready,
    judged.status as ledger_status,
    judged.certification_recommendation,
    judged.graded_n as ledger_graded_n,
    judged.cohort_graded_n,
    judged.minimum_forward_required,
    judged.blockers
  from judged;

-- ---------------------------------------------------------------------------
-- 2. NBA: the certification row, joined to a live artifact.
--
--    Mirrors `public.wow_basketball_certified_model_artifact('NBA')`: the
--    certification must name the artifact version that is actually active and
--    promoted. capability_status alone is not enough. calibrator_version is
--    reported but deliberately NOT required -- the governed function does not
--    test it, and adding it here would be a stricter verdict than the database
--    records.
--
--    No certification row means no verdict was ever recorded: UNVERIFIED /
--    NO_CERTIFICATION_RECORD, not MODEL_HOLD.
-- ---------------------------------------------------------------------------
with cert as (
    select c.sport,
           c.capability_status,
           c.reason_code as registry_reason_code,
           c.model_artifact_version,
           c.calibrator_version,
           c.corpus_rows,
           c.settled_rows,
           exists (
               select 1
                 from public.wow_basketball_team_event_model_artifacts a
                where a.sport = c.sport
                  and a.model_artifact_version = c.model_artifact_version
                  and a.promoted
                  and a.active
           ) as live_artifact
      from public.wow_basketball_team_event_certification c
     where c.sport = 'NBA'
),
judged as (
    select cert.*,
           case
               when cert.sport is null then 'UNVERIFIED'
               when cert.capability_status = 'CERTIFIED' and cert.live_artifact then 'MODEL_CERTIFIED'
               else 'MODEL_HOLD'
           end as verdict,
           case
               when cert.sport is null then 'NO_CERTIFICATION_RECORD'
               when cert.capability_status <> 'CERTIFIED' then 'CAPABILITY_NOT_CERTIFIED'
               when not cert.live_artifact then 'NO_LIVE_ARTIFACT'
               else 'CERTIFIED_ARTIFACT_PUBLISHABLE'
           end as reason_code
      from (select 1) anchor
      left join cert on true
)
select
    'NBA' as sport,
    judged.verdict,
    judged.reason_code,
    case
        when judged.verdict = 'UNVERIFIED' then null
        when judged.verdict = 'MODEL_CERTIFIED' then true
        else false
    end as model_certified,
    judged.capability_status,
    judged.registry_reason_code,
    judged.model_artifact_version,
    judged.calibrator_version,
    judged.corpus_rows,
    judged.settled_rows,
    judged.live_artifact
  from judged;

-- ---------------------------------------------------------------------------
-- 3. NCAAF: the artifact row carries its own publication gate.
--
--    Mirrors `public.wow_ncaaf_certified_model_artifact(feature_schema_version)`,
--    including its champion-first ordering. The table's check constraint ties
--    probability_publishable to promotion, a certified lifecycle and a fitted
--    calibrator; this query re-states the whole predicate so a row edited around
--    the constraint still cannot certify.
--
--    An empty registry and a registry with no active artifact are different
--    gaps in the evidence, so they get different reason codes:
--      total_rows = 0              -> UNVERIFIED / NO_ARTIFACT_RECORDED
--      total_rows > 0, none active -> UNVERIFIED / NO_ACTIVE_ARTIFACT
--      active artifact fails       -> MODEL_HOLD / ACTIVE_ARTIFACT_NOT_PUBLISHABLE
--      active artifact passes      -> MODEL_CERTIFIED
--
--    picked.certified is a three-valued AND, so it is only ever used to pick
--    the verdict; model_certified is derived from the verdict itself. A NULL
--    operand therefore cannot produce a null boolean under MODEL_HOLD.
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
),
judged as (
    select counts.total_rows,
           picked.feature_schema_version,
           picked.artifact_id,
           picked.lifecycle_state,
           picked.probability_publishable,
           picked.calibrator_version,
           picked.calibration_training_n,
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
      left join picked on true
)
select
    'NCAAF' as sport,
    judged.verdict,
    judged.reason_code,
    case
        when judged.verdict = 'UNVERIFIED' then null
        when judged.verdict = 'MODEL_CERTIFIED' then true
        else false
    end as model_certified,
    judged.total_rows,
    judged.feature_schema_version,
    judged.artifact_id,
    judged.lifecycle_state,
    judged.probability_publishable,
    judged.calibrator_version,
    judged.calibration_training_n
  from judged;
