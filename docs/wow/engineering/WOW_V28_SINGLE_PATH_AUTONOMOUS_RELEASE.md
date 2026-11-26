# WOW V28 — Minimum Engineering Release Path

**Effective governance decision:** owner-approved [issue #1830](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1830) expressly removes the separate independent SIRT/source-review premerge prerequisite for routine engineering, **including protected/security-adjacent Class P engineering source**, while preserving exact-head security tests, material defect remediation and one owner-authorized merge. The earlier #2667 independent Gemini/SIRT premerge design is **superseded for routine engineering**. It remains historical audit evidence, not an extra requirement for ordinary repairs. Real independent scientific model certification remains mandatory.

## Single required release path

Existing PR → complete exact-head diff and negative security regressions → three App-ID-15368 GitHub checks SUCCESS (required-three regression, governed probability backend, additional required regression) → one authenticated owner-authorized protected merge → verify Render web + worker LIVE revision parity and actual Action/receipt behavior.

No paid reviewer, repeated SIRT approval checkpoint, automatic source-review artifact, QA App PASS, Release App PASS, extra receipt certifier, or provider quota prerequisite is an additional routine merge authorization gate. QA and Release Apps can continue to produce **truthful diagnostic failures**. No diagnostic failure may be mislabeled as PASS; investigate genuine security defects. The owner must not claim independent review when none occurred.

## Current live rollout status

An authenticated GitHub readback on 2026-11-15 **after the owner saved ruleset #24907422** confirmed exactly THREE required GitHub Actions App-ID-15368 contexts; QA App 5245832 and Release App 5245853 are now advisory, not required status checks. Strict status matching, native branch protections and zero bypass actors remain. This source specification did not alter the native ruleset: that separate owner/admin action was verified directly. Reread the live ruleset and exact-head checks before every protected merge; no bypass, context spoofing or forced protected push is authorized.

Preserve real high/critical security findings, stale/foreign SHA checks, strict protected merges, `can_execute=false`, `V17_TERMINAL_REDUCER`, sport-specific fitted probability calibration, fresh pregame evidence, and independent model certification. No Full Model pick is certified by the engineering release path. Do not substitute source CI or a Render health check for actual scoring evidence.

**Status:** governance policy ratified and live ruleset reconciled to three checks (verified 2026-11-15); PR merge and production acceptance must be verified separately.
