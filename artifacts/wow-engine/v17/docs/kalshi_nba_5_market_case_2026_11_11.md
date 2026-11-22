# WOW V17 — November 11, 2026 Kalshi NBA five-market case study

**Evidence class:** user-supplied closed-position screenshot, *not* independently verified official Kalshi settlement or a frozen pregame model prediction.

**Scope:** Additive retrospective/QA research only. No changes to existing live Kalshi recovery, production odds, fitted team/event models, NFL Pick'em, pricing, execution, or approvals.

## Exact screenshot observations

- Four YES moneylines: Boston over Toronto, Lakers over Bucks, Orlando over Utah, Spurs over Bulls.
- One NO conditional spread: **NO on Memphis winning by more than 7.5 points** versus Denver. This is not a prediction that Memphis must lose: the NO condition wins when Memphis loses, or wins by at most seven (for an integer final scoring margin).
- Reported closed-position results: 5/5 succeeded.
- Cost $99.96; total payout $519.48; net profit $419.52.
- Payout multiple 5.196879×; profit return 419.69%; price-implied all-leg break-even 19.24%.

The break-even figure is **cost ÷ total payout**, not observed frequency, calibrated true probability, a no-vig estimate or predictive edge. A five-market combination does not receive a mechanically assigned 5× multiplier because it has five legs. An executable combo price, the exact five resolved contracts and any fees are needed to reconstruct the actual fill.

## Legitimate learning

1. Audit exact YES/NO proposition wording and spread threshold, independently of team win/loss predictions.
2. Compare moneyline to line-dependent selections: an outright win may occur without a spread cover.
3. Analyze *all* frozen prospective candidate tickets, including losers and excluded/void cases, not only this winning survivorship-selected example.
4. Evaluate the joint dependency-aware event distribution against actual contemporaneous executable prices and fees, with calibrated lower bounds and immutable pregame receipts. Multiplying guessed independent leg probabilities or copying Kalshi implied prices into "Full Model" is not evidence of skill.
5. Track separate metrics: leg prediction quality/calibration, combo pricing accuracy/EV, settlement/fee accounting, and realized P&L variance.

## Missing evidence (fail closed)

Screenshot lacks the exact Kalshi contract tickers/rules, full fill/fee breakdown, independently verified official exchange settlements, immutable pregame model receipts, entry-time orderbook, per-leg quote/probability series, correlation data, and an untouched forward sample. The screenshot is evidence of a **reported win**, not proof that the model selected it, that it had favorable expected value, or that similar five-leg combinations are safe.

The optional score-condition function in `v17/kalshi_combo_win_case_study.py` is diagnostic only. Exchange-specific overtime/push/void/postponement/settlement rules must override any informal interpretation.

## Recovery/governance preserved

The existing `artifacts/flask-scoring-api/gate_engine/combo_gate.py` hard-rejects 4+ markets under Reliability Freeze; `artifacts/flask-scoring-api/kalshi_engine/portfolio_governor.py` rejects multileg structures in Recovery Mode. **This research case does not lift either gate**. `can_execute=false`, dry-run only, `V17_TERMINAL_REDUCER` remains sole terminal authority, no orders, no automatic wagers, no automatic Pick'em entries. No calibration, champion, joint probability, or marketing label changes.

## Test command

```bash
PYTHONPATH=artifacts/wow-engine python -m pytest -q artifacts/wow-engine/tests/test_v17_kalshi_combo_win_case_study.py
```

**Release acceptance:** exact-head regression and reviewer provenance, independent QA and Release Authority approval, governed promotion only after prospective evidence warrants it. This case-study change alone does not authorize production adoption.
