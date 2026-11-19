# WOW V17 — Solo-Maintainer-Compatible Automated Verification Design

**Date:** 2026-11-19  
**Incident:** #2020 (constraint recorded in PR #2732 comment `#issuecomment-7020085885`)  
**Status:** Unmerged P-class candidate branch (`feature/v17-solo-automated-verification-20261119`). PR #2732 remains unmerged. No ruleset, production deployment, or model probability was changed. `can_execute=false` and `V17_TERMINAL_REDUCER` remain intact.

## 1. Operating Model — Three Distinct Lanes

1. **Normal model operation (no human reviewer required):**
   - Already-certified V17 specialists scoring ordinary rows require **zero per-pick human review**.
   - Scientific invariants remain mandatory: `specialist_certified=True`, `calibration_valid=True`, `inputs_fresh=True`, `pregame_eligible=True`, `terminal_authority=V17_TERMINAL_REDUCER`, `heuristic_fallback_used=False`, `can_execute=False`.
2. **Routine engineering changes (Class R):**
   - Strictly restricted to `CLASS_A_EXACT_PATHS` with safe file operations (`added`/`modified`; no `removed`/`renamed`, no trust-root or probability/model paths).
   - Uses existing three native GitHub Actions checks (App `15368`) + `WOW V17 change impact gate` (App `15368`), deterministic tests, and the existing owner-signed release path (`.github/workflows/wow-v17-owner-signed-release.yml`) where permitted.
3. **Protected engineering (Class P) & Model/Calibration/Certification (Class C / P+C):**
   - Research candidate: adds reproducible automated checks (`solo_automated_verification.py`) to existing distinct QA (`5245832`) and Release (`5245853`) Apps. These checks are useful evidence, but **do not replace independent P/C authorization** while the sole owner controls both Apps.

## 2. Investigation Findings: What Existing QA (`5245832`) & Release (`5245853`) Apps Can and Cannot Prove

| Guarantee | Proven by existing QA + Release Apps? | Exact mechanism / honest boundary |
|---|---|---|
| Execution isolation from PR code | **Yes** | Both workflows check out protected `main` (`ref: ${{ github.sha }}`) only and never execute PR branch code. |
| Distinct App-token & check-run separation | **Yes** | QA (`5245832`) and Release (`5245853`) mint separate `checks:write` tokens and cannot forge each other's `integration_id`-pinned checks or App `15368` native CI checks. |
| Exact-head TOCTOU & pagination integrity | **Yes** | Authenticated GitHub REST API reads PR identity before and after file/check collection; rejects redirects, truncated pagination, forks, drafts, and stale SHAs. |
| Security + adversarial + calibration replay verification | **Yes** | P/C requires `WOW CodeQL / python` (App `15368`) + exact-head `adversarial_receipt` (zero failures, zero unresolved high/critical findings). Class C/P+C additionally requires immutable `calibration_replay_receipt` with 64-hex artifact/lower-bound/source digests, chronology, and leakage-free proofs. |
| Independent human review | **No** | Explicitly recorded as `independent_human_review_performed: false` in every receipt and check summary. Never laundered. |
| Independent administration from sole owner | **No (under solo owner)** | The sole maintainer administers the repo, environments and both Apps. A self-supplied JSON `admin_user_id` or 64-hex digest does not authenticate an external issuer. No independently administered verifier with trustworthy signature/provenance checking is configured. |

## 3. Fail-Closed Default & Opt-In Activation

- **Running code on `main` is unchanged**: `WOW_QA_SOLO_VERIFICATION_ACTIVE` defaults to unset/`false` and is mutually exclusive with `WOW_QA_INDEPENDENT_REVIEW_ACTIVE`, `WOW_QA_ZERO_CREDIT_MODE`, and `independent_review_canary`.
- **Fail-closed on P/C independence**: P/C changes return `HOLD` (`SOLO_PROTECTED_INDEPENDENT_ADMIN_UNPROVEN` / `SOLO_INDEPENDENT_ADMIN_NOT_PROVEN`) even when deterministic checks pass. This verifier **cannot** supply substantive independent protected approval.
- **Owner-admin and unsigned-attestation non-bypass**: `WOW_QA_SOLO_OWNER_ADMIN_ACCEPTED=true` cannot turn a protected HOLD into PASS. An external-admin JSON, even one containing a different numeric user ID and a digest-looking string, is rejected absent authenticated, independent issuer provenance. The legacy opt-in argument remains accepted by the CLI for compatibility but grants no protected approval.
- **Required future evidence**: an independently administered verifier with independently validated signed receipts and protected-bootstrap admission would be needed before any P/C approval path could be authorized. No such mechanism is currently proven, and this PR must not be activated as a replacement for protected sign-off.
- **Non-authority invariants**: `merge_or_release_authorized=False`, `model_certified=False`, `probability_publishable=False`, `can_execute=False`, `dry_run_only=True`, `independent_scientific_acceptance_required=True`, `protected_release_authority_required=True`.
