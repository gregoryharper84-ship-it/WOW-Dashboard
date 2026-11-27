# Niche prop fitted-shadow and offer/joint research acceptance — 2026-11-23

## What is implemented
Stacked on #2841, these additive Python modules implement deterministic research contracts:

| Module | Code responsibility | Status |
|---|---|---|
| niche_prop_fitted_shadow.py | Per-exact-stat/period/side fitted logistic challenger trained on source-claimed pregame features; separate temporal Platt calibration and untouched chronological test with Brier, log loss, ECE | SHADOW_UNCERTIFIED |
| niche_prop_quote_integrity.py | HMAC-signature, source-rights identifier, immutable payload digest, current capture timestamp, exact offer/participant/period/line/tier and whole-lineup payout binding | INTERNAL_CAPTURE_INTEGRITY_ONLY |
| niche_prop_joint_shadow.py | Aligned joint scenario outcome vectors, a current signed exact lineup payout schedule and distribution of five-, four-, three-hit outcomes | SHADOW_UNCERTIFIED |

No import or registration in the production probability reducer, trading API, GPT editor or publication registry. The pre-existing #2841 preflight still HOLDs unsupported two-kicker routes.

## Critical distinction
A locally generated HMAC verifies that bytes were signed by whoever held the shared secret. It does NOT prove that PrizePicks published the price, licensed its extraction, or that any provider permission claim is true. The verifier outputs source_content_independently_verified=false and provider_rights_independently_verified=false by design. It cannot be used as a market-edge/publishability gate or trading authority by itself.

The jointly aligned scenario ensemble is *caller-supplied*. This module correctly computes payout outcomes under the supplied empirical distribution but cannot prove those draws came from a calibrated joint fitted generator. No synthetic independence multiplication is substituted. All estimates are explicitly RESEARCH ONLY.

## Real data required to continue
- NFL 1Q two-kicker field goals: verified play-by-play including official kickoff time, all first-quarter made FG events and owning kickers, starting lineup, pregame offense/defense red-zone and possession rates, coaches' fourth-down choice, wind/conditions and game-event identity. Count two kickers as **one offered market**. Need independently validated matching lines/side and settlement semantics.
- NBA unders: full official player game boxscores for 3PA, FTA, PF, TOV, minutes and pregame team/rotation, matchup-adjusted opportunity, injury/usage shifts and availability. Each stat subtype gets its own exact route, not one pooled under specialist.
- NFL 1Q QB pass attempts/fantasy points: official quarter play-by-play and provider-specific scoring rules with verified QB/period identities; free public stats alone cannot imply an offered PrizePicks line.
- Actual current market offers, selection tiers and payout tables through an **authorized** source captured before tip-off/kickoff. No unlicensed PrizePicks scraping, no invented price or paid source by default.
- An independently operated capture signer, sealed readback, genuinely frozen pregame time and official settlement, plus rights approval external to the caller.

## Scientific gates
1. Complete chronological training, later calibrator and untouched final holdout (minimum code tests 80/30/30; not enough on its own for scientific certification), with group/event non-overlap and no leakage.
2. Independently verify each feature was available at its stated prior time; the fixture hashes and source IDs in these modules are **claims** pending authentic source verification.
3. Publish evaluation report for every exact stat, period and side including sample size, prevalence, Brier, log loss, ECE, predictive interval coverage, status by injury and role shifts and comparison against time-correct no-vig baselines.
4. Replay prospective independent pregame captures (including losers and rejected candidates) into an immutable ledger and independent grade. Operator-selected winning screenshots are never included as unbiased training/validation.
5. Validate joint scenario generator (coherence, tails, same-game and cross-leg correlation), offered Flex payout, push/void/DNP and tier effects, and variance/uncertainty of payout-based EV. This research helper holds push/void until an exact quoted settlement schedule is available.
6. Independent scientific model certification, calibration and calibrated lower bound followed by exact-head protected merge, production deployment and authenticated readback. Do not treat tests, signed local capture or simulation as certification.

## Regression command
PYTHONPATH=artifacts/wow-engine python -m pytest -q artifacts/wow-engine/v17/test_niche_prop_fitted_shadow.py artifacts/wow-engine/v17/test_niche_prop_quote_integrity.py artifacts/wow-engine/v17/test_niche_prop_joint_shadow.py artifacts/wow-engine/v17/test_niche_prop_research.py artifacts/wow-engine/v17/test_slip_research_preflight.py

## Safety
V17_TERMINAL_REDUCER sole terminal authority; can_execute=false. No placed entries, player-prop bets or orders. No credentials, production migrations, autonomous source access or paid service activation.
