# V17 production acceptance matrix (2026-10-29T14:55:00Z)

Environment blockers: RENDER_WEB_WORKER_SHA_MISMATCH, PRODUCTION_DB_BINDING_UNPROVEN

| Route | Scorer | Artifact lifecycle | First failing stage | Exact blocker | Readiness |
|---|---|---|---|---|---|
| NFL_MONEYLINE | INVOKED | CHAMPION | CALIBRATION | FORWARD_CALIBRATION_INSUFFICIENT_N | hold |
| NBA_MONEYLINE | NOT_INVOKED | none | MODEL | MODEL_UNAVAILABLE | hold |
| NCAAF_MONEYLINE | NO_ROWS | none | MODEL | MODEL_UNAVAILABLE | hold |
| NBA_PROPS | NO_ROWS | none | MODEL | MODEL_UNAVAILABLE | hold |
| NFL_PROPS | INVOKED | PROSPECTIVE_CERTIFIED | PUBLICATION | FORWARD_CERTIFICATION_UNPROVEN | hold |

can_execute=false; dry-run only; non-authorizing.
