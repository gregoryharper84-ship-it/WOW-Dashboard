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

Native PostgreSQL CI at source 5a55d5a95b827353de4ecf4599b2755066b6b210:
run 40711568853/job 122897938326, 204 passed, signed and independently verified
test evidence. No test skip in that job. Explicit privilege regression was also
reproduced RED with inherited grants and GREEN with all 13 unwanted privileges
revoked in PostgreSQL WASM.

Final compatibility finding: the real legacy claim RPC uses INSERT epoch=1
ON CONFLICT UPDATE epoch=old+1. The INSERT trigger rejected that provisional
epoch before seeing the increment. Reproduced WRITER_EPOCH_CLOSED in PostgreSQL
WASM, then fixed by deferring an existing incident's admission decision to its
UPDATE trigger. Added native regression with the actual UPSERT shape. Fresh
native CI on the follow-up SHA is required; earlier 204-pass evidence does not
transfer. No additional reviewer approval is claimed.

Broad backend result: 7663 passed, 33 skipped, 1 failed. Exact failure:
v17/test_nfl_prop_boundary_integrity.py::test_provider_alias_conflict_is_typed_identity_unresolved
raises NFLPropHydrationError because required remote evidence is unavailable.
Reproduced the identical failure both on unchanged base 6b3c7950 in an isolated
worktree and on the corrected source (preloading pick_request_runtime as in
the broad run). No NFL probability/identity code was changed to hide it.

Obsolete App #2215 closed UNMERGED with durable superseded-source re-entry
comment 6472564150. Source #2188 and incidents #1739/#2136 remain open. This
frees its slot for the existing authenticated adopter after source CI passes;
it is neither a lease release nor a production repair.
