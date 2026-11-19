# WOW Option A — One Protected Release Path

**Goal:** No paid AI reviewer, no new workflow/service, no extra routine merge gate. Reuse current GitHub checks, QA App, Release Authority and fitted specialist replay.

## The entire operating flow

1. **Build one PR:** classify the changed files by diff (R/P/C). Only the current exact-head commit qualifies. The three required GitHub Actions checks must all be SUCCESS from App 15368. Negative security/fail-closed tests apply to P/C. CI runs automatically.
2. **One genuinely independent approval for P/C:** one non-author, non-owner GitHub User reviews and approves that exact head. Its real GitHub user ID is configured once in the existing protected `wow-qa` and `wow-release` environments as `WOW_SIRT_REVIEWER_USER_ID`; do not accept an ID or a PASS from the PR. QA and Release independently re-read GitHub. R-class routine work does not wait for this reviewer.
3. **One-time switch, only after this P-class migration itself is independently accepted and merged:** set `WOW_QA_SIRT_VERIFICATION_ACTIVE=true`, `WOW_QA_INDEPENDENT_REVIEW_ACTIVE=false`, `WOW_QA_ZERO_CREDIT_MODE=false` in the applicable existing protected environments. Existing QA App and Release Authority publish the exact-head outcomes. The Gemini path is never a fallback for SIRT failure.
4. **One production acceptance:** protected merge, verify Render web + worker LIVE at accepted main SHA, exercise one real governed Action → Supabase persisted receipt → V17 terminal result. Failed check, missing review, unpublished QA check, stale SHA or incomplete production readback = HOLD.

**Fitted model science is a separate data check, not another PR workflow.** A C-class specialist promotion still requires its real immutable training/evaluation inputs, exact fitted artifact and calibrated lower-bound hashes, independently reproduced forward grades/calibration and scientific acceptance. SIRT source review alone can never certify or publish probabilities.

## Fail-closed contracts retained

- `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.
- Reviewer cannot be owner, PR author or head-commit author. Reject stale/dismissed approvals, wrong App check runs, incomplete pagination, conflicting activation modes and forged identities. Guard red→green regressions permanently.
- A real provider failure becomes a signed red **PR-head** QA HOLD, not a failed **main** deployment check; QA App publication failure remains hard-fail.
- No direct main write, reviewer self-approval, calibration/threshold relaxation, heuristic probability fallback or live wagering/trading.
- A green regression check or SIRT `EVIDENCE_READY` is never merge, deploy, certification or execution authority.

**Status:** Implementation in PR #2732. Do not claim DONE until exact-head CI, independent P-class approval, protected merge and real production acceptance are all verified. Incident #2020 and the existing `.agents/memory/` entry remain the only durable incident/learning log.
