# August-scale Render deployment cutover — candidate, not activated

**State:** Engineering design and guarded admission-rule source only. The current protected Render release controller is authoritative until a replacement has been independently authorized, installed and verified. Do not enable native Render auto-deploy or issue an unfenced deploy.

## Provider credential finding

Render's API keys can access **all workspaces belonging to the key owner**. Giving a different workflow a newly named `RENDER_API_KEY` does not create a service-only credential boundary. Official source: https://api-docs.render.com/reference/authentication

Render provides a **per-service secret Deploy Hook** under the service's Settings page. The hook supports selecting an exact Git commit by adding the `ref` query parameter; it is an alternative to granting a release runner a workspace-wide API key. Official sources:

- https://render.com/docs/deploy-hooks
- https://render.com/docs/deploys

A copied or syntactically valid deploy-hook URL does **not** prove correct service ownership, sole-writer inventory, authorization, reachability, or release safety. The hook is itself a privileged secret. Never log, upload, paste into PRs, put in an artifact/receipt, or transmit it to a chat.

## Proposed owner-assisted provisioning — blocked until authorized

1. Repository owner and Security/Release identify the sole target production service `srv-da7sa9gu01pc73brt80g` in Render workspace `tea-da7s2ggu01pc73br7gg0`; verify the URL in the Render dashboard.
2. In that service's **Settings → Deploy Hook**, obtain or regenerate the unique hook. Store it in GitHub Actions **environment `wow-release`** as a new protected secret such as `WOW_RENDER_DEPLOY_HOOK`. **Do not share its value in chat.**
3. Security verifies that only the one independently authorized release job can read this environment secret. Ensure no other GitHub workflows, bots, worker runtime, PR events or external automation retain the hook. Rotate any previously exposed hook. Attest this from actual GitHub environment inventory and provider service configuration, not a Boolean supplied by the same process.
4. Before cutover, inventory current Render API-key users, old release workflow, native auto-deploy settings, release hooks and active deploys. Establish the durable lease / intent / exclusive writer **outside** this rule. Stop the old writer only as part of a controlled one-writer transition, and prove no in-flight or uncertain previous POST.
5. A separately reviewed engineering change may then install **one** authenticated writer: from protected merged `main` exact SHA, after all three August-era checks and independent Security/CODEOWNER/QA/Release decisions, reserve one durable release intent, claim the sole writer lease, and invoke only the service-specific hook with `ref` equal to the approved 40-character SHA. The hook URL must be read only from the protected environment and never printed. An ambiguous response means **read-only reconciliation**, never automatic second POST.
6. Independently read back Render service ID, deployment ID, exact live commit SHA and service status, run health / Action / canonical persistence and reconciliation acceptance, and record SHA-bound independent QA. No source PR or successful CI alone proves a deploy.
7. If the replacement cannot be independently certified or verified, preserve a typed HOLD and disable the new writer. Do not allow both old and new writers simultaneously, and do not switch to Render native auto-deploy as an emergency shortcut. Rollback requires separately authorized exact-SHA and durable fence.

## Existing candidate and limitations

- `v17/august_release_admission.py` is **inert** and only checks internally consistent caller-provided facts. Its synthetic success means `CLAIMS_CONSISTENT_NOT_AUTHORIZED`, not proof.
- Until a trusted evidence collector and deployment lease implementation call it, the function does not affect live release. It is unsuitable as a production gate by itself.
- Existing `github_release_journal.py` and its credential blocker remain in force; never change the hardcoded scope gate to `True` to force release.
- The new service hook is a **design alternative** supported by provider docs, not evidence that the hook has been provisioned or authenticated in this account.

Invariants: `custom_gpt_identity=WOW_BETTING_ENGINE`; `runtime_generation=V17_ACTIVE`; `terminal_authority=V17_TERMINAL_REDUCER`; `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`. No betting orders, model substitution or unilateral approval.
