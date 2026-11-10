# WOW V17 — Actor-Neutral Engineering

## Implementation
Authorized contributors may investigate, develop, test,
and submit isolated pull requests without wow-engineering[bot].

The Engineering App is optional, not a required dependency.

## Protected changes
Protected-file changes require the applicable independent
CODEOWNER, Security, QA, and Release approvals.

No contributor may self-approve or impersonate another identity.

## Concurrency
One authorized writer per declared workstream.
Verify current main, PR head SHA, and file ownership.
Do not modify files owned by an active restoration writer.

## Production
Development authorization is not deployment authorization.
Production requires the governed exact-SHA release process.

## Priorities
P0 restoration work takes priority over P1/P2 enhancements.

## V17 invariants
V17_TERMINAL_REDUCER remains the sole terminal authority.
can_execute=false.
No live wagers, trading, or market orders.
