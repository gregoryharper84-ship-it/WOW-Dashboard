# WOW Engineering — Triple D learning v1

**Classification:** offline training/evaluation experiment. **Not** production engineering authority, an autonomous worker, an ML model improvement, or a release gate.

## Purpose
Turn verified past WOW incidents into repeatable debugging exercises without repeating the same failed approaches. Reuse the canonical `v17/nightly_incident_records.py` learning ledger, real GitHub issues, existing CI, and the already-declared optional `mlflow>=3,<4` research dependency. Never create a second incident queue, writer authority, approval service, or production install requirement.

## Run (Python standard library only)
From the repository root:

```bash
python artifacts/wow-engine/learning/evaluate.py validate
python artifacts/wow-engine/learning/evaluate.py prompts --split practice
python artifacts/wow-engine/learning/evaluate.py prompts --split evaluation
python -m unittest discover -s artifacts/wow-engine/learning/tests -v
python artifacts/wow-engine/learning/challenges/run_challenge_tests.py --impl starter  # RED, expected nonzero
python artifacts/wow-engine/learning/challenges/run_challenge_tests.py --impl reference # GREEN
```

The intentionally broken starter represents simplified historical errors; reference code is pedagogical, **never a patch to protected production code**. An agent fixes a disposable copy, adds missing negative cases, then independent QA challenges the actual diff. Do not hand agents reference code when assessing skill. Evaluation answers are present in the repository and **are not a blind benchmark**.

To assess a declaration-only proposal, create a local JSON list of objects with: `case_id`, `root_cause_code`, `proposed_tests`, `preserved_controls`, `proposed_shortcuts`, `disposition`, `incremental_spend_usd`, and `proposed_external_services`. Run `python artifacts/wow-engine/learning/evaluate.py assess --answers <file> --split practice`. Scores measure **reasoning label conformance only**, not code or operational improvement.

## Historical coverage
- #2136 / #2198: four failed governance checks share one upstream trust-root author-provenance defect.
- #2136 / #2127: stale failed QA after genuinely newer authenticated review must have bounded, safe re-entry.
- #2158: conflicted source starvation in a capped adoption queue.
- #2198: Cursor terminal identity and a second held reservation cannot be guessed or forced.
- #1635: asynchronous check completion and typed failure receipts.

## Governing boundaries
`custom_gpt_identity=WOW_BETTING_ENGINE`
`runtime_generation=V17_ACTIVE`
`terminal_authority=V17_TERMINAL_REDUCER`
`can_execute=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

Strict offline scope: no provider API calls, secrets, bets/orders, probabilities, workflow permissions, CODEOWNER policy, release gate changes, direct deployments, MLflow tracking server, or unapproved spend. Only public GitHub incident metadata may be used. GitHub Actions compute and any existing provider billing are not guaranteed free.

## Objective acceptance
1. Corpus validates and is deduplicated, grounded to exact GitHub sources.
2. Existing learning unit tests pass; deliberately faulty starter fails; reference passes.
3. Benchmark cannot claim independent QA, deployed SHA or verified production outcome.
4. Source is reviewable, remains off Render production and is merged only under normal repository policy.
5. True improvement requires held-out real-code exercises, pre-fix RED, post-fix GREEN, independent review and actual verified closures. SIRT should own a sealed unseen evaluation set.
