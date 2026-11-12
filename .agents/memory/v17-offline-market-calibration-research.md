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
