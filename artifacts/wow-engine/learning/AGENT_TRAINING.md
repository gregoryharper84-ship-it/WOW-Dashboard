# WOW Engineering — Triple D Coding Agent Training

This is an **offline teaching/evaluation guide**, not authority to dispatch agents, control production, open new release gates, bypass reviews, or spend on cloud services.

## Training protocol

1. Read the public prompts (`python artifacts/wow-engine/learning/evaluate.py prompts --split practice`). Record the hypothesis and root-cause evidence before seeing the reference solution.
2. Copy `artifacts/wow-engine/learning/challenges/starter.py` into an isolated workspace. Run `python artifacts/wow-engine/learning/challenges/run_challenge_tests.py --impl starter`: nonzero exit is expected; preserve the exact failing assertions as RED evidence. Do not alter the permanent reference test suite.
3. Patch the copied starter functions to repair the queue starvation, legitimate-review QA replay, and Cursor writer-fencing defects. Keep signatures unchanged and avoid importing the real WOW scoring/production system.
4. Add **at least two additional negative tests per issue** covering forged identity, ambiguous status, duplicate work, stale SHA or competing writer. Run focused, adjacent and regression tests against the patched copy.
5. Obtain independent diff review and compare against an untouched test baseline; do not merely copy `reference.py` and call it independent authoring.
6. Create a receipt: exercise case ID, historical source URL, starting snapshot hash, changed file hashes, objective root-cause explanation, pre-fix RED and post-fix GREEN command/result, reviewer identity, failed counterexamples, and remaining limitations. `FIXED_AND_VERIFIED` is **not available** for an offline teaching exercise.
7. Score a local proposal via `evaluate.py assess` as **declaration conformance only**. A score of 100 does not mean a repair is correct, authorized, merged or deployed.
8. SIRT should evaluate separate unseen cases and genuine protected production repairs. Do not report real agent improvement without a before/after cohort and an independently verified denominator.

## Responsibilities

- **Engineering / AMRT:** replay and repair the exercise; document the exact source SHA and minimal patch.
- **Copilot, Claude, Gemini, Cursor:** may run training only if those agents are actually connected and admitted to an isolated environment; writing this guide does **not** dispatch them or consume quota.
- **Independent reviewer:** challenge provenance, concurrency, race windows, malicious or malformed inputs, WIP starvation, duplicate attempts, and false-green results.
- **SIRT:** curate an unseen holdout set and independently audit real operational outcomes, never issue self-approval.

## Scoring and promotion

Retain: agent/provider/version, dataset checksum, split, pre-fix failures, post-fix successes, new negative tests, patch size, independent reviewer, recurrence and closed production incidents. Exclude any leaked case from blind evaluation.

Promote training-guidance updates only when objective unseen outcomes improve without compromising safety; do not fine-tune a proprietary provider model by presenting evaluation feedback. Actual model weight training is a distinct future project, subject to its licensing and spend controls.

## Safety and zero-spend invariants

Only read documented public issue material; do not copy credentials, sensitive logs, private chat data, personal details or model artifacts into training corpora. No external network, paid AI provider, cloud GPU, agent dispatch, overnight task, new watcher, production mutation or market/order capability in this training kit. Reuse the existing authorized single-writer and protected release process for any **real** production repair.

`custom_gpt_identity=WOW_BETTING_ENGINE`
`runtime_generation=V17_ACTIVE`
`terminal_authority=V17_TERMINAL_REDUCER`
`can_execute=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
