# 2026-10-01 Spread Forward Runtime Memory Headroom Receipt

Status: PR_CREATED candidate
Change class: B — serving/runtime reliability only

## Expected
Interactive spread/run-line scorer requests should complete or return their exact typed blocker without exhausting the single production web instance.

## Observed production evidence
On `wow-governed-probability-engine` (Render free plan, single instance, 0.15 CPU), runtime metrics between 18:30Z and 20:45Z showed repeated process-instance replacement while heavy spread/user-path work was occurring. CPU repeatedly reached the 0.15 limit. Memory repeatedly climbed to roughly 500–536 MB before a new instance appeared with a low fresh-process RSS. The affected user run then returned spread scorer/transport failures even though earlier serialized production spread canaries had passed.

This is consistent with the existing #1087 overload failure family but is not limited to post-deploy fan-out: an interactive spread fit can leave too little RSS headroom for the next request on the constrained web instance.

## Root cause addressed by this patch
`v17/spread_margin_forward_fit.py` constructs transient NumPy/scikit matrices and estimator state to produce a compact immutable `MarginDistributionArtifact`. Those large working allocations are not part of the returned artifact. CPython/glibc may retain freed arenas in RSS after references are released, which is material on a ~512 MiB worker.

The patch:
- freezes the exact same artifact first;
- drops all transient fit references;
- runs garbage collection;
- best-effort calls glibc `malloc_trim(0)` when available;
- treats heap trimming as fail-open infrastructure so unsupported libc environments never change scorer terminal behavior.

## Governance
No coefficients, features, split rules, calibration residuals, thresholds, probability math, publication authority, specialist ownership, or execution permission are changed.

`runtime_generation=V17_ACTIVE`
`terminal_authority=V17_TERMINAL_REDUCER`
`can_execute=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Verification contract
1. Existing exact-artifact parity test must remain green.
2. Typed leakage/schema failures must remain unchanged.
3. New regression proves the memory-release hook runs only after a successful artifact is frozen.
4. After merge/deploy, rerun serialized spread production canaries and inspect Render memory/instance continuity.
