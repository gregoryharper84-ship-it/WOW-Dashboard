# V17 Cross-Sport Evidence Integrity — Phase 1 implementation and rollout

**Status:** shared code and deterministic regression tests; NOT production-certified or globally enforced.

**Scope:** every sport declared in `v17/team_event_capability_manifest.py`:
MLB, NFL, NBA, WNBA, NCAAF, NCAAB, NHL, SOCCER, TENNIS, PGA, MMA, BOXING, CRICKET.
This is the **LLP team/event probability lane**. It does not invent a team-win
model for unsupported sports, change player-prop specialist routing, or imply
a spread-scoring model exists.

## What this change implements

- One V17 shared cross-sport evidence-integrity preflight, not an NBA-only path.
- Authoritative event snapshot, exact official participant set, immutable prediction/scorer event binding, and both-team/participant/field completeness. Team sports
  additionally require two separate reconciled personnel snapshots.
- UTC event-time/source-time/material-change validation against actual model time;
  a post-model lineup/roster update invalidates the *current* evidence pass.
- Equality of frozen feature vector hash and scorer-reported consumed vector hash.
- Explicit numerical feature-consumption status, completed-module evidence,
  model artifact identity, and no stage errors; script existence is not execution.
- `TEAM_EVENT_EVIDENCE_PREFLIGHT_FAILED` in the canonical failure registry.
- Official-publication guard HOLD for missing or contradictory proof **when the
  backend scorer is enrolled**. Probability fields are never rewritten.
- Per-bridge `evidence_integrity_required` registration switch (defaults false)
  and health visibility `cross_sport_evidence_enforced`.
- `can_execute=false`, calibrated-lower-bound and sole
  `V17_TERMINAL_REDUCER` authority unchanged.

## Why rollout is staged

The existing `team_event_feature_consumption` receipts are explicitly
**observability-only** and some scorers do not yet return verified consumption.
Enforcing the new contract for those scorers immediately would abruptly hold
otherwise valid pregame probability rows without producing new evidence.
No scorer is silently upgraded or certified in this PR.

`NOT_ENFORCED` is *not* a pass. A successful test envelope is *not* evidence
that any live sport has the required data. The digest produced here is for
deterministic audit identity; it is not a cryptographic source signature.

## Required producer work, per certified sport

1. Source an official event participant inventory and effective-time personnel/participant evidence for **both sides**
   or an exact field. Reconcile transactions, absences, lineup/rotation/goalie/QB/XI
   as relevant to the sport. Preserve forecast vs confirmed status.
2. Freeze authoritative input snapshots server-side, including IDs, provenance
   and source times. Never trust a browser/LLM-supplied 'verified' label.
3. Have the **fitted scorer** emit the hash of the numerical vector it actually
   consumed. Compare to the frozen post-hydration feature vector. Do not claim
   consumption merely because a source field was fetched.
4. Emit required/completed module list from actual backend module instrumentation,
   stage errors and model artifact version. Version/freeze the producer contract.
5. Bind these server-produced fields to the exact canonical event/candidate and
   immutable pregame prediction receipt; enforce model timestamp after the newest
   material update and before event start.
6. Run chronological and source-contradiction replay, current-slate canaries,
   calibration/bounds regression, complete row reconciliation, and authenticated
   Render/Supabase receipts. Only then set `evidence_integrity_required=True`
   for the exact certified bridge. Repeat per sport and verify production SHA.

## Publication boundary

The new preflight affects **official team/event ranking only**.
`MODEL_UNAVAILABLE` is reserved for missing controlling model capabilities.
`MODEL_INPUTS_INSUFFICIENT`, scorer failure, invalid output and market-data
issues stay distinct. A missing exact market price must not erase a valid
sporting probability.

Existing scorer paths will show `cross_sport_evidence_enforced=false` until
their authenticated producer is certified. A bridge registration or a passing
unit test does not change fitted-model availability or certification.

## Acceptance matrix

- 13 sport families audited by one contract; missing participant holds each.
- Rostered sports require two event-effective personnel snapshots and block
  stale/traded/unreconciled states.
- PGA full-field can use 2+ verified participants; no forced two-team assumption.
- Scorer feature hash mismatch, missing consumed-feature proof, absent executed
  modules, or update after inference -> HOLD.
- Opted-in official-publication result holds on absent/invalid proof, preserves
  fitted probability and returns the registered typed blocker.
- Legacy result is explicitly NOT_ENFORCED, not incorrectly called verified.
- No odds/LLM-derived probability, no fabricated calibration, no execution.
- CI, independent governance and authenticated production validation required
  before merging/promoting this as live protection.

**Owner:** V17 Engineering / AMRT producer-recovery by sport. LLP owns exact
team/event specialist contract, not global terminal approval. Related: #1252,
#662 and the existing cross-sport acceptance factory #1689.
