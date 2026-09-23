# WOW V17 Multi-Agent Engineering Team Build Receipt

Status: `PR_CREATED_PENDING_PROTECTED_CI`

This change introduces independent agent invocations for Engineering Lead, Research/Triage, Implementation, Independent Review, System Architect, QA Verification, Release/Production Verification, and Frontier Intelligence / Continuous Improvement.

Governance invariants:
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- `V17_TERMINAL_REDUCER` remains sole global terminal authority
- only `ENGINEERING_AGENT` may write code
- Frontier Intelligence is experiment/research only and must set `production_change=false`
- Class C sporting-model changes remain challenger-only pending replay, counterexample review, holdout/forward validation, regression, and governed promotion
