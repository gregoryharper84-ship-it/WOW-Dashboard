# WOW Engineering — Agent Operating Instructions

**Mission: DEBUG. DISSOLVE. DEPLOY.**

Applies to repository coding and repair agents. Read [the canonical WOW Engineering doctrine](docs/wow/engineering/WOW_ENGINEERING_MISSION_DOCTRINE.md) and [the existing engineering contract](CLAUDE.md) before proposing changes. These instructions supplement, never supersede, V17 governance, exact fitted-model ownership, protected branch rules or release authorization.

- **DEBUG:** Reproduce the actual failure, identify the precise failing boundary and root cause, collect issue/run/SHA evidence, and distinguish model, source, identity, transport, deployment and reconciliation failures.
- **DISSOLVE:** Repair the root cause, replace recurring deadlock designs with simpler approved approaches, preserve single-writer lease and durable owner/re-entry, and do not bypass security, independent review, tests, or native probability/terminal gates.
- **DEPLOY:** Make the smallest safe change, add focused/negative/regression tests, obtain all applicable exact-head approvals, route through protected merge and authorized deployment, and independently verify deployed SHA plus actual product behavior. PRs, CI, and liveness do not prove recovery.

Cross-team accountability: **Engineering repairs; AMRT restores; WOW Betting Intelligence validates model capability; SIRT independently verifies; the Ecosystem Conductor preserves one owner and reliable handoffs.**

Preserve exactly:
```text
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

No live wagers or market orders, no self-approval, no alternate fitted probabilities, no fabricated PASS, no competing production writer. Stop at `PR_CREATED` or `BLOCKED_WITH_EXACT_REASON` when release evidence/authority is absent; keep accountable ownership and a precise next action. A source instruction document is **not** evidence that a resident worker, other Custom GPT editor, or external agent has ingested it.
