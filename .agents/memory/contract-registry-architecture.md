---
name: Contract & Authority Registry Architecture
description: Schema, location, and constraints for docs/wow/contracts/registry.yaml — the WOW authority registry introduced in #249.
---

# Contract & Authority Registry Architecture

## Files
- Registry YAML: `/home/runner/workspace/docs/wow/contracts/registry.yaml`
- Canonical copies: `/home/runner/workspace/docs/wow/contracts/canonical/` (20 .md files)
- Test: `artifacts/flask-scoring-api/gate_engine/tests/test_contract_registry.py` (25 tests)
- REPO_ROOT for the test: `/home/runner/workspace` (4 levels up from the test file)

## Schema (required fields on every contract entry)
`canonical_contract_id`, `source_filename`, `canonical_path`, `sha256`, `version`, `declared_status`, `project_authority_status`, `lane`, `precedence`, `supersedes`, `effective_date`, `can_execute`, `notes`

## Valid declared_status / project_authority_status values
`ACTIVE`, `EMERGENCY_ACTIVE`, `TEST_ONLY`, `RESEARCH_ONLY`, `PROBABILITY_ONLY`, `GOVERNANCE_DOCUMENT`, `PROPOSED`, `UNRESOLVED_AUTHORITY`

Note: `OUTRIGHT_WIN_PROBABILITY_ONLY` is NOT a valid status — use `PROBABILITY_ONLY` + `lane: OUTRIGHT_WIN_PROBABILITY_ONLY`.

## Promotion rules (enforced by T6 test_no_unsafe_promotion)
- `TEST_ONLY` → only `TEST_ONLY`
- `RESEARCH_ONLY` → only `RESEARCH_ONLY`
- `PROBABILITY_ONLY` → only `PROBABILITY_ONLY` or `UNRESOLVED_AUTHORITY`
- `PROPOSED` → only `PROPOSED` or `UNRESOLVED_AUTHORITY`
- `ACTIVE` → only `ACTIVE` or `UNRESOLVED_AUTHORITY`
- `EMERGENCY_ACTIVE` → only `EMERGENCY_ACTIVE` or `UNRESOLVED_AUTHORITY`
- `UNRESOLVED_AUTHORITY` → only `UNRESOLVED_AUTHORITY`

**Why:** Prevents TEST_ONLY/RESEARCH_ONLY/PROBABILITY_ONLY skills from being silently promoted to live-execution status.

## SHA-256 rules
- sha256 = hash of the canonical file at canonical_path (not the source ZIP)
- null sha256 is allowed when canonical_path is null
- UNRESOLVED_AUTHORITY with source_filename=null → sha256 must be null
- UNRESOLVED_AUTHORITY with sha256=null → notes must reference an UNRESOLVED-NNN id

## Unresolved authority items (as of #249)
- UNRESOLVED-001: MLB patches 010-013 not in governance.py _PATCH_REGISTRY
- UNRESOLVED-002: KALSHI_RECOVERY_MODE hardcoded constant lacks formal emergency patch
- UNRESOLVED-003: PATCH-014/015 numbering collision (07-30 patch vs. slip consistency series)

## Source ZIPs (all in attached_assets/)
- WOW-v16-Skills-Pack_1783803767126.zip: sha `90f1f211...`
- WOW-2026-07-30-WNBA-MLB-Patch-Pack_1785421082163.zip: sha `f524c109...`
- LLP_v16_Upgrade_2026-08-01_1785628653866.zip: sha `df532e49...`

## Key classifications (#249 required)
| Contract | declared_status | project_authority_status | lane |
|---|---|---|---|
| MLB 1IP skill | TEST_ONLY | TEST_ONLY | TEST_ONLY |
| MLB pitcher failure-path | RESEARCH_ONLY | RESEARCH_ONLY | RESEARCH_ONLY |
| Slip optimizer | PROBABILITY_ONLY | PROBABILITY_ONLY | PROBABILITY_ONLY |
| MLB game-win expert | PROBABILITY_ONLY | PROBABILITY_ONLY | PROBABILITY_ONLY |
| LLP moneyline expert | PROBABILITY_ONLY | PROBABILITY_ONLY | OUTRIGHT_WIN_PROBABILITY_ONLY |
| MLB patches 010-013 | ACTIVE | UNRESOLVED_AUTHORITY | RESEARCH_ONLY |
| Kalshi Recovery/Combo | EMERGENCY_ACTIVE | UNRESOLVED_AUTHORITY | GOVERNANCE |
| Cross-sport selector | PROPOSED | PROPOSED | ANALYTICAL_SHADOW_MODE |
