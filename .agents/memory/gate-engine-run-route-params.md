---
name: gate-engine/run route param wiring
description: which run_pipeline() kwargs the /gate-engine/run Flask route actually forwards from the request body
---

The `/gate-engine/run` route only forwards `raw_rows`, `target_date`, `enrichment`, and
`record_entries` from the JSON body into `run_pipeline()`. Other `run_pipeline()` kwargs
(`skip_data_contract`, `skip_health_gate`, `skip_settlement_check`) are NOT read from the
request body — passing them in a curl/API payload is silently ignored.

**Why:** caused confusion during manual verification of a patch — rows came back
`DATA_CONTRACT_FAIL` even with `"skip_data_contract": true` in the body, because the route
never reads that key at all (it's a pipeline-internal/test-only knob, not exposed over HTTP).

**How to apply:** when manually curling `/gate-engine/run` to verify a patch, don't rely on
`skip_*` body flags — supply full valid rows (all data-contract-required fields, e.g. `game`)
instead. If a future patch needs `skip_data_contract` exposed over HTTP, that's a deliberate
route change, not a bug fix.
