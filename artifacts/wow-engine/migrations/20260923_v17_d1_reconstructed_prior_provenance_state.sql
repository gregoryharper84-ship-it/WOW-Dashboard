-- V17 D1 candidate registry: permit the reconstructed-prior provenance-ready
-- evidence state emitted by the NCAAF result/form challenger.
--
-- This state is deliberately NOT certification PASS. Certification replay keeps
-- requiring source_review_status='PASS'; this migration only prevents a valid
-- research-only candidate from failing persistence at the database boundary.

alter table public.wow_d1_candidate_artifacts
  drop constraint if exists wow_d1_candidate_artifacts_source_review_status_check;

alter table public.wow_d1_candidate_artifacts
  add constraint wow_d1_candidate_artifacts_source_review_status_check
  check (source_review_status in (
    'REQUIRED',
    'PASS',
    'FAIL',
    'CC_BY_4_0_PROVENANCE_READY',
    'CC0_PUBLIC_DOMAIN_PROVENANCE_READY',
    'RECONSTRUCTED_PRIOR_RESULTS_PROVENANCE_READY'
  ));

comment on column public.wow_d1_candidate_artifacts.source_review_status is
  'Governed source-review state. PASS alone clears source review. *_PROVENANCE_READY values are pre-review evidence states and remain non-certifying.';
