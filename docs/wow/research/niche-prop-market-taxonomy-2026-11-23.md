# Niche-prop research taxonomy (2026-11-23)

**Owner:** Project Hub #2076. **Change class:** additive, research-only observation.
This is **not** a new fitted specialist, a probability model, a provider collector,
a PrizePicks offer attestation, or evidence of profitable forward performance.

## Motivation and strict boundaries

An operator-provided three-card retrospective showed a concentration of
first-quarter NFL two-kicker field-goal combo overs and NBA low-volume stat
unders. Those are selected historical successes, not a representative cohort.
Never train fitted weights, infer a prior, calibrate confidence, assign a
positive edge, or choose card leg counts from such screenshots. Do not commit
the operator's slips, names, monetary record or private screenshots. Tests use
anonymous *synthetic* shape fixtures only.

## Category contract

1. **NFL 1Q two-kicker FG made combo MORE 0.5** is a **single** offered combo
   market with two separately identified kickers tied to one official event
   and quarter settlement. Do not count it as two bets, use either kicker's
   individual FG chance, or multiply kicker probabilities as independent.
   Any missing two-player/official identity is an identity gap. Causal
   features include 1Q drive/field-goal possession opportunity, red-zone
   touchdown vs field-goal decisions, kick range, weather, fourth-down
   tendencies and missing/changed kicker.
2. **NBA low-volume full-game LESS:** 3PT attempts, personal fouls, free-throw
   attempts, turnovers. Require the *exact stat* (3PT attempts is not 3PT
   makes), exact threshold, period, player and role-conditional opportunity,
   opponent matchup and scenario tails. Use actual full-game settlement rules.
3. **NFL 1Q QB passing attempts MORE:** independently evaluate early drive
   count, play rate, pass rate and script; do not convert full-game means.
4. **NFL 1Q QB fantasy score MORE:** evaluate the provider's first-quarter
   fantasy-scoring distribution, including passing/rushing/TD/turnovers,
   separately from attempts. A narrow historical clear is not a safe anchor.

A different line, quarter, direction or stat remains a distinct thesis.
Projection tier (STANDARD/GOBLIN/DEMON) and exact current payout must come
from the existing PrizePicks preflight (#2741); do not assign a 10x multiplier
to a current 5-pick slate without quote verification.

## How this is used

- `v17/niche_prop_research.py` is a deterministic **taxonomy and retrospective
  description** library with no scoring or bankroll path.
- `v17/slip_research_preflight.py` adds `niche_prop_research` to each valid
  research leg and `niche_category_counts` to each card, while preserving
  existing HOLD criteria and adding an explicit HOLD for an NFL two-kicker
  combo with no dedicated registered fitted route. It cannot be disguised as
  an individual player prop. No execution or certification authority is added.
- Existing exact quote, role/opportunity, material news, stale-line, market
  identity, payout and cross-slip concentration checks remain controlling.
  Taxonomy coverage is not market availability. A provided reference is still
  untrusted until independently authenticated.
- All fields `probability`, `calibrated_lower_bound`, `expected_value`, and
  `certified_joint_probability` produced by this research layer are **null**.
  Neither `can_execute` nor terminal authority changes.
- Screenshots alone cannot be used as prospective calibration; only immutable,
  true-pregame, exact-market, separately settled model receipts can qualify.

## Prospective acceptance before claims of improvement

1. Discover and freeze **all** candidates screened, including rejection and
   losing candidates, with immutable current-offer/source/lineup receipts.
   Separate availability from backend specialist readiness and market price.
2. For every qualifying fitted *exact* route, compare chronological untouched
   holdout performance against a time-correct baseline, with Brier/log loss,
   calibration, uncertainty and outcome/period/push/void definitions.
3. Treat the two-kicker combo as a single event-outcome distribution and
   estimate slip payouts from validated **joint** simulations or other
   independently justified dependence modeling. Never multiply leg hit
   rates by default. Validate multi-leg 5/5, 4/5 and 3/5 rates prospectively.
4. Count repeated exposures across slips, missing quotes, postgame evidence,
   lineup/context invalidations and near-boundary clears. Use an independently
   attributed sample not chosen because it won. Require enough sample to
   evaluate uncertainty and non-overfit economic performance.
5. Only after exact-head CI, existing protected-release approvals, independent
   scientific model certification (if a new route is promoted), actual deploy
   and authenticated current-event readback can anyone describe the live route
   as operational. Never equate this taxonomy with a completed deployment.
6. No new paid sources, automated wagers, entry submissions, bypasses or
   changes to `V17_TERMINAL_REDUCER` or `can_execute=false`.

### Focused tests

```bash
PYTHONPATH=artifacts/wow-engine python -m pytest -q \
  artifacts/wow-engine/v17/test_niche_prop_research.py \
  artifacts/wow-engine/v17/test_slip_research_preflight.py
```
