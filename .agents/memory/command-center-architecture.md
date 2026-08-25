---
name: WOW Command Center Phase 1 architecture
description: Federated orchestration/governance layer above the four WOW engines; module structure, invariants, and route map.
---

# WOW Sports Intelligence Command Center — Phase 1

**Status**: IMPLEMENTED, 61/61 tests passing, routes registered.
**Package**: `gate_engine/command_center/` (8 modules + __init__)
**Routes**: `POST /wow/cc/intake`, `POST /wow/cc/run`, `GET /wow/cc/health`, `GET /wow/cc/status/<run_id>`, `POST /wow/cc/reconcile`

## Invariants (enforced everywhere, never negotiable)
- `can_execute = False` — unconditional module constant
- `dry_run_only = True` — all runs are analytical only
- `KALSHI_RECOVERY_MODE = "ACTIVE"` — Kalshi pool always isolated

## Protocol (two-phase)
- **Phase A** (`/wow/cc/intake`): intake validation → routing manifest; no engine calls
- **Phase B** (`/wow/cc/run`): accepts candidates + optional engine_results dict; full orchestration

## Module layout
| Module | Role |
|---|---|
| `cc_labels.py` | Namespaced CC: labels, ALL_FAMILIES, CEILING_ORDER (monotonic rank index) |
| `candidate_intake.py` | make_envelope(), validate_batch(), extract_engine_label() |
| `market_router.py` | route_candidate() / route_batch() — strict 1:1 family assignment |
| `ceiling_resolver.py` | apply_ceiling_to_row() — can only move to more restrictive; upstream preserved |
| `shared_services.py` | run_all(): slate_integrity, cross_platform_exposure, calibration, failure_path, exact_line, final_refresh, row_completeness |
| `kalshi_isolation.py` | apply_recovery_mode_caps() → calls kalshi_engine.portfolio_governor; fallback local caps if import fails |
| `reconciliation.py` | reconcile_batch() — rules R-01 through R-10 |
| `orchestrator.py` | run_intake() + run_command_center() — main pipeline |

## Key design rules
- Each candidate routes to EXACTLY ONE family (PROP / LLP / KALSHI_SPORTS / KALSHI_WEATHER)
- Platform mismatch (e.g. platform=KALSHI declared as PROP) → CC:ROUTING_CONFLICT blocker
- Kalshi pool is fully isolated from Prop/LLP; portfolio_governor caps (max 2/day, max 1/event) applied
- CC labels (CC:*) are only emitted by the orchestration layer, never by engines
- Invalid candidates are included in output, never silently dropped
- Monotonic ceiling: final_label must be >= cc_ceiling in restrictiveness rank

**Why:** The four engines run independently; the CC layer provides the unified governance envelope, routing authority, and cross-engine monotonic ceiling without modifying any engine.

**How to apply:** When extending Phase 1, add modules to `gate_engine/command_center/`; never modify existing engine modules. Phase 2 will add database-backed run persistence (run_id → DB row) and upgrade `/wow/cc/status` from stateless to stateful.
