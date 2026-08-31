# WOW Operational Runbooks

This directory contains operational playbooks for the WOW scoring engine service. Reference these during incidents, deployments, and rollbacks.

---

## Runbook index

| Runbook | When to use |
|---------|-------------|
| [deploy.md](deploy.md) | Deploying a new version to production |
| [rollback.md](rollback.md) | Rolling back code and/or database after a bad deploy |
| [incident.md](incident.md) | Investigating and resolving production incidents |

---

## Critical operational facts

- **Production database restoration** is separate from code rollback. A code rollback via legacy platform checkpoint does NOT restore the production database. Database migrations need their own recovery plan.
- **Governance hash** changes whenever gate count, patch count, or precedence changes. After deploy, all GPT sessions must call `GET /wow/governance/status` to get the new hash before scoring.
- **gunicorn workers:** 2 workers in production. In-process state is NOT shared between workers (use PostgreSQL for shared state). Advisory lock `778597299` guards cron writes.
- **Keepalive daemon:** hits `/wow/engine/health` every 10 min to prevent autoscale SIGTERM at 15-min idle. Requires `legacy platform_APP_URL` environment variable.
