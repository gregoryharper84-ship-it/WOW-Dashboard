# WOW V17 Player Props — One-Path Operator View (2026-11-16)

## Decision

Use the **existing** prop lifecycle machinery. Do not build another scraper, model, status gate, certification authority, scheduler, or writer. The only new functionality is a minimal, read-only operator projection attached to the existing authenticated \`/v17/prop-priority-durable-audit-run\` result.

\`\`\`
Permitted source/feature evidence (existing #1933 / #1937; rights pending)
  -> existing immutable forward and exact settlement
  -> existing fitted specialist and calibrated exact-line probability
  -> existing certification and production registration
  -> existing canonical Action -> V17_TERMINAL_REDUCER
  -> operator summary from existing route health (NO authority)
\`\`\`

## Contract

\`v17.prop_operator_summary.summarize_prop_routes(dashboard, requested_routes, stage_statuses)\` uses only inputs produced by the existing durable audit, without network, credentials, SQL, mutable state, or scoring. The priority durable route adds \`operator_summary\` to its response. It does not change the stored health snapshot, underlying status, HTTP authentication, model registries, score routes, or terminal reducer.

For each exact requested route the view provides:
- \`REGISTERED_OBSERVED\` **only** when all existing audit stages succeeded, the authoritative lifecycle row says \`PRODUCTION_REGISTERED\`, the existing runtime/model/calibrator/hydration/canary flags are explicitly true, and there are no blockers.
- Otherwise \`HOLD\`, retaining the original blockers and surfacing a single \`first_blocker\` to make the next engineering step obvious.
- A missing or duplicate route, missing/failed audit stage, malformed blocker list, inconsistent registration evidence, or any \`can_execute\` mismatch is a typed \`HOLD\`; an empty list never establishes an empty slate.
- All summary outputs keep \`can_execute=false\`, \`probability_publishable=false\`, \`authorizes_model_promotion=false\`. \`source_rights_status=NOT_EVALUATED_BY_OPERATOR_VIEW\` explicitly prevents treating the operator display as a vendor entitlement or source/training clearance.

This diagnostic can never self-certify a route, approve a source, promote a model, publish probabilities, or place a wager.

## Test and release

\`\`\`bash
cd artifacts/wow-engine
PYTHONPATH=. python -m pytest -q tests/test_prop_operator_summary.py v17/test_prop_priority_lifecycle_runtime.py
\`\`\`

- Local pure-function TDD: missing implementation yielded nine failing tests; later two tests reproduced an execution-authority oversight and failed before fix. Final isolated run: 12 passed.
- Hosted exact-head CI and a nonauthor independent review must pass before protected merge.
- No new paid service, Supabase table, scheduled task, source registry entry, odds fallback, reviewer team, or source permissions.

## Existing owners and unfinished proof

- [PR #2683](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2683) — rights-gated scraper foundation; remains blocked on genuine independent review and merge.
- [Issue #1933](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1933), [draft PR #1937](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1937) — NFL source-rights verification, read-only historical canary and point-in-time data authorization.
- [Issue #2285](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2285) — first genuine NFL PASSING_YARDS certification, canonical Action and same-identity terminal receipt.
- [Issue #1563](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1563) — independent QA/SIRT/security and source review.

**Not production-complete:** no independently permitted PropsMadness unattended scrape or training grant, no new universal reviewed NFL exact-route release, and no verified production terminal chain. Keep \`V17_TERMINAL_REDUCER\` the sole terminal authority and \`can_execute=false\`.
