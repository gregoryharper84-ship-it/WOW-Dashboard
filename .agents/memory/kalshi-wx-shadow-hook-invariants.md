---
name: Kalshi WX shadow hook and shadow_failure_only invariants
description: Two non-obvious invariants: pre/post hooks see the same dict; shadow_failure_only semantics differ by layer.
---

## Invariant 1 — Pre-hook and post-hook scan the same dict

Inside `_run_single_tool_subagent`, the pre-hook and post-hook both inspect `tool_input_dict` (the model's tool use input captured from the SDK response). They run the same forbidden-key scan. This means:

- A forbidden key in the model's output is **always caught by the pre-hook first**.
- The post-hook's non-blocking contract (violations recorded, `success` still True) is unreachable via the subagent loop with a forbidden key in the real flow.
- To test the post-hook's non-blocking behavior, either:
  1. Call `post_tool_use_hook()` directly on `CapabilityBoundary`, or
  2. Monkey-patch `post_tool_use_hook` on the boundary instance to return a failure on a clean dict.

**Why:** Both hooks scan the same `input_schema`-produced dict because "executing" a shadow tool = capturing the model's structured input, not producing a separate output.

## Invariant 2 — `shadow_failure_only` semantics differ by layer

`shadow_failure_only=True` is the invariant for **client-level gate failures** only:
- Gate 1: flag-off → `SHADOW_AGENT_DISABLED`
- Gate 2: authority violation → `SHADOW_CLIENT_AUTHORITY_VIOLATION`
- Gate 3: missing SDK client → `NO_SDK_CLIENT`
- Gate 4 import error: `ORCHESTRATOR_IMPORT_ERROR`
- Gate 4 uncaught exception: `ORCHESTRATOR_ERROR`

When the **orchestrator runs** and produces a schema-valid BLOCKED payload (all subagents failed but the BLOCKED payload passes `validate_shadow_output`), `validate_shadow_output` returns `SHADOW_PASS`, which has `shadow_failure_only=False`. This is correct — the pilot completed a run.

**Why:** `BLOCKED` is a valid `status` in the shadow schema. `validate_shadow_output` returns `SHADOW_PASS` singleton on any schema-valid payload, regardless of status. Tests asserting `shadow_failure_only=True` for orchestrator-delegation scenarios are wrong.

**How to apply:** Only assert `shadow_failure_only=True` in tests that exercise gate 1–4 failures. For orchestrator paths, assert only that the result is a `ShadowValidationResult` (not None, not dict).
