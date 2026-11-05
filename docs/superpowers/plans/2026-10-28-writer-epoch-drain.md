# Durable writer epoch closure implementation plan

> **For agentic workers:** use superpowers:executing-plans in this session.

**Goal:** close mutation admission durably before any lease release.
**Architecture:** extend the existing review-only fence and RPC adapter; prove
ordering using the existing isolated PostgreSQL workflow, never production.
**Tech stack:** Python, PostgreSQL PL/pgSQL, pytest, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-28-writer-epoch-drain-design.md

## Global constraints

- can_execute=false; V17_TERMINAL_REDUCER; no live orders.
- No source/SQL activation or credential changes without independent review.
- Preserve pending intents and existing owner/App provenance.

## Review focus

- Concurrent close/begin: either pending is preserved or new work is denied.
- Expired lease with unknown writes: never permits takeover.
- SQL NULLs: never turn authorization into implicit success.
- Legacy direct transitions: cannot reopen or erase an old epoch.
- Closed epoch recovery: readback/confirm only, never duplicate external POST.

## Task 1: Close and drain protocol

Files: existing engineering_fenced_writer.py, staged writer migration,
tests/test_v17_engineering_fenced_writer.py, new isolated PostgreSQL test.

- [ ] Write tests for close_epoch(incident_id, owner_id, epoch, lease_group),
  exact RPC identity, DRAINING/DRAINED/DENIED and malformed responses.
- [ ] Run RED; implement close_epoch adapter; run offline suite GREEN.
- [ ] Write real SQL tests for pending close, admission rejection, release,
  monotonic takeover, NULLs, deletion, and concurrent close/begin.
- [ ] Implement immutable closure records and guarded legacy transitions.

## Task 2: Existing proof workflow and review handoff

Files: existing release-fence-postgres-proof.yml, acceptance document.

- [ ] Add isolated local-only writer tests to the existing proof workflow.
- [ ] Run focused and adjacent tests; run broad suite and record limitations.
- [ ] Commit source correction to existing owner-source #2188 using lease CAS;
  never push to an App-owned branch or transfer old approvals.
- [ ] Obtain fresh review; inspect exact-head PostgreSQL and source CI results.
- [ ] Record precise remaining production cutover authority/evidence gaps.
