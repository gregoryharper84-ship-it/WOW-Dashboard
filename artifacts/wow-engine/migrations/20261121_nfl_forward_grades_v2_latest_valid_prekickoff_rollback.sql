-- Rollback for 20261121_nfl_forward_grades_v2_latest_valid_prekickoff.sql.
-- Removes only the V2 reconciliation view. The append-only V2 grade ledger is
-- retained as immutable evidence; V1 objects were never modified.
drop view if exists public.wow_nfl_forward_verified_grades_v2;
