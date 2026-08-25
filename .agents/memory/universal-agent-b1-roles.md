---
name: Universal Agent B1 role architecture
description: Six advisory role contracts (B1) — schemas, validators, registry, two-phase validation pattern
---

## Two-phase validator pattern

Every role validator calls `validate_role_advisory_output()` from `role_base.py`:
- **Phase 1**: delegates entirely to B0's `validate_output_contract()` — includes recursive forbidden governance key scan over the entire payload (advisory_findings included). Phase 1 catches ALL governance key smuggling at any depth.
- **Phase 2**: validates `advisory_findings` against role-specific closed schema (allowlist, required fields, types, enums). Phase 2 does NOT re-scan for forbidden keys — that is B0's responsibility.

**Why:** Avoids duplicating the forbidden key scanner. Tests prove sharing via `assertIs` on the function objects and `mock.patch` to verify call.

## Critical import: Lane

`Lane` class lives in `gate_engine.universal_agent.evidence_packet`, NOT in `agent_registry`. All B1 role REGISTRY_ENTRY definitions use:
```python
from gate_engine.universal_agent.evidence_packet import Lane
```

## Six role IDs and agent_ids

| Role ID | agent_id |
|---|---|
| DATA_SLATE_INTEGRITY | uac-data-slate-integrity-v1 |
| NEWS_STATUS | uac-news-status-v1 |
| MARKET_EXACT_LINE | uac-market-exact-line-v1 |
| SPORT_SPECIALIST | uac-sport-specialist-v1 |
| FAILURE_CONTRADICTION | uac-failure-contradiction-v1 |
| FINAL_REFRESH | uac-final-refresh-v1 |

All six use `Lane.UNKNOWN` (lane wiring is B2+). All `model_module=None` (model wiring is B2+).

## Registry isolation

`registry_b1.py` provides `build_b1_registry()` (fresh isolated) and `register_b1_roles(registry)` (inject into any). Does NOT mutate the B0 module-level REGISTRY singleton. Tests use isolated instances.

## Test mixin pattern

`_RoleAdversarialMixin` defines ~18 common adversarial tests inherited by all 6 role test classes. `_dict_field_for_nested_test` class attribute points at a known dict-type advisory_findings field for depth-2 governance injection tests. Roles without a top-level dict field in their minimal `valid_payload()` must either override `valid_payload()` to include one (see FinalRefresh) or leave `_dict_field_for_nested_test = ""` to trigger `self.skipTest` (News/Status, Market/Exact-Line).

## "UNKNOWN"/"MISSING" as explicit evidence states

All constrained enum fields include "UNKNOWN" (and where appropriate "MISSING") as valid values. Validators never reject these — they preserve absence of evidence explicitly rather than requiring fabricated values.

**Why:** Satisfies the "preserve missing/unknown evidence as explicit states" requirement; downstream roles can detect unknown inputs and route appropriately.
