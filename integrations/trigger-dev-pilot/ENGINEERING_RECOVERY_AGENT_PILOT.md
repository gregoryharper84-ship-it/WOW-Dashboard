# WOW V17 Engineering Recovery Agent — Trigger.dev DEV pilot

**Status:** draft, development-only, read-only. This is the first durable execution vertical, not production autonomy.

## Why this exists

The existing WOW engineering recovery system already owns incident selection, coding-provider dispatch, the single writer lease, pull requests, protected CI, independent review, QA, Release and SIRT. Trigger.dev must not create a second controller or a competing code writer.

This first Trigger task proves the durable execution spine around one existing incident. It reads the canonical public GitHub issue, captures an immutable identity snapshot (including a SHA-256 digest of the body without copying the body into task output), optionally waits across a Trigger.dev checkpoint/resume boundary, and re-reads the issue to detect source changes.

## Task

- Task ID: \`wow-v17-engineering-recovery-intake\`
- Environment: Trigger.dev DEVELOPMENT only; the provider-reported \`ctx.environment.type\` must equal \`DEVELOPMENT\`.
- Concurrency: one run.
- Retries: up to three, with bounded backoff for transport, rate-limit and 5xx failures only.
- Checkpoint probe: pass \`checkpointProbe: true\` to wait 65 seconds using Trigger.dev \`wait.for\`, then re-read the issue. A stable snapshot returns \`DURABLE_CHECKPOINT_RESUME_PROVED\`; a changed issue returns \`SOURCE_CHANGED_DURING_CHECKPOINT\`.
- Source: fixed public GitHub Issues API URL for \`gregoryharper84-ship-it/WOW-Dashboard\`; no user-supplied URL or Authorization header.
- Output: issue number/title/labels/timestamps, body digest and body length. The untrusted issue body is never returned or logged.

## Manual DEV test

From \`integrations/trigger-dev-pilot\`, after the parent pilot is independently accepted for DEV use:

1. Configure the existing Trigger.dev project's actual \`TRIGGER_PROJECT_REF\` and its Development-only \`tr_dev_\` key in the operator's local environment. Never put credentials in Git, CI logs or chat.
2. Set \`WOW_TRIGGER_PILOT_ENABLED=true\`, \`WOW_TRIGGER_PILOT_ENV=dev\`, \`WOW_TRIGGER_ENGINEERING_AGENT_ENABLED=true\`, \`WOW_CAN_EXECUTE=false\`, and \`WOW_DRY_RUN_ONLY=true\`.
3. Run \`npm run dev\` to connect the local Development worker.
4. In the Trigger.dev DEVELOPMENT dashboard, manually run \`wow-v17-engineering-recovery-intake\` with \`{"incidentNumber":2516,"checkpointProbe":true}\`.
5. Verify one run, the provider-reported DEVELOPMENT environment, the 65-second checkpoint/resume, the second source read, and the terminal output. Repeat with a changed or closed issue fixture only in isolated tests, not by mutating a production issue.

This task is manual-only. It adds no schedule, cron, webhook, Trigger deployment, production credential, production worker, database write, GitHub mutation or model route.

## What is deliberately not implemented yet

- No LLM call: no new paid API, provider key or model subscription is introduced. This first vertical establishes durable execution and evidence capture without spending on a model provider.
- No GitHub Actions dispatch: the Trigger task has no write credential and no authority to dispatch the existing coding worker.
- No code edits, shell execution, commits, PR creation, merge, deployment or release actions.
- No sporting probability, fitted artifact, calibration, lower-bound, model qualification, scoring, settlement, publication or wager/order capability.

## Next integration milestone

After this pilot's live DEV checkpoint/resume receipt and non-author SIRT review, integrate Trigger with the existing authorized engineering worker through its current single-writer admission and lease authority. The Trigger agent may orchestrate and wait for that worker; it must not become a second writer. Use the existing approved coding provider and existing GitHub/CI/QA/Release/SIRT lifecycle. Do not add a second queue, ledger, controller, cron, reducer, credential or paid model provider.

Before any write-capable handoff, prove cross-provider idempotency, ambiguous-timeout reconciliation, exact incident/PR correlation, duplicate suppression, cancellation and stale-head behavior. No Trigger idempotency key alone can prove exactly-once remote side effects.

## Invariants

\`\`\`text
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE only when independently verified; this intake returns UNVERIFIED_BY_READ_ONLY_INTAKE
terminal_authority=V17_TERMINAL_REDUCER
probability_authority=NONE
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
\`\`\`
## Exact-head validation note

The first hosted attempt caught a TypeScript JSDoc parameter-shape error in the DEV guard; the helper signature was corrected. The latest branch head must pass a fresh hosted run before this pilot can be considered source-validated. The previous failed run is retained as evidence and is not relabeled.


## GitHub-hosted DEVELOPMENT acceptance (no local terminal)

For operators who do not want to run a local CLI, the repository includes an opt-in GitHub Actions workflow at `.github/workflows/wow-trigger-remote-dev-acceptance.yml`. It runs the existing Trigger.dev CLI inside an ephemeral GitHub-hosted runner, not on the operator's Mac. It starts a named DEVELOPMENT branch `wow-v17-github-actions-acceptance`, triggers the health, synthetic retry and this engineering-intake task, verifies the 65-second wait/resume plus second read, prints a sanitized receipt, and stops the worker.

Before enabling it, configure:
- Repository Actions variable `WOW_TRIGGER_REMOTE_DEV_ENABLED=true`.
- Repository Actions variable `TRIGGER_PROJECT_REF` set to the actual project reference.
- GitHub Actions environment `wow-trigger-dev` with a new temporary DEVELOPMENT-only `TRIGGER_SECRET_KEY` secret. Trigger.dev Free/Hobby only provides a no-restrictions key preset, so this key has full access to DEVELOPMENT. Use a short expiration and revoke after acceptance; never use Production/Staging keys.

The workflow is restricted to same-repository PR #2529, is opt-in, read-only at GitHub permission level, has no schedule and a 12-minute timeout. It does not use Preview branches (which are unavailable on the Free plan), deploy to Staging/Production, or dispatch any coding worker. The workflow's actual task receipt—not CI green—is required for acceptance. The local DEV procedure remains available as a fallback.
