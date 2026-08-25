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

## Source-union completeness

**Rule:** Canonical reconciliation includes both exact terminal-row accounting and the completeness of every attempted discovery source. A failed or partial primary or backup source makes the manifest non-committable even when another source supplies terminally scored rows.

**Why:** A usable alternate board cannot prove that the cross-source union is complete; treating it as reconciled hides omitted lanes behind an apparently valid candidate set.

**How to apply:** Preserve per-source statuses from discovery through scanner diagnostics and manifest reconciliation. Expose row and source reconciliation separately, but require both before a canonical summary can be selected.

## Preserved boundaries

**Rule:** Runtime safety changes are contract and source-selection gates only. They do not alter probability formulas, calibration, thresholds, terminal-label taxonomy, execution authority, governance, or active-lane coverage.

**Why:** Safety cleanup must stop bad inputs and inconsistent reads without changing scoring economics.

**How to apply:** Keep future orientation and summary fixes at the pre-model or storage-selection boundaries. Treat model or lane behavior changes as separate work.

## Immutable scoped moneyline Daily runs

**Rule:** A narrow remaining-today moneyline run persists scope plus the acknowledgement-time instant, filters on that immutable instant in the stored IANA timezone, and gives each scoped selection an event-aware identity.

**Why:** A delayed/retried executor must not move the requested event boundary, and the same team can have two same-day events (such as a doubleheader). A team-only identity silently collapses those distinct selections. An unavailable h2h source must degrade the run rather than masquerading as a successful empty board.

**How to apply:** Keep the broad prop board outside this lane; use only the established h2h/moneyline validation and snapshot handoff. Normalize scoped acquisition failures, including quota-proactive skips, into the canonical source-coverage failure vocabulary, preserve existing terminal labels, and never mutate a stored scope on an idempotent retry.
