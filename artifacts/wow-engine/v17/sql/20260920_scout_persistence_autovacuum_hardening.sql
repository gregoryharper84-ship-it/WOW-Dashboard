-- R1 operational hardening for the append-only/idempotent Scout evidence tables.
-- Repeated governed persistence replays can create dead tuples from conflict
-- updates faster than PostgreSQL's default 20% autovacuum threshold notices
-- them. Keep cleanup proportional to the write workload without changing any
-- row, identity, governance, probability, or execution semantics.

alter table wow_scout.source_snapshots set (
  autovacuum_vacuum_scale_factor = 0.01,
  autovacuum_vacuum_threshold = 1000,
  autovacuum_analyze_scale_factor = 0.02,
  autovacuum_analyze_threshold = 1000
);

alter table wow_scout.candidate_source_links set (
  autovacuum_vacuum_scale_factor = 0.01,
  autovacuum_vacuum_threshold = 1000,
  autovacuum_analyze_scale_factor = 0.02,
  autovacuum_analyze_threshold = 1000
);
