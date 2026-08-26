# WOW Incident Response Runbook

## Severity levels

| Level | Criteria | Response time |
|-------|----------|---------------|
| P0 | Scoring completely down; health endpoint non-200 | Immediate |
| P1 | Scoring returns wrong terminal labels in production | < 15 min |
| P2 | Single prop type / sport silently failing | < 1 hour |
| P3 | Diagnostic/logging degraded; no scoring impact | Next session |

---

## P0: Scoring down

```bash
# 1. Check health
curl -s https://<production-url>/wow/engine/health | python3 -m json.tool

# 2. Check deployment logs (Replit)
# Replit UI → Deployments → Logs

# 3. Check gunicorn workers
# Look for: WORKER TIMEOUT, ImportError, port bind failure

# 4. If port not bound: check for bare `gunicorn` vs `python -m gunicorn`
# 5. If WORKER TIMEOUT: check for threading.Lock deadlock (--preload must be off)
```

If not resolved within 5 minutes → rollback ([rollback.md](rollback.md)).

---

## P1: Wrong terminal labels

```bash
# 1. Get governance hash
curl -s https://<production-url>/wow/governance/status | python3 -m json.tool | grep governance_hash

# 2. Compare to expected hash from last known-good deploy
# 3. If hash changed unexpectedly → code was deployed with gate/patch count change

# 4. Check blockers on a failing row
# Look for: DATA_CONTRACT_FAIL, GOVERNANCE_MISMATCH, RUN_INVALID_*
```

Root causes by error code:
- `GOVERNANCE_MISMATCH` → GPT session using stale hash; GPT must call `/wow/governance/status` to resync
- `DATA_CONTRACT_FAIL:missing_field:*` → enrichment field missing from GPT payload
- `PROB_LEDGER_INCOMPLETE` → `model_probability_ledger` provided in incomplete Stage-2 format; omit or provide full ledger

---

## P2: Single prop type silently failing

```bash
# 1. Test the specific prop type with a minimal payload
# 2. Check _fetch_mlb stat_key mapping (must be canonical: "H" not "Hits")
# 3. Check enrichment key format (player:prop must use lowercase)
# 4. Check acquisition_execution_report for NOT_CALLED pathways
```

Common cause: new prop type added to the board but not mapped in `_STAT_KEY_CANONICAL` in `auto_enrichment.py`.

Fix: add the alias (e.g. `"plate appearances" → "PA"`) to `_STAT_KEY_CANONICAL` and `_MLB_STAT_FIELDS` in `auto_game_log.py`.

---

## Evidence to collect for any incident

1. `GET /wow/engine/health` → full JSON
2. `GET /wow/governance/status` → full JSON  
3. The failing request body (scrub any credentials)
4. The full response body including `blockers`, `terminal_label`, and `acquisition_execution_report`
5. Last 50 lines of gunicorn logs
6. Git log of last 3 commits: `git log --oneline -3`
