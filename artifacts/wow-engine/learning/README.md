# WOW Engineering — Triple D learning v1

**Classification:** offline training/evaluation experiment. **Not** production engineering authority, an autonomous worker, an ML model improvement, or a release gate.


## Simple operational V1: verified fix lookup

This read-only lookup uses the **existing canonical incident ledger** (no new
database, provider credits, workflow, agent, or secrets). Run it from repo root
before attempting an engineering repair:

```bash
python artifacts/wow-engine/learning/lessons.py list
python artifacts/wow-engine/learning/lessons.py match --category OBSERVABILITY_CORRELATION
python -m unittest discover -s artifacts/wow-engine/learning/tests -v
```

The lookup returns a root cause, original test gap, preventive control, regression
test and reference PR **only** when the source incident has confirmed root cause,
passed independent review/QA, a recorded verified production deployment and
Reporter closure. For unknown categories it returns `NO_VERIFIED_MATCH`
with an empty list, never an invented repair. Results are advice for a **new**
incident, not proof that the current incident is repaired. Newly closed incidents
become available when their canonical ledger row contains the required evidence;
no retraining job or duplicate learning store is created.

### Automatic canonical incident intake

The existing Reporter `create-postmortem` command now consults the same verified
lessons **without requiring a separate manual lookup**. It searches for an
exact, uppercase verified failure-category token in the incident title/evidence.
Reporter can also supply an explicit category once it is known:

```bash
python artifacts/wow-engine/v17/nightly_incident_records.py create-postmortem   --title "Self acceptance correlation" --severity P2   --domain observability --evidence "OBSERVABILITY_CORRELATION seen in logs"
# Or: --failure-category OBSERVABILITY_CORRELATION
```

The resulting canonical postmortem includes an **advisory** historical context
section with prior evidence and a reference PR. No source-provided suggestion
becomes a new incident root cause, verified status, or approval. Missing,
ambiguous, or unsupported matches never claim a verified fix. Missing lookup
module is recorded as `UNAVAILABLE` but does not prevent normal intake.
Do not run this write-producing example against real canonical ledger merely
for demonstration; the test suite uses an isolated temporary ledger.

V1 does **not** retrain LLM weights or sporting probability models, modify
production, authorize merging, or start a background job. Real-world benefit
must be measured separately through independent incident recurrence and
production recovery statistics. The standalone training exercise below remains
distinct from this operational lookup.

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
