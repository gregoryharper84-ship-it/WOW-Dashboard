-- WOW V17 MLB score timestamp chronology repair — 2026-09-21
--
-- PostgreSQL now() is transaction-stable. The lineup confirmer records
-- lineup_confirmed_at with clock_timestamp() and then calls the fitted scorer in
-- the same transaction. A score row whose model_timestamp defaults to now() can
-- therefore appear milliseconds *before* the lineup confirmation even though it
-- was produced after confirmation. The governance hydrator correctly rejects
-- that chronology as POST_LINEUP_SCORE_SNAPSHOT_REQUIRED, which then prevents
-- evidence/failure-path/calibration fields from binding to the event prediction.
--
-- Use wall-clock time for future fitted-score timestamps. No historical score
-- receipt is rewritten. No model weights, probabilities, calibration values,
-- qualification thresholds, terminal authority, or execution controls change.
-- can_execute=false remains invariant; this migration creates no execution path.

alter table public.wow_mlb_forward_score_snapshots
  alter column model_timestamp set default clock_timestamp();

-- Preserve dry-run governance. This migration changes timestamp chronology only.
