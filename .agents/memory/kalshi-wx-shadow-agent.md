---
name: Kalshi WX shadow agent Step 10.1
description: SDK adapter for one Kalshi Weather forecast-context subagent; proves SDK → Step 9 validator path end-to-end; no routes, hooks, orchestrator, or ceiling interaction.
---

## Rule
`invoke_forecast_context_agent(city, date, run_id, *, sdk_client=None)` in
`gate_engine/kalshi_wx_shadow_agent.py` is the ONLY public entry point.
It always returns a `ShadowValidationResult` — never a raw dict, never None.

**Why:** The shadow pilot requires that model output NEVER escapes to callers
without passing through `validate_shadow_output`. This is the architectural
invariant the whole pilot depends on.

## Validator invariant (enforced structurally)
Every `return` statement in `invoke_forecast_context_agent` is a direct call to:
- `validate_shadow_output(payload)` — Step 9 schema validator
- `_call_failure(reason)` — SDK-level error (exception, unparseable JSON, no key)

Test T4 uses `ast.parse` + `ast.walk` to assert this at the AST level: every
`Return` node's value must be an `ast.Call` whose `.func` unparsed name is in
`{"validate_shadow_output", "_call_failure"}`. Any bare `return`, `return None`,
`return payload`, or `return {}` would fail T4.

## _call_failure semantics
`_call_failure(reason)` constructs `ShadowValidationResult` with:
- `passed=False`, `shadow_failure_only=True`
- `violation=ShadowSchemaViolation.WRONG_TYPE` (closest available enum member —
  no new enum was added; the WRONG_TYPE is used for SDK-level failures, with
  `failure_reason` prefixed `"AGENT_CALL_FAILURE: "` to distinguish from
  true schema type errors)
- `failure_path="$"`

## Client construction
`_build_client()` checks in order:
1. `AI_INTEGRATIONS_ANTHROPIC_API_KEY` + `AI_INTEGRATIONS_ANTHROPIC_BASE_URL`
   (Replit AI integrations proxy — preferred in this environment)
2. `ANTHROPIC_API_KEY` (direct key)

Both keys are present in the environment. Returns `None` if neither is set;
`invoke_forecast_context_agent` returns `_call_failure(...)` in that case.

## Model and scope
- Model: `claude-3-5-haiku-20241022`, max_tokens=1024
- Agent has NO external tools — read-only by construction (no tool schemas passed)
- System prompt explicitly lists the 11 forbidden key names and the 6 allowed
  `recommended_ceiling` values
- `advisory_only: true` is required in the system prompt instructions

## Testing pattern
All 4 tests pass a `mock_client` via `sdk_client=` param — no live API call.
Mock structure: `mock_client.messages.create.return_value` has
`.content[0].text = json.dumps(payload)`.

For SDK-raise tests: `mock_client.messages.create.side_effect = exc`.

## What is explicitly NOT here
- No Flask route
- No orchestrator
- No hooks
- No ceiling resolver interaction
- No additional subagents
- No changes to schema module or registry module
- No weather_scout_log writes

## Files
- `gate_engine/kalshi_wx_shadow_agent.py` — the adapter module
- `tests/test_kalshi_wx_shadow_agent.py` — 4 tests (T1–T4), all pass
