---
name: Source Provenance Freshness Auditor
description: Architecture of WOW-PATCH-2026-08-14-SOURCE-PROVENANCE-FRESHNESS-AUDITOR-v2 (Patch 4, P0 tier)
---

## Module: gate_engine/source_provenance/

Patch 4 of the P0 governance tier. Precedence 102. can_execute=False on all modules.

### Three enforced design invariants
- **INVARIANT-1 (Freshness)**: NOT `now - retrieved_at`. Each `(fact_type, checkpoint)` has a FactPolicy specifying `freshness_basis` (PUBLISHED_AT / EFFECTIVE_AT / OBSERVED_AT / RETRIEVED_AT). retrieved_at is last-resort only.
- **INVARIANT-2 (Ceilings)**: SourceType does NOT impose universal ceiling caps. Ceiling is `FactPolicy.insufficient_source_ceiling` for the specific checkpoint, not a global per-source-class rule.
- **INVARIANT-3 (Conflicts)**: Two facts conflicting on `fact_value_hash` → MATERIAL_SOURCE_CONFLICT. Both records preserved; neither selected automatically. `conflicts_with` populated on each.

### SourceType normalized values (8 + UNKNOWN)
OFFICIAL / PRIMARY_API / SPORTSBOOK_EXCHANGE / TRUSTED_SECONDARY / RECONSTRUCTED / PROXY / SCREENSHOT / OPERATOR_SUPPLIED / UNKNOWN.
SOURCE_TYPE_NORMALIZER maps 60+ legacy source_grade.py keys → canonical enum.

### Policy registry lookup priority
1. (fact_type, checkpoint) exact  2. ("*", checkpoint)  3. (fact_type, "*")  4. ("*", "*") fallback.
Wildcard ("*","*") policy_id=WILDCARD_UNIVERSAL; never returns None in practice.

### FactPolicy fields
freshness_basis, max_age_seconds, accepted_source_types (frozenset[SourceType]),
stale_ceiling (str|None), insufficient_source_ceiling (str|None).
Freshness tiers: FRESH ≤ max_age, STALE ≤ 3×max_age, EXPIRED > 3×max_age.
UNVERIFIABLE when required timestamp is absent (not STALE — different failure mode).

### Call sites (auditSourceProvenance is invoked here)
1. `gate_engine/universal_agent/audit_store.py::record_evidence_packet()` → `_audit_uac_evidence_provenance()` → checkpoint="uac_evidence_intake". Writes 10 audit columns back to uac_evidence_packets via UPDATE.
2. `gate_engine/llp_stage2_tables.py::log_calibration_entry_pg()` → `_audit_calibration_entry_provenance()` → checkpoint="llp_calibration". Also writes first real rows to llp_source_snapshots when source_snapshot_id is set (fixes the perpetually-empty table).

Both hooks are **best-effort** — wrapped in bare `try: ... except Exception: pass`. Never block the primary write.

### Schema changes
- `llp_source_snapshots`: 16 new provenance columns in CREATE TABLE IF NOT EXISTS + 3 indexes
- `uac_evidence_packets`: same 16 columns via migration only (existing table, no DDL change in definition)
- `llp_calibration_ledger`: FK constraint `fk_llp_calibration_source_snapshot` added NOT VALID (idempotent DO block)
- `schema_migration.run_provenance_migration(conn)`: ADD COLUMN IF NOT EXISTS for both tables; called from ensure_all_tables()

### Why NOT VALID for the FK
llp_source_snapshots had no writes before this patch (table existed but always empty). NOT VALID avoids scanning/rejecting existing orphaned rows. Future inserts/updates are validated. Run VALIDATE CONSTRAINT once data is clean.

### Tests
`gate_engine/tests/test_source_provenance.py` — 91 tests T-SP-01..T-SP-93; all pass.
Includes isolation tests confirming Command Center / SkillOrchestrator / moneyline_probability / llp_governance.py not touched.
