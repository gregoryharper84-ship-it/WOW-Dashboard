# NFL Daily shared-row-ceiling unknown-count regression

- Date: 2026-11-05
- Typed reason: `NFL_PROP_ROW_CEILING_EXHAUSTED`
- Mechanism: When MLB consumed every Daily acquisition slot, the zero-budget NFL source short-circuited to `COMPLETED`, zero discovered and zero unattempted counts, even though candidate discovery never ran; composite `setdefault` silently preserved the false zero.
- Detection point: PR #2485 exact-head diff review, before independent signoff or production release.
- Fix: Explicit fail-closed `NOT_ATTEMPTED_ROW_CEILING`, null unknown counts, and source-leg status carried into the Daily per-sport receipt. No additional calls, no fabricated model probability.
- Prevention artifact: `artifacts/wow-engine/v17/test_daily_nfl_prop_acquisition.py` zero-budget and MLB-exhaustion regressions; canonical `artifacts/wow-engine/docs/failure_codes.md` entry.
- Link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2485
- Verification: source-level causal review complete; exact-head GitHub CI and production readback still required. No FIXED_AND_VERIFIED claim yet.
