# WOW Deploy Runbook

## Pre-deploy checklist

1. Run `bash scripts/wow-preflight` — must exit 0 (no failures)
2. Run full regression suite — must show `N passed, 1 failed (sentinel only)`
3. Confirm `publish_authorized: true` in the patch contract
4. Confirm the patch commit is the HEAD of `main`

## Deploy steps

1. **Create a legacy platform checkpoint** before deploying (legacy platform UI → Version Control → Create checkpoint)
2. Click **Deploy** in the legacy platform UI or run `legacy platform deploy` from CLI
3. Wait for deployment to complete and the health endpoint to return 200:
   ```bash
   curl -s https://<production-url>/wow/engine/health | python3 -m json.tool
   ```
4. Verify the `governance_hash` matches what was in the pre-deploy `GET /wow/governance/status` response

## Post-deploy smoke verification

Using **fresh** session/run IDs (never reuse dev IDs):

```bash
GOV_HASH=$(curl -s https://<production-url>/wow/governance/status | python3 -c "import sys,json; print(json.load(sys.stdin)['governance_hash'])")
SESSION_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
RUN_ID=$(python3 -c "import uuid; print(uuid.uuid4())")

curl -s -X POST https://<production-url>/gate-engine/run \
  -H "X-API-Key: $GPT_ACTION_SECRET" \
  -H "Content-Type: application/json" \
  -d "{
    \"rows\": [{\"player\": \"Test Player\", \"sport\": \"MLB\", \"prop_type\": \"Strikeouts\", \"line\": 5.5, \"direction\": \"MORE\", \"slate_date\": \"$(date +%Y-%m-%d)\"}],
    \"expected_governance_hash\": \"$GOV_HASH\",
    \"session_id\": \"$SESSION_ID\",
    \"research_run_id\": \"$RUN_ID\",
    \"as_of\": \"$(date +%Y-%m-%d)\",
    \"record_entries\": false
  }" | python3 -m json.tool | grep -E "terminal_label|http_status|error_code"
```

Expected: HTTP 200, no `error_code`, `terminal_label` not `DATA_CONTRACT_FAIL` (unless no enrichment supplied).

## Rollback trigger

If any of the following occur within 30 minutes of deploy:
- Health endpoint returns non-200
- `governance_hash` is wrong
- Live scoring returns unexpected terminal labels
- Database connection errors in logs

→ Follow [rollback.md](rollback.md) immediately.
