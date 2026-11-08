-- NBA POINTS research snapshots are append-only once captured.
-- Other sports retain the existing shared-table writer contract.
CREATE OR REPLACE FUNCTION public.wow_reject_nba_points_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.sport = 'NBA' AND OLD.stat_type = 'POINTS' THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'NBA_POINTS_SNAPSHOT_IMMUTABLE';
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF (
            (OLD.sport = 'NBA' AND OLD.stat_type = 'POINTS')
            OR (NEW.sport = 'NBA' AND NEW.stat_type = 'POINTS')
        ) AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'NBA_POINTS_SNAPSHOT_IMMUTABLE';
        END IF;
        RETURN NEW;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS wow_nba_points_snapshot_immutable_guard
    ON public.wow_prop_evidence_snapshots;

CREATE TRIGGER wow_nba_points_snapshot_immutable_guard
BEFORE UPDATE OR DELETE
ON public.wow_prop_evidence_snapshots
FOR EACH ROW
EXECUTE FUNCTION public.wow_reject_nba_points_snapshot_mutation();

COMMENT ON FUNCTION public.wow_reject_nba_points_snapshot_mutation()
IS 'Prevents mutation or deletion of immutable NBA POINTS evidence snapshots; leaves other sports unchanged.';
