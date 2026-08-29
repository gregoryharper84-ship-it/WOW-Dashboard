# Overnight Acceptance Gate

Before infrastructure provisioning or production migration:

- full repository regression PASS
- real Postgres schema/RPC integration PASS
- Redis/Celery subprocess integration PASS
- HTTP→worker→coordinator→reconciliation PASS
- worker registry parity PASS
- `/health/ready` fail-closed behavior PASS
- no duplicate runtime/data plane introduced
- `can_execute=false`

No Calibration Health or probability-publication gate may be promoted without real prospective evidence.
