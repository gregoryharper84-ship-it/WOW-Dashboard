# P0 #1834 — Atomic Render release fence candidate (OFFLINE ONLY)

**Integration target:** Copilot-owned PR #1834 at initial head `e80fc36753e9a7b2e3b6fc1843b5028f1248f9fe`.
No changes to production or to the existing protected deployment workflow.
Do **not** apply this migration or enable the adapter without governance.

## Design

- Private, non-Data-API-exposed `wow_release_control` schema. No privileged grants or secrets.
- `service_fence` locks the **service**, not the GitHub run or commit.
- One unresolved intent per service is additionally enforced by a PostgreSQL partial unique index.
- An expired lease never clears an unresolved `RESERVED`, `POST_UNCERTAIN`, `PROVIDER_ACK`, or `LIVE_OBSERVED` intent.
- A previously used service/SHA can never be reused for another deployment POST.
- `reserve_single_post` transitions to `POST_UNCERTAIN` before any network write; lost ACK is observation-only, never retry.
- Immutable intent identity / monotonic states enforced by a database trigger.
- A separate independent verification decision is required to transition a `LIVE_OBSERVED` intent to `VERIFIED`; no automatic acceptance exists here.
- No production writer/DB identity has been attested and no credential or deployment mutation is authorized.

## Release blockers that this candidate intentionally does NOT satisfy

1. **Production DB/provenance:** independently identify the actual production release-control database and restricted principal; connected `wow-engine-validation` is not release authority.
2. **Genuine PostgreSQL integration:** concurrent 2+ connections, committed-before/after-ACK crash, lost Render ACK, expired lease, stale run/fence, uniqueness, trigger/RBAC and migration tests in an isolated database.
3. **Recovery observer handoff:** after lease expiry, a new approved observer can reconcile a pending deployment but must never gain another Render POST. Do not clear/verify a prior intent from elapsed time or a guessed Render list.
4. **Governed protected workflow wiring:** authenticate source Run/App, CODEOWNER/security/QA/Release, current protected main SHA, service writer inventory, live Render autoDeploy off; single Render `commitId` POST; no competing secret-sync or alternate writer.
5. **Release verifier/receipt:** independently confirm provider live ID/SHA, main SHA, actions, models and reconciliation; only then advance to VERIFIED and (if authorized) the pointer.
6. Integrate independent repair #1839 (rollback zero-retry and 90m observation) into original PR #1834 by its verified branch owner; certify exact head.

## Commands

```bash
python -m pytest -q artifacts/wow-engine/tests/test_render_release_service_fence_candidate.py
```

Offline fakes are **not** evidence of real PostgreSQL serialization or a deployment.
`custom_gpt_identity=WOW_BETTING_ENGINE`,
`runtime_generation=V17_ACTIVE`,
`terminal_authority=V17_TERMINAL_REDUCER`,
`can_execute=false`,
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
