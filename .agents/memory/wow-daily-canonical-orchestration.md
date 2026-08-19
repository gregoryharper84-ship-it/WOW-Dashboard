---
name: WOW Daily runtime contracts
description: Fail-closed moneyline orientation and single-source canonical daily-summary selection.
---

# WOW Daily runtime contracts

## Typed moneyline orientation

**Rule:** Moneyline participant orientation has three states: HOME, AWAY, and unresolved. Missing, malformed, or conflicting markers never default to HOME. Canonical orchestration serializes unresolved as `SIDE_UNKNOWN`; direct scoring stops with a typed data-contract failure before probability, inversion, calibration, or classification.

**Why:** A silent HOME fallback evaluated away-side MLB candidates from the home perspective and could publish a probability for the wrong participant.

**How to apply:** Any new moneyline entry point must use the shared typed resolver and preserve unresolved state. Never infer orientation from field absence, field order, team naming, or market prices.

## Canonical-manifest-first daily summaries

**Rule:** A canonical daily manifest is authoritative only when it is finished with an accepted completed status, explicitly reconciled, and its persisted row count exactly matches its discovered count. The latest qualifying manifest owns counts, rows, flags, filters, and source metadata as one unit.

**Why:** Mixing canonical counts with legacy rows creates internally contradictory summaries. Treating a canonical query failure as “no manifest” can silently resurrect stale legacy results.

**How to apply:** Legacy results are fallback-only when canonical lookup succeeds and explicitly returns no committed run. Propagate canonical lookup failures. Never select counts from one store and rows or flags from another.

## Preserved boundaries

**Rule:** Runtime safety changes are contract and source-selection gates only. They do not alter probability formulas, calibration, thresholds, terminal-label taxonomy, execution authority, governance, or active-lane coverage.

**Why:** Safety cleanup must stop bad inputs and inconsistent reads without changing scoring economics.

**How to apply:** Keep future orientation and summary fixes at the pre-model or storage-selection boundaries. Treat model or lane behavior changes as separate work.
