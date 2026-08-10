---
name: Universal Agent B2 orchestrator architecture
description: B2 deterministic orchestration layer — five new modules connecting B0 infrastructure and B1 six advisory roles
---

## Five new B2 modules

| Module | Responsibility |
|---|---|
| `role_runner.py` | `RoleRunnerStatus` (7 constants) + `MockRoleRunner` (preset-based, tracks packet identity) |
| `role_result.py` | `RoleResult` frozen dataclass (9 fields); `.accepted`, `.effectively_accepted`, `.failed` properties |
| `contradiction_detector.py` | 4 deterministic rules; `detect_contradictions()` pure function → sorted tuple |
| `bundle_assembler.py` | `EvidenceBundle` frozen dataclass; `assemble_bundle()` pure function |
| `orchestrator.py` | `run_orchestrator()` main entry; `B1_ROLE_IDS` tuple; `_ROLE_VALIDATORS` dict; `OrchestratorResult` |

## Key wiring decisions

**Packet identity**: The SAME `EvidencePacket` Python object (`id()`) is passed to every runner. Tests verify with `MockRoleRunner.packet_ids_seen()` — all 6 calls return the same integer.

**Pre-hook tool name**: `entry.allowed_capabilities[0]` is used as the `tool_name` in boundary pre/post hooks. Each B1 entry has exactly one capability (e.g. `"emit_data_slate_integrity"`), so this is always in the agent's allowlist.

**Fail-closed chain**: Runner exception → RUNNER_FAILED. Non-dict output → RUNNER_FAILED. Governance key in output → GOVERNANCE_REJECTED (B0 post-hook). Wrong B1 schema → INVALID. No runner registered → NO_RUNNER. BOUNDARY_BLOCKED if pre-hook blocks.

**None of these statuses can become ACCEPTED in the bundle.**

**Resumability**: Work unit ID = `"{snapshot_id}:{agent_id}"`. `mark_work_completed` is called ONLY for ACCEPTED results. Failed roles (any non-ACCEPTED) are NOT marked → they are retried on resume. SKIPPED_RESUMED roles are not re-persisted.

**SKIPPED_RESUMED in bundle**: Treated as `effectively_accepted` for bundle status computation. `advisory_findings=None` (not reloaded from DB at B2). `accepted_findings` in bundle only contains truly ACCEPTED roles.

**Bundle status rules**:
- COMPLETE: all expected role_ids in `effectively_accepted` set AND no HIGH-severity contradiction
- PARTIAL: ≥1 effectively accepted, but not all, OR any HIGH contradiction
- FAILED: zero effectively accepted

## Contradiction detection rules (4)

| Rule ID | Trigger | Severity |
|---|---|---|
| RULE-1-PLAYER-OUT-POSITIVE-ASSESSMENT | NEWS_STATUS.player_status=="OUT" + SPORT_SPECIALIST.statistical_assessment not in exclusion set | HIGH |
| RULE-2-STALE-DATA-LINE-CONFIRMED | DATA_SLATE_INTEGRITY.data_freshness_status=="STALE" + MARKET_EXACT_LINE.line_confirmed==True | MEDIUM |
| RULE-3-FAILURE-HIGH-SEVERITY | FAILURE_CONTRADICTION.contradiction_detected==True + contradiction_severity=="HIGH" | HIGH |
| RULE-4-FINAL-REFRESH-COMPLETE-WITH-MISSING | FINAL_REFRESH.all_roles_completed==True + non-empty missing_role_ids | MEDIUM |

Rules run only on ACCEPTED roles (SKIPPED_RESUMED have no in-memory findings). Output is sorted by rule_id for determinism.

## Test counts

- `test_universal_agent_b2.py`: non-DB, 6 test classes, covers all 6 required properties
- `test_universal_agent_b2_db.py`: DB integration, 2 test classes (D3 Persistence + D4 Resumability)
- Combined B0+B1+B2 focused suite: 475 passed, 3 skipped (same B1 mixin skips as before), 24 subtests

**Why:** Rule 1's `statistical_assessment` is typed as `dict` in SPORT_SPECIALIST — a dict is never equal to the exclusion strings (UNKNOWN/MISSING/NEGATIVE_OUTLOOK), so Rule 1 fires whenever player_status=="OUT" and statistical_assessment is any dict. This is intentional and tested.
