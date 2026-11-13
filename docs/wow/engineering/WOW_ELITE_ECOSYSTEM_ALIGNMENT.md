# WOW Elite Ecosystem Alignment — Class A Contract

## Authority
- Neutral Ecosystem Conductor: durable coordination and exact identity/lease/handoff reconciliation; does not own sporting probability, safe hold, approval, or publication.
- Betting Intelligence: product objectives, completeness and end-user acceptance.
- Engineering: implementation, tests and governed delivery; retains repair ownership.
- SIRT: independent reliability diagnosis, systemic prevention and governed safety holds.
- Independent QA: independent evidence-based release verification; may return PASS/HOLD/FAIL, never self-certifies Engineering.
- V17_TERMINAL_REDUCER: sole global terminal publishing authority.

## Closure evidence
Use `python artifacts/wow-engine/v17/independent_closure_contract.py path/to/receipt.json` as one *necessary* gate for FIXED_AND_VERIFIED.
The validator rejects self-review, self-QA, missing evidence, mismatched reviewed/tested head SHAs or merged/deployed/QA release SHAs, unapproved B/C changes, failed checks and protected-invariant drift. Acceptance from this local tool is not proof of real-world deployment or independent human/agent identity: GitHub/Render/Supabase evidence must be independently authenticated by separate release controls. Do not wire test fixtures as production receipts.

## Operational alignment
1. Product creates acceptance criteria and severity.
2. Conductor preserves incident ID, owner, queue state, lease, revision and typed blocker through all handoffs.
3. SIRT independently reproduces systemic failures and proposes prevention requirements.
4. Engineering changes code and tests; review challenges before authorized release.
5. QA checks the exact deployed SHA and independent replay; HOLD keeps work owned and tracked.
6. Product checks usability; SIRT tracks recurrence. Only independent closure evidence permits FIXED_AND_VERIFIED.

## Integration requirements for follow-up PR
Connect the checker to existing protected closeout workflow only after verifying the active receipt schema and authorization roots. Avoid parallel queues and databases. Enforce authentication of reviewer identity and production SHA at source, not from arbitrary JSON assertions. This PR does not claim runtime deployment or completed protected CI integration.

## Merge-strategy identity binding
The reviewed/tested PR head may legitimately differ from the deployed merge/squash commit. The validator therefore compares reviewed=tested and merged=deployed=QA_verified independently. `release_identity_authenticated=true` is only an assertion within a receipt, **not** proof by itself. The protected integration must derive this claim from GitHub's authenticated PR merge identity, verify the exact release SHA deployed by Render, and bind independent QA acceptance to that release SHA. Until these independent sources are checked, **HOLD** remains the only permitted production-closure decision.
