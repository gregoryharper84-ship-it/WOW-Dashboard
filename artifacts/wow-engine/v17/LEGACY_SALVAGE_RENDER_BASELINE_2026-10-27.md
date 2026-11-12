# WOW V17 Legacy Salvage — Read-only production baseline receipt

Recorded: 2026-10-27 (Render connected workspace; read-only `list_services` + `list_deploys` responses).  
Parent: #2162 / WS0 #2167 / draft PR #2166.

## Production core deploys (observed by Render, NOT inferred from source)

| Service | Render ID | Latest live deploy ID | Live git commit | Status |
|---|---|---|---|---|
| WOW governed probability web | `srv-da7sa9gu01pc73brt80g` | `dep-dbge9k59el5s73e011og` | `f514d2e240f99847efe1a38af77e8b1e853f3385` | `live` |
| WOW agent worker | `srv-dapkbc0473hc73c1978g` | `dep-dbgec058hevc73ageqqg` | `f514d2e240f99847efe1a38af77e8b1e853f3385` | `live` |

Web and worker production deploys had exact **same commit SHA** in the returned Render evidence. The service records also show the web startup as `uvicorn api_ncaaf_acceptance:app` and worker as `celery -A agent_runtime.celery_app:celery_app worker --loglevel=INFO --concurrency=2`. Both are on `main`. The worker auto-deploy trigger was shown as off (manual/API deployment remains possible); the web trigger was commit.

**Important boundaries**: A `live` Render deploy attests to service deployment and SHA, *not* a valid scoring receipt, route registration, fitted model completeness, calibration or all-sport performance. The draft salvage PR has not been merged to main and is **not included** in the live SHA above.

## Follow-on runtime tests still required for full WS0 acceptance

- Fetch backend Action health/version and the actual certified model/capability manifest for each target sport/stat.
- Trace current model specialty route through `/score-pick-request`, scoring receipt, calibration/lower bound and `V17_TERMINAL_REDUCER`; do not infer from API mounted status.
- Independently confirm exact line, outcome-space/settlement and immutable pregame prediction receipt for target acceptance rows.
- Reconcile all 31 legacy sections with the live route, source status, and actual model adapter registration.
- Record `LIVE_GPT_EDITOR_SYNC` only after live editor save/reload plus authenticated Action acceptance; this Render deployment does not prove editor sync.

No model scoring attempted as part of this **read-only deployment baseline**. `can_execute=false` throughout.
