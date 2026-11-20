# WOW AMRT — Autonomous Model Recovery Team

> **Mission:** Debug. Dissolve. Deploy. Restore broken, missing, degraded or
> incomplete model capabilities to **independently verified production use**.
> AMRT is the permanent Model Restoration & Recovery Unit under Production
> Engineering. It is not a second orchestration service or status-only team.

**[AMRT Project Hub / workboard — #2078](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2078)**

## Canonical sources (reuse, never fork the control plane)

- [AMRT recovery and production-closure contract](../../docs/wow/engineering/WOW_AMRT_RECOVERY_TEAM.md)
- [AMRT seven-role bootstrapping / validation](../../docs/wow/engineering/WOW_AMRT_TEAM_BOOTSTRAP.md)
- [Machine-readable specialist registry](../../artifacts/wow-engine/v17/amrt_team_registry.json)
- [Operational lane decisions](../../artifacts/wow-engine/v17/amrt_operational_lanes.py)
- [Existing Engineering receipt bridge](../../artifacts/wow-engine/v17/amrt_receipt_bridge.py)
- [Existing Engineering execution priorities — #1128](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1128)
- [Controlling model availability incident — #1562](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1562)

Do not add new agents, queues, privileged principals, schedules, DB tables, or release
routes merely to make this project. The Conductor, existing authenticated Engineering
worker, scoped lease/writer protocol, immutable receipts, independent QA and Release,
and `V17_TERMINAL_REDUCER` remain authoritative.

## Ownership and execution

| Function | Work / handoff |
| --- | --- |
| Blocker Sentinel | Detect, fingerprint and deduplicate incidents; capture trusted evidence |
| Root Cause Investigator | Reproduce, isolate first failing boundary, and document source evidence |
| Model Recovery Engineer | Restore scorer/package/stage/publication without silent model substitution |
| Data & Integration Engineer | Restore source hydration, event identity and persisted data contracts |
| Code & CI Engineer | Prepare minimal lease-safe change, focused tests and negative regressions |
| Independent QA | Independently challenge the exact source SHA and safety invariants |
| Release & Verification | Verify authorized merge, deployed SHA, live Action and DB readback |

One existing issue remains accountable for each incident. Specialist handoffs do not
create additional accountable incident owners. External providers are implementation
options—not a hard dependency for diagnostics or deterministic repair paths.
Use authorized alternatives when a provider is rate-limited, without claiming
successful generation, authorizing spending, or relaxing control-plane safeguards.

## Current recovery work

Use the linked issue / PR as the live source of truth, not a copied status:

- **NFL scorer / publication:** [#1841](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1841) and [PR #1842](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1842)
- **Cross-sport identity / guard:** [#1806](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1806), [PR #1835](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1835)
- **AMRT worker intake:** [#1978](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1978), [PR #1843](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/1843)
- **Provider survival:** [#2034](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2034), [PR #2035](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2035)
- **Gemini / alternative provider:** [#2014](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2014), [PR #2058](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2058)

Prioritize NFL, NBA, NHL and NCAAF according to the controlling Engineering issue
#1128, without hijacking a separately owned lease or bypassing a valid safety HOLD.

## Minimal status contract

An incident records: **owner, exact failing boundary, reproducer/proof, next valid
action, source PR and exact SHA, tests and negative cases, independent review,
approved merge SHA, Render live SHA, Action/model/terminal/persistence readback,
and independent closure receipt**. Cite existing durable receipts rather than
maintaining a competing database or duplicating issue state in this directory.

Allowed plain-English states:
- **OPEN / INVESTIGATING** — defect not yet repaired.
- **PATCH_READY / REVIEW_PENDING** — source exists but not independently accepted.
- **BLOCKED_WITH_EXACT_REASON** — a specific gate remains, with owner and re-entry action.
- **FIXED_AND_VERIFIED** — *only* after independent end-to-end production proof.

A healthy fallback router, green CI, configured key, or successful dispatch cannot
be called recovered model capability without the matching live model receipt.

## Definition of done

1. Root cause and regression are reproducible with authentic event/route evidence.
2. Smallest authorized change passes exact-head CI and independent negative tests.
3. Distinct QA / Release authorizations, protected merge and deployment are proved.
4. Same actual production run reconciles scorer output, calibration, terminal
   decision, Action response, persisted rows and the exact live revision.
5. Independent SIRT/final verification signs off; recurrence is tracked for 7/30 days.

**Safety stays intact:** `can_execute=false`; dry-run only, no trading or market
orders. Class C fitted-model / probability changes require separate certification.
This project README and #2078 do **not** activate a worker, merge a PR, fix an
incident, or certify production.
