# August-simple operations restoration

## Stage 1: optional engineering workflows

The engineering digest, effectiveness scorecard, PR keep-current, and probe
cleanup workflows remain available through `workflow_dispatch`. Their scheduled
triggers—and the keep-current workflow's `push` trigger—are removed, so they do
not run automatically. Their jobs, permissions, and existing safeguards are
unchanged.

This stage does not retire required CI, QA/Release checks, reconciliation, model
workflows, or the protected deployment controller. It does not change production
runtime state, model behavior, or execution safety settings.
