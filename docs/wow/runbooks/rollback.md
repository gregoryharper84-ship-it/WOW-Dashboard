# WOW Rollback Runbook

## Code rollback vs. database rollback

**These are independent operations.** A legacy platform checkpoint restores code. It does NOT restore the production database. Assess which rollback is needed before acting.

| Symptom | Code rollback? | DB rollback? |
|---------|---------------|--------------|
| Bad route / logic regression | Yes | No |
| Missing column / wrong schema | Maybe | Yes |
| Scoring returns wrong labels | Yes | Maybe |
| Data silently lost | No | Yes |
| Performance regression | Yes | No |

---

## Code rollback (legacy platform checkpoint)

1. Open legacy platform → Version Control → Checkpoints
2. Identify the last known-good checkpoint (taken before the bad deploy)
3. Click **Restore** — this restores code AND restarts the service
4. Verify health endpoint returns 200:
   ```bash
   curl -s https://<production-url>/wow/engine/health
   ```
5. Verify `governance_hash` matches the pre-patch value
6. Run post-deploy smoke test from [deploy.md](deploy.md)

---

## Database rollback

> **Warning:** Production database restoration is separate from code rollback and may not be available via checkpoint. Assess data loss impact before proceeding.

1. Identify which migration caused the issue (check `git log -- migrations/`)
2. If a rollback script exists (`migrations/<id>_rollback.sql`), run it:
   ```bash
   psql $DATABASE_URL < migrations/<id>_rollback.sql
   ```
3. If no rollback script exists, manually revert the schema change:
   - DROP newly added columns (after confirming no data depends on them)
   - RECREATE dropped columns from the prior schema definition
4. Verify the scoring pipeline can connect and read/write correctly

---

## After rollback

1. File a post-mortem issue documenting what went wrong
2. Update the patch contract template with the new failure mode as a known risk
3. Add a regression test that would have caught the issue
4. Do not re-attempt the patch until the regression test exists and passes
