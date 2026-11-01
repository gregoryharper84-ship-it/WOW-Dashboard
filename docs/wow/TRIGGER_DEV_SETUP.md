# Trigger.dev — development-only smoke test

This is a **development-only** Trigger.dev integration for WOW V17, project
`proj_ywxmmbamgelgqoxbpbql`. It adds one harmless task; it does **not** replace
the existing Render worker, change fitted-model probabilities, publish forecasts,
authorize wagers, or schedule background production work.

## One-time setup in a network-connected checkout

This repository uses pnpm. From the repository root:

```bash
pnpm install --lockfile-only
pnpm install --frozen-lockfile
npx trigger.dev@4.8.3 login
```

The login command opens a browser and must be completed by the account owner.
Do not commit the login token, `.env`, or generated `.trigger/` files.

Open **Trigger.dev → project proj_ywxmmbamgelgqoxbpbql → Development →
API Keys**. Create a **Trigger only** development API key, and set it in a local
`.env` file:

```dotenv
TRIGGER_SECRET_KEY=<development key from dashboard>
```

Do not use a production key here. Do not paste the key into GitHub, a PR,
CI logs, or task payloads.

## Register and run the test task

```bash
npx trigger.dev@4.8.3 dev
```

Keep that process running. In another terminal run:

```bash
npx trigger.dev@4.8.3 tasks trigger wow-trigger-dev-smoke --payload '{"message":"WOW V17 development smoke test"}' --wait
```

Check the **Development** task list and run details in the Trigger.dev
dashboard. The task ID is `wow-trigger-dev-smoke`; a successful output includes
`status: "ok"` and `can_execute: false`. Capture the run ID, result, and
timestamp for independent acceptance. If the CLI is not authenticated, complete
`npx trigger.dev@4.8.3 login` first.

## Release boundary

The source alone is **not** proof of a registered or running task. Preserve
existing protected merge/release checks and update `pnpm-lock.yaml` in a
network-connected checkout before considering this integration installable under
the repository's frozen-lockfile CI. Trigger.dev development success does not
prove Render production integration or WOW model capability.

The global WOW V17 terminal authority remains `V17_TERMINAL_REDUCER`.
`can_execute=false` remains enforced.
