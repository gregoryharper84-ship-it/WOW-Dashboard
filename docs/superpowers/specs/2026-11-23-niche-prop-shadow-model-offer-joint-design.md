# Niche-market shadow model, signed-offer and joint-scenario design — 2026-11-23

## Intent
Provide a governed research path from independently attributed, chronologically frozen histories to fitted, evaluated, exact-line shadow probabilities, trusted-signer offer integrity checks and aligned joint outcome draws for Flex-style payout evaluation. Extend PR #2841's taxonomy, not the terminal reducer.

## Alternatives considered
1. Production specialists now: rejected; verified registered artifacts, source rights, current PrizePicks offers and prospective calibration receipts are missing for the exact routes.
2. Scrape or assume unofficial data: rejected without verified permission and provenance.
3. **Chosen:** offline research candidates, signed offer-integrity boundary, and joint scenario diagnostics. Keep HOLD until real data and independent scientific acceptance. Stack on PR #2841.

## Files and interfaces
- v17/niche_prop_fitted_shadow.py: separate per-category fitted logistic exact-line research challengers; prior-only feature inputs; chronological train/calibration/test; SHA-bound artifacts; untouched-test Brier/log-loss/ECE. No odds, screenshot hit-rate, or absent model fallback.
- v17/niche_prop_quote_integrity.py: HMAC-verified receipts issued by an authorized capture service; exact platform, offer, event, participants, line, period, side, tier, status and source digest. HMAC proves signer integrity, not PrizePicks-issued attestations or source usage permission. No scraper.
- v17/niche_prop_joint_shadow.py: one frozen aligned scenario ensemble, signed lineup payout, exact line binding, descriptive all-clear and expected gross-return diagnostics. Reject unaligned scenarios, unquoted push/void economics, incomplete draws and stale payout.
- Dedicated adversarial tests for each module.

## Invariants
- V17_TERMINAL_REDUCER sole terminal authority; can_execute=false.
- No production route activation, automatic wagers, certificate, registry promotion, RLS or secret changes or paid sourcing.
- Outputs SHADOW_UNCERTIFIED or RESEARCH_HOLD with null certified probabilities, lower bounds and authorization.
- Exact immutable market identity, pregame snapshot chronology and non-overlapping dates. Untouched held-out test.
- Never misrepresent internally signed capture as third-party source rights. Screenshots not included in fit.

## Acceptance
1. Reproducible synthetic RED/GREEN tests for chronology, split overlap, malformed features, missing identities and category mismatch.
2. Deterministic candidate fitting and untouched test Brier/log-loss/ECE, with no certification claim.
3. Signed offer tests for tampering, staleness, wrong market and missing rights.
4. Aligned scenario payout diagnostics for 5/5, 4/5, 3/5, same-event dependencies, missing draws and voids.
5. Existing taxonomy/preflight tests plus exact-head hosted CI and independent code review.
6. Production separately requires rights-cleared official sporting history and current offered market evidence, real immutable pregame forward receipts, independently verified result settlement, measured calibration and scientific certification.

## Risk
Research software cannot make an independently verified production probability or live offer appear where source rights and real data do not exist. Fail closed.
