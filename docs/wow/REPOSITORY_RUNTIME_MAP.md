# WOW repository runtime map

**Scope:** repository navigation and source classification. **Checked against GitHub `main` and the connected Render service on 2026-10-15.** This is not a live-model, editor, deployment, or release attestation. Do not use the date or any snapshot here as proof of current production health.

## Canonical authority

- Product: `WOW_BETTING_ENGINE`; V17 is active; V16 is legacy/superseded. See [`GENERATION_STATUS.md`](GENERATION_STATUS.md).
- Sporting probabilities come only from the appropriate governed fitted specialist; global terminal authority is `V17_TERMINAL_REDUCER`. Neither source evidence, market odds, ChatGPT, nor a compatibility service may substitute probabilities.
- `can_execute=false` and `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` are immutable. No wagers or market orders are authorized.
- Source authority is protected GitHub `main`; operational service is Render with governed Supabase state. **Source, CI, deployed SHA, GPT-editor parity, and end-to-end acceptance are different attestations.**

## Production entry point and ownership

| Concern | Canonical source / entry point | Notes |
| --- | --- | --- |
| Production Python API | `artifacts/wow-engine/api_ncaaf_acceptance.py` | Render runs `uvicorn api_ncaaf_acceptance:app --host 0.0.0.0 --port $PORT` from `artifacts/wow-engine/`; despite the filename, this app mounts V17 capabilities. Trace installed routes from here. |
| V17 contracts, pipeline, integrations | `artifacts/wow-engine/v17/` | V17 additions and contracts; not a separate standalone production service. |
| Production service **declaration** | `render.yaml` | Desired Blueprint source may differ from actual Render service. Do not apply Blueprint or infer live plan/auto-deploy state from it. |
| Production service **observation** | Render `wow-governed-probability-engine` (`srv-da7sa9gu01pc73brt80g`) | Confirm LIVE deploy SHA, plan and auto-deploy mode in Render independently of `render.yaml`. Release-control drift is tracked in [#1752](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1752). |
| GPT host contract | `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml` and `openapi.llp-team-engine.v17.yaml` | Editor installation/sync requires separate acceptance; Git content does not prove current GPT Action configuration. |
| Evidence/status documents | `artifacts/wow-engine/V17_PRODUCTION_STATUS.md` | Status pointer, not automatically refreshed live attestation; check timestamp and corroborate live claims. |
| Worker / release governance | `.github/workflows/` and `artifacts/wow-engine/agent_runtime/` | Never treat queued workflow, passing check, or deployment request as production verification. |

## Other source trees: separate manifests and distinct authority

| Path | Navigation guidance |
| --- | --- |
| `artifacts/api-server/` | Separate Node/Express API artifact. Its `package.json` governs its build; not the Render V17 entry point. |
| `artifacts/mcp-server/` | Independent Node MCP server (`src/v17_http.js`, `src/v17_stdio.js`); presence does not prove external MCP deployment. |
| `artifacts/flask-scoring-api/` | Compatibility/historical Flask-based surface with still-active tests and CI contracts. **Do not delete, assume inert, or repoint without usage proof.** |
| `artifacts/final-lock/`, `artifacts/mockup-sandbox/` | Independent front-end artifacts, not proof of one canonical deployed V17 UI. |
| `artifacts/wow-odds-proxy/`, `artifacts/wow-odds-router/` | Odds support; never promote odds to fitted sporting probability authority. |
| `lib/` | Shared TypeScript packages. Use root `pnpm` and package-specific manifests. |
| `attached_assets/` | Imported/historical material; preserve provenance. |

## Test and build entry points

Run from a clean checkout with approved, synthetic/local test configuration. None of these commands deploys production:

```bash
# Python V17 backend: local focused tests (full coverage is CI-sharded)
cd artifacts/wow-engine
python -m pytest -q tests/<relevant_test_file>.py

# TypeScript workspace: from repository root
pnpm run typecheck
pnpm run build

# MCP component: from repository root
pnpm --dir artifacts/mcp-server run test:v17
```

- `.github/workflows/wow-engine-verify.yml`: Python engine sharded regression and durable-agent integration checks.
- `.github/workflows/wow-verify.yml`: multi-artifact governed checks, including Flask compatibility. Green CI is not live deployment proof.
- `.github/workflows/wow-v17-render-production-deploy.yml`: protected release handoff; **do not dispatch or bypass while release idempotency, independent checks and authorization are on HOLD**. See [#1759](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1759).
- Service-specific `requirements.txt` and `package.json` govern dependencies. Root `package.json` does not install the Python production runtime.

## Source-to-production verification checklist

1. Record exact protected `main` SHA, classification (A/B/C), provenance and independent reviewer/QA/Release decisions.
2. Confirm exact-head required CI and focused, negative, regression, adjacent-lane and applicable security/concurrency coverage.
3. Reconcile exact Render release service ID and deployed **LIVE commit SHA** with approved source SHA; reject stale pointers and ambiguous provider acknowledgments.
4. Independently verify health, routes, typed scorer failures, state persistence/reconciliation and the actual governed user journey. Separately attest production GPT editor/Action parity.
5. Persist immutable evidence and maintain owner/next action for unresolved incidents. Merge or healthy `/health/live` is not `FIXED_AND_VERIFIED`.

**Lifecycle note:** When records disagree, current provider evidence and protected contracts take precedence over a historical status snapshot. Protected release workflow changes require designated independent security/CODEOWNER/QA/Release authorization.
