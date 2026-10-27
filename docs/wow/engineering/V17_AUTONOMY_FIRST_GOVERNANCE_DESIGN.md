# WOW V17 — Autonomy-First Engineering Governance

Incident: #2136. Parent: #1688. Existing implementation/QA/worker lanes remain owners.

## Verified starting point
The existing agent-identity policy ALREADY intends ordinary App PRs to pass
without human approval after authentic source-pinned QA and Release. The defect
is excessive control-plane coupling: wide trust-root scopes, premature receipt
polling, App byte-copy confused with independent review, and the original
Cursor reservation still held even with authenticated provider termination.

## Minimal redesign — one existing worker, risk-specific gate
* Routine Class A narrow safe repair: genuine diff identity and exact-head
  required-three tests; existing QA/Release checks, protected merge and real
  production readback. No extra CODEOWNER where ruleset permits autonomy.
* Class B routing/identity/data/lease repair: exact-head code+negative tests,
  proportional independent challenge and required current protected rules.
* Class C model math, fitted artifact, calibration, threshold, ranking,
  specialist promotion: historical and forward model evidence, certified
  calibration/lower bound, independent science and V17 reducer authority.
* Trust roots (auth, workflows, CODEOWNERS, deploy rights, agent instructions):
  retain authentic independent trust-root approval. App mechanical adoption
  proves source-copy identity only; it is NOT independent semantic review.
* A TERMINATED provider session is not a CODE-APPROVED PR. Verify original
  reservation+owner/epoch, no newer active writer, real atomic CAS release,
  and readback to close *session* lease; keep code review independently held.
* One completed producer creates one immutable exact-run receipt; consumers
  react to producer completion and do not poll for a future artifact.
  Inapplicable events neutral; failed/stale/mismatched evidence remains HOLD.
* Retry/reentry is typed: blocked P0 retains owner while other nonoverlapping
  work advances on the current Conductor and single writer. No scheduler clone.

## Source included
Pure function v17/autonomy_governance_profile.py chooses advisory route:
ROUTINE, ENGINEERING, MODEL, TRUST_ROOT or HOLD, given independently verified
path/effect/identity evidence. It also checks the minimal proof requirements
for the original remote-writer release. It NEVER merges, approves, deploys,
writes database, releases leases, invokes a model or enables wagering.

12 isolated regression tests verify legitimate route selection and negative
cases (untrusted effect claims, invalid head/diff, sensitive paths,
unproven lease epoch, missing CAS finalizer).

**NOT ACTIVE PRODUCTION GOVERNANCE.** No protected workflow or ruleset is
modified by this source-only PR. Autonomous provider/class-B integration must
be reviewed and adopted using the existing #2136 / #2054 change owners.

## Integration acceptance
1. Current required-three exact-head CI pass and authorized merge of this
   isolated policy contract. Do not claim new operational capability yet.
2. Through #2136 only, apply the risk-specific policy to existing QA/Release
   and Engineering App routing, with one legitimate protected trust-root change
   and a demonstrated ROUTINE low-risk autonomous merge.
3. Through #2054 only, implement genuine remote reservation CAS; after
   authenticated Cursor terminal proof, verify durable hold=false while
   unapproved source PR remains unmerged; reject races and stale epochs.
4. Demonstrate two unattended AMRT dispatch cycles and five independent
   specialist role receipts attached to one existing incident, plus Render
   web/worker exact SHA parity and independent SIRT review.
5. Through original sport-owned incidents, prove current pregame sporting
   evidence, proper fitted calibration and immutable Action→terminal receipts.
   No external odds, market lines or manual judgment may substitute.

Existing GitHub security restrictions must not be weakened by an AI role.
V17_TERMINAL_REDUCER is sole global terminal authority. can_execute=false.
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.

## Critical integration security invariant: policy self-protection

The advisory policy classifies edits to its *own path* as `TRUST_ROOT` and a
single diff mixing trust-root changes with sporting-model mathematics now
preserves **both** the protected authorization and the independent model
certification requirement. **Before privileged callers consume this policy**,
the separately authorized integration must also add
`/artifacts/wow-engine/v17/autonomy_governance_profile.py` to the enforced
`.github/CODEOWNERS` and to `governance_trust_roots()` input discovery.
A Python classifier's own result is not a substitute for actual GitHub ruleset
enforcement. A missing protected path must fail admission rather than provide
an unreviewed self-edit route.
