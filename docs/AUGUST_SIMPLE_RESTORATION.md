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

The generic `wow-v17-enable-auto-merge` workflow is also retired. Repository
auto-merge is disabled, and its attempted run failed with
`enablePullRequestAutoMerge`; it is no longer a usable merge path. This does not
change the separate guarded model-experiment workflow's auto-merge behavior.
Historical auto-merge settings on existing PRs may remain after workflow
retirement. The repository owner must audit and cancel those settings through
GitHub before any future re-enablement; this change does not clear them.
