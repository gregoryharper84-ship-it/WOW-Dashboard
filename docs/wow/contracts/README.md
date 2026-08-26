# WOW Authoritative Contract Documents

This directory holds the authoritative WOW skill and contract documents. These are the binding authority for all patch decisions. The `wow-replit-patch-governor` skill references these documents in the `binding_authority` field of every build-packet contract.

**Registry file:** `registry.yaml` — the single source of truth for all contract metadata, SHA-256 hashes, declared statuses, and project authority statuses.

**Canonical copies:** `canonical/` — immutable copies of all WOW skill and patch documents, preserved from their source ZIPs and attached_assets files. Never edit files in `canonical/` — they are integrity anchors for SHA-256 verification.

---

## Lifecycle rules

### Source-declared status vs. project authority status

Every registry entry has two separate status fields:

| Field | Meaning |
|-------|---------|
| `declared_status` | Exactly what the source document text says |
| `project_authority_status` | What the project has explicitly confirmed in production |

**These are never merged.** A document can be `declared_status: ACTIVE` but `project_authority_status: UNRESOLVED_AUTHORITY` if its activation record cannot be confirmed from the supplied documents.

### Promotion rules (enforced by `test_contract_registry.py`)

| declared_status | Allowed project_authority_status |
|----------------|----------------------------------|
| `TEST_ONLY` | `TEST_ONLY` only |
| `RESEARCH_ONLY` | `RESEARCH_ONLY` only |
| `PROBABILITY_ONLY` | `PROBABILITY_ONLY` or `UNRESOLVED_AUTHORITY` |
| `PROPOSED` | `PROPOSED` or `UNRESOLVED_AUTHORITY` |
| `ACTIVE` | `ACTIVE` or `UNRESOLVED_AUTHORITY` |
| `EMERGENCY_ACTIVE` | `EMERGENCY_ACTIVE` or `UNRESOLVED_AUTHORITY` |
| `GOVERNANCE_DOCUMENT` | `GOVERNANCE_DOCUMENT` only |

**TEST_ONLY, RESEARCH_ONLY, and PROPOSED documents may never be promoted to ACTIVE without a new explicit patch record.** Any such promotion is a registry integrity violation caught by the test suite.

---

## Current unresolved authority items

Three items require resolution before they can be treated as confirmed:

| ID | Description |
|----|-------------|
| UNRESOLVED-001 | MLB Pitcher Failure-Path patches 010–013 declared ACTIVE but no source document with those IDs found in supplied files |
| UNRESOLVED-002 | Kalshi Recovery and Combo Governance patch declared EMERGENCY_ACTIVE but no source document found |
| UNRESOLVED-003 | PATCH-014/PATCH-015 ID collision between `WOW-PATCH-2026-07-23-SLIP-CONSISTENCY` and `WOW-PATCH-2026-07-30-WNBA-MLB-GOVERNANCE` |

---

## Registry index

| canonical_contract_id | Lane | declared_status | project_authority_status |
|-----------------------|------|----------------|--------------------------|
| WOW-PATCH-2026-08-01-LLP-SLATE-INTEGRITY | llp | ACTIVE | ACTIVE |
| WOW-LLP-V16-MANIFEST | llp | GOVERNANCE_DOCUMENT | GOVERNANCE_DOCUMENT |
| WOW-LLP-SLATE-INTEGRITY-EXPERT-SKILL | llp | ACTIVE | ACTIVE |
| WOW-LLP-DYNAMIC-CALIBRATION-EXPERT-SKILL | llp | ACTIVE | ACTIVE |
| WOW-LLP-FAILURE-PATH-EXPERT-SKILL | llp | ACTIVE | ACTIVE |
| WOW-LLP-FINAL-REFRESH-GOVERNOR-SKILL | llp | ACTIVE | ACTIVE |
| WOW-LLP-MARKET-NORMALIZATION-EXPERT-SKILL | llp | ACTIVE | ACTIVE |
| WOW-LLP-MONEYLINE-PROBABILITY-EXPERT-SKILL | llp | PROBABILITY_ONLY | PROBABILITY_ONLY |
| WOW-LLP-ACTIVATION-PROMPT-2026-08-01 | llp | GOVERNANCE_DOCUMENT | GOVERNANCE_DOCUMENT |
| WOW-LLP-REGRESSION-TESTS-2026-08-01 | llp | GOVERNANCE_DOCUMENT | GOVERNANCE_DOCUMENT |
| WOW-PATCH-2026-07-30-WNBA-MLB-GOVERNANCE | mlb_wnba | ACTIVE | ACTIVE |
| WOW-MLB-PITCHER-FAILURE-PATH-EXPERT-SKILL-V2 | mlb | RESEARCH_ONLY | RESEARCH_ONLY |
| WOW-CROSS-TICKET-EXPOSURE-GOVERNOR-SKILL | cross_slip | ACTIVE | ACTIVE |
| WOW-WNBA-COMPOSITE-PROP-EXPERT-SKILL | wnba | ACTIVE | ACTIVE |
| WOW-SLIP-PROBABILITY-OPTIMIZER-SKILL-V3-WNBA-PACK | slip | PROBABILITY_ONLY | PROBABILITY_ONLY |
| WOW-REGRESSION-TESTS-2026-07-30 | mlb_wnba | GOVERNANCE_DOCUMENT | GOVERNANCE_DOCUMENT |
| WOW-PATCH-2026-07-23-SLIP-CONSISTENCY | slip | ACTIVE | ACTIVE |
| WOW-V16-SLIP-CONSISTENCY-INTEGRATION-CHECKLIST | slip | GOVERNANCE_DOCUMENT | GOVERNANCE_DOCUMENT |
| WOW-SLIP-PROBABILITY-OPTIMIZER-SKILL-V3-DIRECT | slip | PROBABILITY_ONLY | PROBABILITY_ONLY |
| WOW-SLIP-PROBABILITY-OPTIMIZER-V3-SKILL-ALT | slip | PROBABILITY_ONLY | PROBABILITY_ONLY |
| WOW-MLB-FIRST-INNING-PITCH-COUNT-EXPERT-SKILL-V3 | mlb | TEST_ONLY | TEST_ONLY |
| WOW-REPLIT-HANDOFF-2026-08-01-POSTMORTEM | mlb_wnba_slip | ACTIVE | ACTIVE |
| WOW-PATCH-2026-08-04-OUTS-MORE | mlb | ACTIVE | ACTIVE |
| WOW-PATCH-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR | cross_sport | PROPOSED | UNRESOLVED_AUTHORITY |
| WOW-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR-SKILL | cross_sport | PROPOSED | UNRESOLVED_AUTHORITY |
| WOW-MLB-PITCHER-FAILURE-PATH-PATCHES-010-013 | mlb | ACTIVE | UNRESOLVED_AUTHORITY |
| WOW-KALSHI-RECOVERY-COMBO-GOVERNANCE | kalshi | EMERGENCY_ACTIVE | UNRESOLVED_AUTHORITY |
| WOW-ZIP-LLP-V16-UPGRADE-2026-08-01 | llp | GOVERNANCE_DOCUMENT | GOVERNANCE_DOCUMENT |
| WOW-ZIP-WNBA-MLB-PATCH-PACK-2026-07-30 | mlb_wnba | GOVERNANCE_DOCUMENT | GOVERNANCE_DOCUMENT |
| WOW-ZIP-V16-SKILLS-PACK | general | GOVERNANCE_DOCUMENT | UNRESOLVED_AUTHORITY |
| WOW-ZIP-FANTASY-SCORE-SUPPORT-V0-1-0 | fantasy | GOVERNANCE_DOCUMENT | UNRESOLVED_AUTHORITY |

---

## How to add a contract document

1. Place the canonical copy in `canonical/` with a descriptive filename.
2. Calculate SHA-256: `sha256sum canonical/<filename>.md`
3. Add an entry to `registry.yaml` with all required fields.
4. Add it to the index table in this README.
5. Run `python3 -m pytest gate_engine/tests/test_contract_registry.py -q` — must pass.
6. Reference the `canonical_contract_id` in the `binding_authority` field of any patch contract.

## How to resolve an UNRESOLVED_AUTHORITY entry

1. Obtain the source document.
2. Verify its SHA-256 matches what the authority document describes.
3. Copy it to `canonical/`.
4. Update the registry entry: set `sha256`, `canonical_path`, and `project_authority_status`.
5. Remove it from the unresolved_authority_items list in `registry.yaml`.
6. Re-run the validation tests.
7. Commit with the patch ID that authorizes the resolution.

## What does NOT belong here

- Engineering process documents → `docs/wow/runbooks/`
- Architecture decisions → `docs/wow/architecture/`
- Test fixtures or sample data → `gate_engine/tests/`
