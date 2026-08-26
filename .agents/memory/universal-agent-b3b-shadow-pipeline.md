---
name: Universal Agent B3B — offline MLB Moneyline shadow pipeline
description: B3B wires B3A adapter → DeterministicAdapterRunner → B2 orchestrator into a default-off shadow pipeline with no live LLM/API calls
---

## Three new files under gate_engine/universal_agent/shadow/

| Module | Responsibility |
|---|---|
| `__init__.py` | Package marker; re-exports public API |
| `deterministic_runner.py` | `DeterministicAdapterRunner` — wraps adapter role_payloads, returns them to orchestrator without any LLM/API call |
| `shadow_pipeline.py` | `run_shadow_pipeline()` + `ShadowPipeline` class + `ShadowPipelineResult` (frozen) + `ShadowPipelineStatus` |

## DeterministicAdapterRunner design

- Stores `role_payloads: dict[role_id → payload]` (keyed by role_id, e.g. "DATA_SLATE_INTEGRITY")
- `__call__(entry, packet)` looks up by `entry.role` (role_id), not `entry.agent_id`
- Raises `RuntimeError` (fail-closed) when role_id not in payloads
- Records call_log with `{agent_id, role_id, packet_id, snapshot_id}` per call
- `build_role_runners(registry)` → `{entry.agent_id: self for entry in registry.all_agents()}` — one instance handles all six agents

## Shadow pipeline flow (when enabled)

```
run_shadow_pipeline(row, run_id, *, _force_enabled=False)
  → if not enabled: DISABLED result (immediate return)
  → MlbMoneylineAdapter.adapt(row, run_id)
      AdapterInputError → ADAPTER_ERROR result (orchestrator never called)
  → DeterministicAdapterRunner(adapter_result.role_payloads)
  → build_b1_registry() (or _registry injection for tests)
  → det_runner.build_role_runners(registry)
  → run_orchestrator(packet, registry, role_runners, db_conn=db_conn)
  → map BundleStatus → ShadowPipelineStatus
  → ShadowPipelineResult (frozen)
```

## Default-off gate

`SHADOW_ENABLED = False` at module level. Only `_force_enabled=True` (testing escape-hatch) or setting `SHADOW_ENABLED = True` in the calling context enables it. Never changed at module load.

## ShadowPipelineStatus constants

COMPLETE / PARTIAL / FAILED (mirror BundleStatus) / DISABLED / ADAPTER_ERROR

## Key invariants proven by B3B tests

- `SHADOW_ENABLED = False` at module load — never changed here
- `can_execute = False` on all three shadow modules + ShadowPipeline class
- Same EvidencePacket Python object (id()) received by all 6 runners — proven by DeterministicAdapterRunner.call_log
- Forbidden governance keys in any role payload → GOVERNANCE_REJECTED → role not in accepted_findings → bundle not COMPLETE
- Missing runner for any agent_id → NO_RUNNER (fail-closed, not silently accepted)
- Contradictions (HIGH severity) → BundleStatus.PARTIAL → ShadowPipelineStatus.PARTIAL
- SCRATCHED starter row always triggers RULE-1 (HIGH: player OUT + SS dict assessment) → PARTIAL
- No app.py imports, no Flask routes, no anthropic/openai/requests/httpx anywhere in shadow package
- `accepted_findings` never contains FORBIDDEN_GOVERNANCE_KEYS
- Persistence: `persisted=True` only when db_conn provided; only uac_* tables written
- AdapterInputError always surfaces as ADAPTER_ERROR, never swallowed or propagated to orchestrator

## Contradiction triggers proven in B3B

| Row type | Rules fired | Bundle |
|---|---|---|
| Full PASS (CONFIRMED starter, PASS preflight) | None | COMPLETE |
| SCRATCHED starter (OUT) + full SS dict | RULE-1 (HIGH) + RULE-3 (HIGH from FC hard blockers) | PARTIAL |
| Governance key injected | GOVERNANCE_REJECTED | not COMPLETE |
| Runner missing for one agent | NO_RUNNER | not COMPLETE |

## docstring token-scan anti-pattern

The `test_shadow_pipeline_no_live_api` test does a plain substring scan (`token in source`). Docstring phrases like "No import from app.py" contain "from app" — triggers false positive. Use "No app.py import" phrasing instead.

## Test counts

- `tests/test_universal_agent_b3b.py`: 101 collected, 101 passed, 0 failed
- 15 test classes covering: DeterministicAdapterRunner, disabled gate, adapter error, full pass, degraded row, governance key rejection, missing runner, contradiction, packet identity, persistence, class interface, no-production-imports, invariants
