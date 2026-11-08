# WOW V17 dashboard

The default page submits an exact player prop to the canonical V17
`/score-pick-request` backend through the same-origin Express API server. It
displays native receipt IDs, blockers, publication/ranking flags and complete
JSON, and supports immutable receipt lookup. It supplies no model probability.
All execution remains disabled. Legacy pages are explicitly marked V16 and use
the separate Flask compatibility service and legacy ledgers.

Production build defaults to `/final-lock/` and needs no build-time environment
variables. From the repository root, using the installed workspace dependencies:

```sh
cd artifacts/final-lock
./node_modules/.bin/vite build --config vite.config.ts
cd ../api-server
node build.mjs
PORT=8080 NODE_ENV=production node dist/index.mjs
```

Configure server environment using `../api-server/.env.example` as a reference.
The application reads runtime environment variables; it does not automatically
load that example file. Set `WOW_V17_BACKEND_API_KEY` to the authorized backend
Action credential and `WOW_DASHBOARD_API_KEY` to a distinct dashboard access
credential. Neither belongs in a `VITE_*` variable. Enter only the dashboard
access key in the browser; it is retained in session storage for this tab.
Missing credentials return typed 401/503 responses. `DATABASE_URL` is optional
for V17; DB-backed legacy views return `LEGACY_DATABASE_NOT_CONFIGURED` when it
is absent.

Open `http://localhost:8080/final-lock/`. `BASE_PATH` must match between frontend
build and API-server runtime and end in `/` (for example `/` or `/dashboard/`).
API requests always use `/api`, independently of the UI base path. For Vite
development, the default UI port is 5173 and `/api` proxies to
`http://127.0.0.1:8080`; set `DASHBOARD_API_PROXY_URL` to change that local target.

The V17 proxy exposes only host contract, capabilities, exact prop scoring and
receipt lookup. It requires an HTTPS backend origin, refuses redirects and
withholds responses that grant execution or echo its credentials. Explicit
loopback HTTP fixtures require `WOW_V17_ALLOW_LOCAL_BACKEND=true`.

Keep the submitted request ID and row key when a scoring response times out.
Use **Recover submitted receipts** before any retry; the gateway never retries
scoring automatically. A receipt lookup that is unresolved or not found does
not prove an earlier score request stopped.

Run focused contract checks from the repository root:

```sh
./scripts/node_modules/.bin/tsx --test artifacts/api-server/test/*.test.ts artifacts/final-lock/test/*.test.ts
./node_modules/.bin/tsc --build lib/api-client-react lib/api-zod lib/db
./node_modules/.bin/tsc -p artifacts/api-server/tsconfig.json --noEmit
./node_modules/.bin/tsc -p artifacts/final-lock/tsconfig.json --noEmit
./node_modules/.bin/tsc -p artifacts/flask-scoring-api/tsconfig.json --noEmit
```
