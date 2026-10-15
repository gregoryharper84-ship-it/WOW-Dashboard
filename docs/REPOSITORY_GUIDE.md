# Repository guide

This guide is an orientation map for contributors. It describes repository
ownership and declared configuration, not a live production audit. For
generation authority, use [generation status](wow/GENERATION_STATUS.md); for
production, editor-sync, and user-journey claims, use
[V17 production status](../artifacts/wow-engine/V17_PRODUCTION_STATUS.md) and
its cited acceptance evidence.

## System map

```text
WOW governance and contracts
        ↓
V17 request/scoring pipelines ──→ governed Supabase/PostgreSQL persistence
        ↑                                     ↑
market, sport, and evidence adapters       SQL schemas/migrations
        ↓
HTTP APIs and Custom GPT Action contracts
        ↓
independent tests, CI, and controlled Render deployment
```

The V17 runtime is the Python service under `artifacts/wow-engine`. The
deployment manifest at `/render.yaml` declares the Render web service root as
`artifacts/wow-engine`, starts `api_ncaaf_acceptance:app`, and references
Supabase configuration. The manifest is configuration evidence; verify the
deployed SHA, runtime health, schema, and route acceptance separately before
making production claims.

## Directory map

| Path | Responsibility | Notes |
| --- | --- | --- |
| `artifacts/wow-engine/` | Active Python V17 backend, request/scoring pipelines, sport-specific components, calibration, persistence, SQL, and tests. | Normal production runtime; see its README and V17 status documents. |
| `artifacts/wow-engine/v17/` | V17-specific contracts, integrations, scripts, schemas, and operational evidence. | Historical or research material is not active authority unless current governance says so. |
| `artifacts/api-server/` | TypeScript/Express API service and route adapters. | Separate service; do not assume it is the production scoring entry point. |
| `artifacts/mcp-server/` | Node.js Model Context Protocol server, including V17 HTTP/stdio interfaces and tests. | Supporting integration surface. |
| `artifacts/final-lock/`, `artifacts/flask-scoring-api/`, `artifacts/mockup-sandbox/` | Separate React/Vite application artifacts; the Flask artifact also contains legacy/reference API material. | Treat each as its own project; verify deployment and active ownership before changing or relying on it. |
| `artifacts/wow-odds-proxy/`, `artifacts/wow-odds-router/` | Odds-related supporting services. | Evidence/transport surfaces, not probability authority. |
| `lib/` | Shared TypeScript workspace packages: API client, OpenAPI spec, generated Zod types, and Drizzle database utilities. | Package names and dependencies are declared in each package manifest. |
| `docs/wow/` | WOW architecture, contracts, runbooks, patches, and engineering documentation. | Canonical contract copies and registry rules are documented under `docs/wow/contracts/`. |
| `scripts/` | Workspace scripts and validation utilities. | Check each script's own usage and owning workflow. |
| `.github/workflows/` | Pull-request checks, deployment, scheduled maintenance, and evidence workflows. | A green workflow proves only the checks it ran. |
| `attached_assets/` | Imported files, historical prompts/patches, screenshots, and supporting artifacts. | Historical evidence; not a substitute for current contracts or runtime verification. |

## Technologies and package boundaries

- **Python backend:** FastAPI/Uvicorn, Pydantic, NumPy, scikit-learn, pandas,
  Supabase, and pytest. Celery/Redis and PostgreSQL checkpoint packages support
  worker/control-plane components where enabled.
- **TypeScript workspace:** pnpm, TypeScript, Express, React, Vite, and Node.js.
  Shared packages are under `lib/`; service and UI packages are under
  `artifacts/`.
- **Persistence:** Supabase/PostgreSQL, with SQL schemas and migrations in the
  backend. The TypeScript database package uses Drizzle.
- **Automation:** GitHub Actions handles validation and operational workflows;
  Render configuration is in `render.yaml`.

There is no single application manifest for every runtime: the root pnpm
workspace coordinates JavaScript/TypeScript packages, while Python services
have their own dependency files. Install and run commands from the package
whose manifest owns the target.

## Contributor workflow and checks

Use a focused branch and begin with the owning component's README, package
manifest, tests, and workflow. The common checks declared in this repository
are:

```sh
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm run build
```

The root build runs typechecking and then builds workspace packages that define
a build script. For the V17 Python service, from `artifacts/wow-engine/`:

```sh
python -m pip install -r requirements.txt
python -m pytest -q test_pick_request_runtime.py
```

Replace the example test with the focused suite for the files changed. The
backend's complete test suite is large and CI shards it; inspect the relevant
workflow before selecting broader local checks. For the MCP service:

```sh
pnpm --filter @workspace/mcp-server test:v17
```

Not every workspace package defines a test or build script. Check its
`package.json` before assuming a root command covers it. CI workflows may also
provision ephemeral databases, queues, or other services, so local and CI
coverage are not always equivalent.

## Authority, status, and historical material

Read status in this order:

1. `docs/wow/GENERATION_STATUS.md` for active-generation authority.
2. `artifacts/wow-engine/V17_PRODUCTION_STATUS.md` for recorded V17 production,
   editor-sync, and user-journey status and their acceptance requirements.
3. The referenced deployment receipts, route checks, schema state, and live
   acceptance evidence for claims that require runtime proof.

Older patches, incident reports, snapshots, imported assets, and V16 documents
are retained for provenance, audit, compatibility, or regression coverage.
Do not bulk rewrite or delete them to simplify navigation. Prefer clear links
and explicit lifecycle labels; update a current-status document only when its
underlying evidence has actually been refreshed.

## Improvement opportunities

These are safe maintainability opportunities, not authorization to change
model or governance behavior:

1. Keep this map and the current-status pointers updated as service ownership
   changes.
2. Give each independently deployed service a short component README with its
   owner, entry point, dependency file, focused tests, and deployment evidence.
3. Add a lightweight index for dated historical reports and imported assets,
   while preserving their contents and provenance.
4. Consider a single documented validation entry point only if it can accurately
   dispatch to existing checks without hiding required service-specific or CI
   infrastructure.

Probability, calibration, publication, and execution gates remain governed by
the active V17 contracts; documentation or tooling improvements must not weaken
them. `can_execute=false` and dry-run-only behavior remain invariant.
