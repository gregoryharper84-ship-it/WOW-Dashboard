# Execution ledger — 2026-10-28-writer-epoch-drain.md

Base: 6b3c79508ebe8a60e9ab166d38fcbc10508c9942, existing owner-source #2188.

Ruling: use an isolated clone and publish only to the existing owner-source
branch with expected-head CAS. Never modify the App-owned integration branch.
This preserves App provenance; the cost is a fresh adoption/review cycle.

Ruling: no production schema or credential cutover until independent review
and genuine authority are established. Production read-only inspection proves
schema shape, not credential isolation. Activating only part of the fence
would stop legacy release or leave direct mutation bypasses.

Task 1: offline adapter RED 14 missing-method failures, then GREEN; CLI close
RED 4 failures, then GREEN. Focused baseline 56 passed; new focused 74 passed.
PostgreSQL WASM baseline reproduced missing close RPC; corrected migration
passed sequential close/drain/recovery/null/epoch assertions. Native PostgreSQL
test remains required in CI, not inferred from WASM.

Fresh reviewer identified inherited TRUNCATE as High: service_role can bypass
row triggers. Added explicit revokes and real role-privilege tests with ALL
default grants in the isolated fixture. service_role remains trusted for intent
UPDATE; no claim of RPC-only authority or universal production enforcement.

Initial root pytest attempt: collection stopped with 511 import errors, mainly
missing requests, fastapi, httpx and jwt. Dependencies are being installed;
this is not a green broad-suite result. Focused/adjacent run: 129 passed,
1 isolated-Postgres skip before final privilege/CLI additions.

Remaining: native PostgreSQL CI, exact source CI, real independent security /
database review; authenticated App re-adoption; credential inventory/restriction;
live route wiring; genuine CODEOWNER/QA/Release; protected merge and deployed
web+worker acceptance. Source proposal is not production closure.
