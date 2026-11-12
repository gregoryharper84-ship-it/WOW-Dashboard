# V17 offline model–market baseline: paired-source proof (2026-11-12)

- **date:** 2026-11-12
- **typed_reason:** REPLAY_MARKET_QUOTE_PAIR_MISMATCH; REPLAY_EXACT_IDENTITY_MISMATCH; REPLAY_EVENT_CONTEXT_CONFLICT
- **mechanism:** A selected-side decimal price and arbitrary opposite price were accepted as the same bookmaker snapshot without an independent opposite-side identity. Settlement had no explicit market/period/selection/line check, and two distinct selections could attach conflicting teams to one official event. This predicted falsely improved descriptive Brier/log-loss comparisons even with syntactically valid hashes.
- **detection_point:** Offline research evaluator negative-path tests, before historical backtest or fitting. Required evidence should have been checked when accepting external quote/settlement columns.
- **fix:** Require second-side source digest, same bookmaker, event, period, market, line and capture instant, plus opposite selection/direction. Require exact market/period/selection/line/direction settlement match. Reject every affected selection if one sport/event has contradictory home/away identities. Do not guess identity or fill missing fields.
- **prevention_artifact:** artifacts/wow-engine/tests/test_offline_market_calibration.py. Three new negative tests reproduced defects (35 passing / 3 failing before fix); corrected fixtures and safeguards now give 43/43 local PASS; Python compile and 30-row synthetic CLI descriptive receipt PASS.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/tree/research/v17-offline-market-calibration-20261112
- **independent_evidence:** Hosted CI / SIRT semantic review / production verification not established at local test time. Unattested metrics cannot certify a fitted model or rights to a source. No new source acquisition and no commercial spend.
- **scoreboard:** NFL prop runtime-scoreable lanes unchanged at 4/10; newly qualified 0, production SHA improvement not claimed; research suite 35 to 43 passing (+8), source gap 3,994 mapped missing weekly rows and 48 unmapped player-games unchanged.
- **safety:** custom_gpt_identity=WOW_BETTING_ENGINE; runtime_generation=V17_ACTIVE; terminal_authority=V17_TERMINAL_REDUCER; can_execute=false; DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.

## Reused receipt and non-book quote hardening (2026-11-12)

- **date:** 2026-11-12
- **typed_reason:** REPLAY_PREDICTION_RECEIPT_REUSED; REPLAY_MARKET_QUOTE_INVALID
- **mechanism:** Exact-thesis duplicate detection did not reject the same immutable pregame prediction ID mapped to different official events. This could inflate retrospective sample counts with one prediction replayed as independent evidence. A whitespace-only bookmaker string was also accepted as a same-book claim.
- **detection_point:** Source-identity audit before offline calibration metric collection; these should have been preflighted when the immutable-prediction and bookmaker-key fields were introduced.
- **fix:** Reject every row sharing an immutable prediction ID with another thesis; require trimmed, nonblank bookmaker tokens for both sides; no model fitting or automatic eligibility/promotion.
- **prevention_artifact:** Two additional negative-first tests in tests/test_offline_market_calibration.py. Original 43 passed / 2 RED, patched 45/45 local PASS; synthetic 30-row CLI receipt remains descriptive and can_execute=false. Exact-head CI must be reacquired.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2623
- **production_evidence:** None. Protected merge/release and independent semantic acceptance unproven; model coverage +0, no live changes.

## Independent CodeRabbit numerical and complement challenge (2026-11-12)

- **date:** 2026-11-12
- **typed_reason:** REPLAY_TIME_LEAKAGE_OR_MISMATCH; REPLAY_MARKET_QUOTE_PAIR_MISMATCH (existing registry entries); valid-probability loss-math defect
- **mechanism:** Model probabilities valid within (0,1) were silently clipped to [1e-12,1-1e-12] in reporting, hiding true extreme forecast loss. UTC conversion near year bounds could overflow outside validation and abort the whole audit. Merely distinct quote-side names/directions were insufficient to prove complementary payouts; (HOME,WIN) and (AWAY,LOSE) describe the same outcome.
- **detection_point:** External CodeRabbit review of initial exact head, then five new offline negative regression cases; original evaluator RED in all five. Should have been checked at mathematical scoring and opposite-side schema boundary.
- **fix:** Compute exact log loss for already-valid 0<p<1 without clipping; convert timezone inside checked try/except (ValueError,OverflowError); reduce pilot to explicit HOME-WIN vs AWAY-WIN MONEYLINE/NO_LINE while holding spreads, totals and any unsupported market alias pending their own settlement contracts.
- **prevention_artifact:** tests/test_offline_market_calibration.py, five additional RED→GREEN cases. 50/50 targeted local tests PASS; 30-row synthetic CLI remains descriptive and can_execute=false.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2623
- **production_evidence:** None. Fresh exact-head CI/independent semantic approval still required; 0 fitted model/market qualification changes, 0 production deployment.

## Event-level conflict hidden by a malformed sibling — 2026-11-12
- **date:** 2026-11-12
- **typed_reason:** REPLAY_EVENT_CONTEXT_CONFLICT (existing canonical code)
- **mechanism:** Event context counted only fully valid quote/settlement rows. A bad quote carrying a different primary home/away pair was ignored, so its sibling selection was erroneously included in calibration metrics.
- **detection_point:** CodeRabbit PR #2623 review before independent semantic acceptance. Needed pre-release malformed-peer regression.
- **fix:** Read nonblank primary event/team pairs independently of row quote acceptance. Reject the valid side of a conflicting event; blank identities remain held without fabricating conflict.
- **prevention_artifact:** tests/test_offline_market_calibration.py; two original RED, one control GREEN; updated 53/53 GREEN, Python compile PASS (local).
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2623
- **production_evidence:** None; protected exact-head and independent SIRT acceptance pending. No registered probability, changed threshold, live order or execution; can_execute=false.

## Canonical team-token mismatch caused malformed-row audit crash — 2026-11-12
- **date:** 2026-11-12
- **typed_reason:** REPLAY_EXACT_IDENTITY_MISMATCH (existing canonical fail-closed reason).
- **mechanism:** `_identity` accepted whitespace-padded home/away IDs if all input streams matched, but the event-context collection rejected padded team IDs. The scoring loop then attempted a nonexistent context key and raised KeyError, aborting all other rows.
- **detection_point:** CodeRabbit exact-head review after initial synthetic regression acceptance; the malformed same-ID-in-all-streams counterexample should have been an initial input-contract test.
- **fix:** Reject padded team tokens consistently across primary, quoted and settled home/away fields in `_identity`; preserve per-row HOLD without coercing tokens or changing event-identity joins.
- **prevention_artifact:** `tests/test_offline_market_calibration.py` two new malformed-team cases (initial KeyError RED, then targeted GREEN); 55/55 locally PASS and Python compile PASS. New hosted exact-head CI/independent SIRT needed.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2623
- **production_evidence:** None; no price/probability model, protected merge, runtime deployment, data rights, or terminal execution. `can_execute=false`, `V17_TERMINAL_REDUCER`.
