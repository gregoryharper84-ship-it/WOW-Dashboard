-- V17 Fantasy Score forward-evidence identity.
-- Additive only. Existing prediction rows remain valid and unchanged.
-- These columns prevent calibration evidence from silently losing sport/role,
-- controlling-specialist, or exact scoring-profile identity.

alter table public.wow_predictions
  add column if not exists fantasy_score_lane text,
  add column if not exists market_family text,
  add column if not exists controlling_specialist text,
  add column if not exists scoring_profile_id text,
  add column if not exists scoring_profile_sha256 text,
  add column if not exists evidence_source_kind text,
  add column if not exists fantasy_position text;

alter table public.wow_predictions
  drop constraint if exists chk_wow_predictions_fantasy_score_lane;
alter table public.wow_predictions
  add constraint chk_wow_predictions_fantasy_score_lane
  check (
    fantasy_score_lane is null or fantasy_score_lane in
      ('NFL','NBA','WNBA','MLB_HITTER','MLB_PITCHER')
  );

alter table public.wow_predictions
  drop constraint if exists chk_wow_predictions_fantasy_evidence_source;
alter table public.wow_predictions
  add constraint chk_wow_predictions_fantasy_evidence_source
  check (
    evidence_source_kind is null or evidence_source_kind in
      ('IMMUTABLE_PREGAME_SETTLED','SYNTHETIC_TEST_ONLY')
  );

alter table public.wow_predictions
  drop constraint if exists chk_wow_predictions_scoring_profile_sha256;
alter table public.wow_predictions
  add constraint chk_wow_predictions_scoring_profile_sha256
  check (
    scoring_profile_sha256 is null or scoring_profile_sha256 ~ '^[0-9a-f]{64}$'
  );

-- Candidate forward-evidence rows must remain non-publishable. Production
-- promotion creates/serves governed production artifacts through the normal
-- V17 lifecycle rather than mutating historical candidate evidence.
alter table public.wow_predictions
  drop constraint if exists chk_wow_predictions_fantasy_candidate_not_publishable;
alter table public.wow_predictions
  add constraint chk_wow_predictions_fantasy_candidate_not_publishable
  check (
    fantasy_score_lane is null
    or evidence_source_kind <> 'IMMUTABLE_PREGAME_SETTLED'
    or probability_publishable = false
  );

create index if not exists idx_wow_predictions_fantasy_forward
  on public.wow_predictions(fantasy_score_lane, stat_type, event_start_time)
  where fantasy_score_lane is not null;
