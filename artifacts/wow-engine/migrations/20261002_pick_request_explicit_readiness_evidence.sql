-- V17 candidate-readiness evidence on the existing durable pick-request ledger.
-- Class B reliability only: no sporting probability, calibration, ranking,
-- publication threshold, terminal-reducer, or execution-authority changes.

alter table public.wow_pick_request_row_states
    add column if not exists identity_verified_explicit boolean not null default false,
    add column if not exists model_inputs_ready_explicit boolean not null default false,
    add column if not exists identity_evidence jsonb not null default '{}'::jsonb,
    add column if not exists hydration_evidence jsonb not null default '{}'::jsonb,
    add column if not exists specialist_id text,
    add column if not exists feature_snapshot_id text;

alter table public.wow_pick_request_row_states
    add constraint wow_pick_request_row_states_identity_evidence_object
        check (jsonb_typeof(identity_evidence) = 'object'),
    add constraint wow_pick_request_row_states_hydration_evidence_object
        check (jsonb_typeof(hydration_evidence) = 'object');

comment on column public.wow_pick_request_row_states.identity_verified_explicit is
    'True only when the identity/reconciliation validator emitted an explicit readiness receipt. Synthetic stage gap-fill never sets this flag.';
comment on column public.wow_pick_request_row_states.model_inputs_ready_explicit is
    'True only when the hydration/model-input validator emitted an explicit readiness receipt. Synthetic stage gap-fill never sets this flag.';
comment on column public.wow_pick_request_row_states.identity_evidence is
    'Probability-neutral canonical/provider identity evidence supporting IDENTITY_VERIFIED.';
comment on column public.wow_pick_request_row_states.hydration_evidence is
    'Probability-neutral completeness/freshness evidence supporting MODEL_INPUTS_READY.';
comment on column public.wow_pick_request_row_states.specialist_id is
    'Controlling fitted specialist identity when assigned; never a probability or execution authority.';
comment on column public.wow_pick_request_row_states.feature_snapshot_id is
    'Immutable feature/evidence snapshot identity bound before specialist evaluation when available.';

-- Existing RLS/ACL policy remains authoritative. These columns do not add a new
-- exposed table and can_execute remains constrained false by the incumbent schema.
