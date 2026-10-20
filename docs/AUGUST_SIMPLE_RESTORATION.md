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

## Independent QA/Release manual recovery

The independent QA and Release Authority workflows retain their `workflow_run`
triggers. Their hourly polling schedules are removed; when an upstream event is
lost, an authorized operator can recover the checks manually:

1. In GitHub Actions, open `wow-v17-independent-qa-check`, choose **Run
   workflow** on branch `main`, and enter the PR number. Wait for the
   `WOW Independent QA exact-head` check.
2. If QA passes, open `wow-v17-release-authority-check`, choose **Run
   workflow** on branch `main`, and enter the same PR number. Wait for
   `WOW Release Authority exact-head`.
3. Confirm both checks are completed successfully on the PR's current head and
   attributed to their separate QA and Release Apps. If the head changes, rerun
   QA and then Release for that head. A failure or missing check remains a
   blocker; do not bypass it.

Manual dispatch re-resolves the open PR's current head and independently
rechecks evidence. There is no automatic polling fallback: unresolved or lost
events require this operator re-entry, and the required ruleset checks remain
unchanged.
