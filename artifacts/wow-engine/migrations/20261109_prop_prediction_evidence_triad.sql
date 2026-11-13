-- WOW V17 source migration: persist the prop evidence-triad receipt without
-- altering prior immutable predictions, calibrators or any probability.
--
-- REQUIRED BEFORE deploying v17/prop_universal_forward_evidence.py's new
-- _persist_prediction payload. Its four new fields otherwise cause a PostgREST
-- unknown-column upsert error in the current validation schema.
--
-- Forward research rows remain non-executable. Nothing here certifies or
-- promotes a model, grants a new API role, or makes a prediction publishable.

alter table public.wow_predictions
    add column if not exists model_evidence_triad jsonb,
    add column if not exists evidence_triad_required boolean not null default false,
    add column if not exists evidence_triad_status text,
    add column if not exists triad_blockers text[] not null default '{}'::text[];

-- The receipt has four governed states; arbitrary caller-supplied "approved"
-- or other values cannot silently represent a verified triad.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.wow_predictions'::regclass
          and conname = 'chk_wow_predictions_evidence_triad_status'
    ) then
        alter table public.wow_predictions
            add constraint chk_wow_predictions_evidence_triad_status
            check (
                evidence_triad_status is null
                or evidence_triad_status in (
                    'COMPLETE', 'INCOMPLETE', 'IDENTITY_UNRESOLVED', 'EVIDENCE_UNAVAILABLE'
                )
            );
    end if;
end
$$;

-- A record that asserts it was triad-gated must have a shaped receipt. This
-- is only a shape check: seal verification and exact evidence binding remain
-- the responsibility of the trusted scoring boundary and terminal reducer.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.wow_predictions'::regclass
          and conname = 'chk_wow_predictions_evidence_triad_receipt_shape'
    ) then
        alter table public.wow_predictions
            add constraint chk_wow_predictions_evidence_triad_receipt_shape
            check (
                evidence_triad_required = false
                or (
                    model_evidence_triad is not null
                    and jsonb_typeof(model_evidence_triad) = 'object'
                    and coalesce(model_evidence_triad ->> 'report_id', '') <> ''
                    and coalesce(model_evidence_triad ->> 'report_digest', '') <> ''
                    and evidence_triad_status is not null
                )
            );
    end if;
end
$$;

-- Incomplete, unresolved or unreadable triads may never be published. No
-- migration-time update of historical predictions is performed.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.wow_predictions'::regclass
          and conname = 'chk_wow_predictions_incomplete_triad_not_publishable'
    ) then
        alter table public.wow_predictions
            add constraint chk_wow_predictions_incomplete_triad_not_publishable
            check (
                evidence_triad_required = false
                or (
                    probability_publishable = false
                    or (
                        evidence_triad_status = 'COMPLETE'
                        and model_evidence_triad ->> 'bundle_complete' = 'true'
                        and coalesce(cardinality(triad_blockers), 0) = 0
                    )
                )
            );
    end if;
end
$$;

comment on column public.wow_predictions.model_evidence_triad is
    'Read-only triad assessment from a trusted V17 scoring boundary; never a certification or reducer receipt.';
comment on column public.wow_predictions.evidence_triad_required is
    'Whether the serving boundary required exact-version fitted artifact, calibrator and pregame corpus evidence.';
comment on column public.wow_predictions.triad_blockers is
    'Typed model-evidence blockers, distinct from upstream scorer and calibration blockers.';

-- No grants, RLS changes, UPDATE to historical predictions, or execution rights.
