---
name: Source Provenance Freshness Auditor
description: Architecture of WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2 (Patch 4, P0 tier)
---

## Module: gate_engine/source_provenance/

Patch 4 of the P0 governance tier. Precedence 102. can_execute=False on all modules.

### Three design invariants
- **INVARIANT-1 (Freshness)**: NOT `now - retrieved_at`. Each `(fact_type, checkpoint)` has a FactPolicy specifying `freshness_basis` (PUBLISHED_AT / EFFECTIVE_AT / OBSERVED_AT / RETRIEVED_AT). retrieved_at is last-resort only.
- **INVARIANT-2 (Ceilings)**: SourceType does NOT impose universal ceiling caps. Ceiling is `FactPolicy.insufficient_source_ceiling` for the specific checkpoint, not a global per-source-class rule.
- **INVARIANT-3 (Conflicts)**: Two facts conflicting on `fact_value_hash` → MATERIAL_SOURCE_CONFLICT. Both records preserved; neither selected automatically. `conflicts_with` populated on each.

### Key types
- `SourceType`: OFFICIAL / PRIMARY_API / SPORTSBOOK_EXCHANGE / TRUSTED_SECONDARY / RECONSTRUCTED / PROXY / SCREENSHOT / OPERATOR_SUPPLIED / UNKNOWN
- `FreshnessBasis`: PUBLISHED_AT / EFFECTIVE_AT / OBSERVED_AT / RETRIEVED_AT
- `FreshnessStatus`: FRESH / STALE / EXPIRED / UNVERIFIABLE / POLICY_ABSENT
- `StructuredEvidence`: 20 evidence fields + audit fields

### Policy registry lookup priority
1. (fact_type, checkpoint) exact
2. ("*", checkpoint) wildcard fact_type
3. (fact_type, "*") wildcard checkpoint
4. ("*", "*") universal fallback

### Call sites (where auditSourceProvenance is invoked)
1. `gate_engine/universal_agent/audit_store.py::record_evidence_packet()` → `_audit_uac_evidence_provenance()` → checkpoint="uac_evidence_intake". Writes audit columns back to uac_evidence_packets via UPDATE.
2. `gate_engine/llp_stage2_tables.py::log_calibration_entry_pg()` → `_audit_calibration_entry_provenance()` → checkpoint="llp_calibration". Also writes first real data to llp_source_snapshots when source_snapshot_id is set.

Both hooks are **best-effort** (wrapped in try/except; never block the primary write).

### Schema changes
- `llp_source_snapshots`: 16 new provenance columns added to CREATE TABLE + 3 new indexes
- `uac_evidence_packets`: same 16 columns added via migration only
- `llp_calibration_ledger`: FK constraint added (NOT VALID) referencing llp_source_snapshots
- Migration: `run_provenance_migration(conn)` — ADD COLUMN IF NOT EXISTS, idempotent

### Why NOT VALID for the FK
llp_source_snapshots previously had no writes (table existed but was always empty). NOT VALID adds the FK without scanning/rejecting existing rows. Future inserts/updates are validated. Run VALIDATE CONSTRAINT once data is clean.

### Test file
`gate_engine/tests/test_source_provenance.py` — 91 tests T-SP-01 through T-SP-93.
