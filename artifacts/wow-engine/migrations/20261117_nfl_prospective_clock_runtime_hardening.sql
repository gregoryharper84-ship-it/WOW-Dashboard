-- Forward-only hotfix for existing immutable NFL research trigger.
-- clock_timestamp() samples the actual execution instant, including after waits/locks;
-- Statement-start time was unsafe for kickoff-boundary insert semantics.
-- The existing BEFORE trigger retains this exact function identity.

CREATE OR REPLACE FUNCTION public.wow_nfl_prospective_immutable_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
    clock_time timestamptz := clock_timestamp();
    unchanged_old jsonb;
    unchanged_new jsonb;
    expected_brier double precision;
    expected_log_loss double precision;
    selected_p double precision;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'NFL_PROSPECTIVE_IMMUTABLE_DELETE_DENIED';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.lifecycle_state <> 'FORWARD_SHADOW'
           OR NEW.outcome IS NOT NULL OR NEW.graded_at IS NOT NULL
           OR NEW.brier IS NOT NULL OR NEW.log_loss IS NOT NULL OR NEW.hit IS NOT NULL
           OR NEW.grade_source_manifest <> '{}'::jsonb THEN
            RAISE EXCEPTION 'NFL_PROSPECTIVE_INSERT_MUST_BE_UNGRADED';
        END IF;
        IF NEW.event_start_time_utc <= clock_time
           OR NEW.home_team = NEW.away_team OR NEW.selected_participant NOT IN (NEW.home_team, NEW.away_team)
           OR NEW.source_feature_hash !~ '^[0-9a-f]{64}$'
           OR NEW.research_metadata->>'status' IS DISTINCT FROM 'PREGAME_SHADOW_UNCERTIFIED'
           OR coalesce(NEW.research_metadata->>'source_snapshot_sha256', '') !~ '^[0-9a-f]{64}$'
           OR coalesce(NEW.research_metadata->>'model_artifact_sha256', '') !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'NFL_PROSPECTIVE_PREGAME_SOURCE_IDENTITY_INVALID';
        END IF;
        IF coalesce((NEW.research_metadata->>'source_snapshot_fetched_at')::timestamptz, clock_time + interval '1 hour') > clock_time
           OR (NEW.research_metadata->>'source_snapshot_fetched_at')::timestamptz < clock_time - interval '96 hours' THEN
            RAISE EXCEPTION 'NFL_PROSPECTIVE_SOURCE_STALE';
        END IF;
        -- Database/server time is the authority, not the caller-supplied clock.
        NEW.created_at := clock_time;
        NEW.prediction_created_at := clock_time;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        unchanged_old := to_jsonb(OLD) - ARRAY['outcome','graded_at','brier','log_loss','hit',
                                                  'lifecycle_state','grade_source_manifest'];
        unchanged_new := to_jsonb(NEW) - ARRAY['outcome','graded_at','brier','log_loss','hit',
                                                  'lifecycle_state','grade_source_manifest'];
        IF unchanged_new IS DISTINCT FROM unchanged_old
           OR OLD.lifecycle_state <> 'FORWARD_SHADOW' OR NEW.lifecycle_state <> 'GRADED'
           OR OLD.outcome IS NOT NULL OR OLD.grade_source_manifest <> '{}'::jsonb THEN
            RAISE EXCEPTION 'NFL_PROSPECTIVE_FROZEN_FORECAST_MUTATION_DENIED';
        END IF;
        IF clock_time <= OLD.event_start_time_utc OR NEW.graded_at > clock_time + interval '30 seconds'
           OR NEW.graded_at < OLD.event_start_time_utc
           OR NEW.outcome NOT IN (0,1)
           OR NEW.hit IS DISTINCT FROM (NEW.outcome=1)
           OR NEW.grade_source_manifest->>'status' IS DISTINCT FROM 'TWO_PROVIDER_RECONCILED_NOT_LEAGUE_CERTIFIED'
           OR NEW.grade_source_manifest->'sources' IS DISTINCT FROM '["ESPN","NFLVERSE"]'::jsonb
           OR coalesce(NEW.grade_source_manifest->>'espn_raw_sha256', '') !~ '^[0-9a-f]{64}$'
           OR coalesce(NEW.grade_source_manifest->>'nflverse_content_sha256', '') !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'NFL_PROSPECTIVE_GRADE_PROVENANCE_INVALID';
        END IF;
        selected_p := greatest(1e-6, least(1-1e-6, OLD.calibrated_probability));
        expected_brier := power(OLD.calibrated_probability - NEW.outcome, 2);
        expected_log_loss := -(NEW.outcome * ln(selected_p) + (1-NEW.outcome)*ln(1-selected_p));
        IF NEW.brier IS NULL OR NEW.log_loss IS NULL
           OR abs(NEW.brier-expected_brier)>1e-9
           OR abs(NEW.log_loss-expected_log_loss)>1e-9 THEN
            RAISE EXCEPTION 'NFL_PROSPECTIVE_GRADE_MATH_INVALID';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'NFL_PROSPECTIVE_OPERATION_DENIED';
END;
$guard$;
