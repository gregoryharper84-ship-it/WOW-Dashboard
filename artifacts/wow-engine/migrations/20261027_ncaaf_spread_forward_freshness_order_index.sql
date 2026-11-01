-- V17 NCAAF spread forward-shadow latest-source fingerprint access path.
--
-- Incident #1059, source PR #2160 (2026-10-27): the previous index
-- (sport, model_family, feature_schema_version, official_event_id) covered
-- persisted corpus pagination but not the latest-event freshness query.
-- The exact PostgREST query ordered by event_start_time DESC, official_event_id
-- DESC, scanning 16,533 rows to return just one. Read-only validation
-- EXPLAIN ANALYZE measured 5,076 ms and ~16,000 shared buffers.
--
-- Class B data-access performance only. The previous index remains available
-- for official_event_id ordered pagination; this additional btree matches the
-- three exact equality predicates plus both DESC order keys.
-- Do not add odds/market features, mutate rows/artifacts/calibrators, or alter
-- V17_TERMINAL_REDUCER / can_execute=false.
--
-- Apply through authorized migration/release path. For a high-traffic
-- database, plan an independently reviewed online/concurrent index build.
-- This repository migration intentionally uses standard idempotent DDL
-- compatible with the existing transactional migration runner.

CREATE INDEX IF NOT EXISTS wow_d1_training_rows_ncaaf_spread_freshness_order_idx
    ON public.wow_d1_training_rows USING btree (
        sport,
        model_family,
        feature_schema_version,
        event_start_time DESC,
        official_event_id DESC
    );
