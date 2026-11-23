# WOW V17 pregame evidence-chain reliability — design
Date: 2026-11-18 | Incident: #2020 | Owner: WOW Engineering / AMRT
Independent acceptance: SIRT + Independent Verification. Terminal authority: V17_TERMINAL_REDUCER.

## Intent
Every published sporting probability must be traceable from an authorized, timestamped pregame input snapshot to the exact fitted specialist, its artifact and calibrator, an immutable numeric prediction, and independent certification. An absent link must remain a specific blocker. Preserve existing good models; do not use odds, prose or a second control plane as substitute probabilities.

## Approaches considered
1. Replace sources, models and infrastructure with a vendor stack: rejected (expense, no independent WOW calibration, migration risk).
2. Add a minimal read-only evidence-chain audit to existing Supabase/Render/GitHub with targeted repairs: **chosen** (low operational overhead, preserves existing governance).
3. Use a manually reviewed spreadsheet: rejected as primary path (cannot prove automated freshness or immutable numeric lineage).

## Architecture
Existing source snapshots -> canonical as-of feature rows -> existing fitted model registry + calibrated scorer -> immutable prediction receipts -> exact-version forward grades -> independent certification evidence -> V17 terminal reducer.

No new scheduler, credential, paid service, training framework, release bypass or scoring endpoint is introduced by phase 1. The audit is read-only and never certifies or changes a publication flag.

## Scope and acceptance
P0-A (this PR): exact NFL verified-forward-grade -> prediction -> source snapshot/feature hashes -> artifact checksum -> calibrator -> independent certification receipt audit.
- Source/model version, event, pregame timing and freshness must match.
- Metric summary is grouped by exact model version, not only a mixed-version global health flag.
- No inferred certification, no execution, no SQL writes.
- Validation observation on 2026-11-18: 107 grades / 107 distinct events for NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb; all queried metadata links matched; no matching independent certification receipt. Audit is not a sport-wide numerical-input-consumption test or production acceptance.

P0-B (separate governed follow-up): independently recompute exact-version ECE, Brier, log loss, bias, lower-bound reliability and counterexamples with true chronological cutoff. Confirm forward grades were recorded pre-kickoff and no data leakage. Obtain SIRT counterexample review; protected certification and artifact publishability decisions remain human/governed, never automatic.
P0-C: verify authenticated Render scorer/Action and immutable current-slate receipt after approved deployment; revalidate source timestamps and model rerun after material news.

P1-D: NBA/WNBA replay for current SHADOW candidate artifacts; validate lineup/usage/rest/opponent input columns and independent time-sliced holdouts before approval.
P1-E: NHL licensed-source rights and two independent starter-goalie confirmations; check shift/TOI/workload numerically, not only in narrative; build chronologically valid fitted specialist evidence.
P2-F: apply the same contract sport by sport to NCAAF, NCAAB, MLB, soccer, tennis, golf, MMA, boxing and cricket without pretending equal model readiness.

## Guardrails
- Existing tables and source contracts are authoritative; no manual model probabilities, odds substitution or blanket weight changes.
- A hash records evidence identity, not proof that a feature had a numerical effect; require controlled input-perturbation tests in later sport-specific acceptance.
- Validation DB evidence is not proof of production deployment, live GPT Action integration or market availability.
- Keep BACKEND_RUNTIME, MODEL_CAPABILITY, REPOSITORY_GOVERNANCE and LIVE_GPT_EDITOR_SYNC separate.
- Preserve typed blockers and can_execute=false.
- Explicit non-goals: wager execution, automatic promotion, threshold relaxation, rewriting historical predictions, purchasing data.
