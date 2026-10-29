# V17 production acceptance matrix (2026-10-29T13:40:00Z)

Environment blockers: RENDER_WEB_WORKER_SHA_MISMATCH, PRODUCTION_DB_BINDING_UNPROVEN

| Route | Scorer | Artifact certified | First failing stage | Exact blocker | Readiness |
|---|---|---|---|---|---|
| NFL_MONEYLINE | INVOKED | False | CALIBRATION | FORWARD_CALIBRATION_INSUFFICIENT_N | hold |
| NBA_MONEYLINE | NOT_INVOKED | n/a | MODEL | MODEL_UNAVAILABLE | hold |
| NCAAF_MONEYLINE | NO_ROWS | n/a | MODEL | MODEL_UNAVAILABLE | hold |

can_execute=false; dry-run only; non-authorizing.
