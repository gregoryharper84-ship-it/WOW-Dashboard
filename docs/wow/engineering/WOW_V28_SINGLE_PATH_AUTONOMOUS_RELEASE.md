# WOW V28 — Minimum Engineering Release Path

**Effective governance decision:** owner-approved [issue #1830](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1830). The earlier #2667 independent Gemini/SIRT premerge design is **superseded for routine engineering**. It remains historical audit evidence, not an extra requirement for ordinary repairs. Real independent scientific model certification remains mandatory.

## Single required release path

Existing PR → complete exact-head diff and negative security regressions → three App-ID-15368 GitHub checks SUCCESS (required-three regression, governed probability backend, additional required regression) → one authenticated owner-authorized protected merge → verify Render web + worker LIVE revision parity and actual Action/receipt behavior.

No paid reviewer, repeated SIRT approval checkpoint, automatic source-review artifact, QA App PASS, Release App PASS, extra receipt certifier, or provider quota prerequisite is an additional routine merge authorization gate. QA and Release Apps can continue to produce **truthful diagnostic failures**. No diagnostic failure may be mislabeled as PASS; investigate genuine security defects. The owner must not claim independent review when none occurred.

## Current rollout gap

As verified 2026-11-15, live ruleset #24907422 still requires FIVE contexts: it pins QA App 5245832 and Release App 5245853 in addition to the three Actions checks. This source specification and an owner-approved issue **do not alter native ruleset enforcement**. The one-time GitHub administrator change documented in `docs/WOW_STABILITY_FIRST_MINIMUM_GOVERNANCE.md` must be applied and read back before any five-check failure can cease to be a native block. No ruleset bypass, context spoofing or forced protected push is authorized.

Preserve real high/critical security findings, stale/foreign SHA checks, strict protected merges, `can_execute=false`, `V17_TERMINAL_REDUCER`, sport-specific fitted probability calibration, fresh pregame evidence, and independent model certification. No Full Model pick is certified by the engineering release path. Do not substitute source CI or a Render health check for actual scoring evidence.

**Status:** governance policy ratified; live technical enforcement pending repository admin reconciliation and production acceptance.
