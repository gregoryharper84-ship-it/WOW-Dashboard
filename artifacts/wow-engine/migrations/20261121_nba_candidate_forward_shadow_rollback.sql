-- Rollback for 20261121_nba_candidate_forward_shadow.sql.
-- Removes only the grading view. The append-only capture ledger is retained as
-- immutable research evidence.
drop view if exists public.wow_nba_candidate_forward_verified_grades;
