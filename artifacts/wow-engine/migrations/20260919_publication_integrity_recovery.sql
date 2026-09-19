-- V17 publication integrity recovery (recovery rebuild, 2026-09-19).
--
-- PROPOSED. Not applied by this commit. Per the engineering contract's git
-- rules (Class B: schema-affecting), this migration requires governed
-- review before being applied to the wow-engine-validation Supabase
-- project, and application must follow (not precede) deployment of the
-- application-layer changes it supports -- both already treat these
-- constructs as absent/best-effort, so applying this migration is additive
-- and order-independent, but the reverse (schema before code) has no
-- benefit and is not the intended sequence.
--
-- CONTEXT
-- Verified against wow-engine-validation on 2026-09-19: wow_predictions had
-- 2907 rows collapsing to 957 distinct exact theses (~3x duplicate
-- inflation from repeated scoring attempts hitting a plain INSERT with no
-- identity check). ledger.insert_prediction() now performs an
-- application-layer query-before-insert exact-identity check (see
-- ledger.py, _find_exact_duplicate), which stops new duplicates but cannot
-- close a race between two truly concurrent identical inserts, and cannot
-- retroactively merge the 957/2907 rows that already exist.
--
-- This migration adds:
--   1. A supporting (non-unique) index on the same exact-identity columns
--      the application-layer check already queries, so that check is an
--      index scan instead of a sequential scan as the table grows.
--   2. wow_prediction_thesis_v: a read-only view collapsing existing rows
--      to one (the earliest-scored) row per exact thesis, so calibration
--      and cohort-counting code can read distinct-thesis counts without
--      waiting on a data cleanup of the historical duplicates.
--
-- IT DOES NOT:
--   - add a UNIQUE constraint on the exact-identity columns. The existing
--     957/2907 duplication must be reviewed and deliberately deduplicated
--     (or explicitly accepted as immutable historical evidence) before a
--     UNIQUE constraint could be added without the migration itself
--     failing or silently dropping rows -- that data decision belongs to a
--     separate, explicitly reviewed migration, not this one.
--   - touch controlling_specialist. That column already exists and already
--     accepts values; the reason 2895/2907 existing rows are NULL was a
--     Python-layer gap (PredictionRow had no such field), now fixed in
--     ledger.py/engine.py/prop_discrete_engine.py/api.py/api_prod_market.py.
--     Existing NULL rows are immutable historical evidence and are not
--     backfilled by inference here.
--   - change any RLS policy, grant, or privilege.

create index if not exists wow_predictions_exact_identity_idx
    on public.wow_predictions (
        event_id,
        sport,
        market_type,
        stat_type,
        line,
        direction,
        source_snapshot_id
    );

-- player/team are nullable and excluded from the composite index (a partial
-- index per NULL-vs-non-NULL combination would be needed for a btree to help
-- the IS NULL branch of the application-layer check); this index still
-- narrows the scan to the same event/market/line/direction/snapshot before
-- the application layer does the final player/team comparison in memory.

create or replace view public.wow_prediction_thesis_v as
select distinct on (
    event_id, sport, market_type, stat_type, line, direction,
    coalesce(player, ''), coalesce(team, '')
)
    prediction_id,
    event_id,
    sport,
    market_type,
    stat_type,
    line,
    direction,
    player,
    team,
    source_snapshot_id,
    controlling_specialist,
    probability_publishable,
    calibrated_probability,
    calibrated_probability_lower_bound,
    created_at
from public.wow_predictions
order by
    event_id, sport, market_type, stat_type, line, direction,
    coalesce(player, ''), coalesce(team, ''),
    created_at asc;

comment on view public.wow_prediction_thesis_v is
    'One row per distinct exact thesis (earliest-scored) in wow_predictions. '
    'Use this view, not a raw wow_predictions row count, whenever counting '
    'distinct candidates toward a calibration/probation threshold -- a raw '
    'count double-counts repeated scoring attempts of the same thesis. '
    'Read-only; carries no RLS grant beyond what wow_predictions itself '
    'already exposes to the querying role.';
