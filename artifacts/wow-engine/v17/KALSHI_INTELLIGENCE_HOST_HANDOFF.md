# WOW V17 — Kalshi Slip Intelligence, shared host knowledge packet

**Use with BOTH existing Custom GPTs:** `WOW_BETTING_ENGINE` and `LLP_TEAM_BETTING_ENGINE`. This is a Knowledge addendum, not a replacement for either host's canonical instructions. Attach only after the deployed backend and canonical Action schemas are verified.

## What the shared exchange is

A common authenticated append-only research ledger for identifying fatigue, rest/travel, availability, game-script, leg-correlation, market/quote and postmortem diagnostic observations. Each record is `UNVERIFIED_RESEARCH`. It is **not** a probability, a direct model feature, a recommendation, an approved slip, an order, or an automatic model update.

## Before scoring

- Identify the canonical sport, official event ID, event start, market and settlement contract using the controlling specialist.
- When the exchange Action is installed and authenticated, use `readWowV17KalshiSlipResearch` (WOW) or `readLlpV17KalshiSlipResearch` (LLP) with the canonical event ID. Consider both source host labels equally; they are unverified because they share a bearer credential.
- Independently check any useful research observations against primary, point-in-time sources before admitting context to a sport-specific specialist's legitimate input path. Do not use unverified findings to derive a probability, confidence or model certification.
- If the route is not installed, disabled, times out or returns 503, continue the existing governed model workflow with `INTELLIGENCE_EXCHANGE_UNAVAILABLE`; do not invent cross-engine evidence or override another upstream blocker.

## After identifying a research fact

- Use the matching host's publish operation to append an observed, source-referenced categorical finding. No credentials, Kalshi account data, personal order IDs, balances, stakes, payouts, bookmaker odds, model probabilities, free-form private data or invented observations.
- Supply precise `as_of_utc`, `source_observed_at_utc` and `event_start_utc` for event-scoped pregame observations. `source_observed_at_utc <= as_of_utc < event_start_utc`; timestamps must not be future-dated beyond server tolerance.
- A successful append returns a research receipt only. Source-host identity remains a claim; do not treat the successful write as proof of an independently authenticated engine.

## Division of labor

- LLP owns team/event winners, favorites, underdogs, upset scenario analysis and controlling team-level explanations.
- WOW owns player/prop/scalar specialist explanations and player-role, minutes/usage, injury and correlation research.
- Both can contribute schedule/rest/travel and joint-thesis hypotheses, but never replace the other's controlling specialist, publish a probability or change `V17_TERMINAL_REDUCER` output.
- Research on multi-leg Kalshi combos remains blocked from live approval/execution in recovery mode. For two-leg research compare conservative joint probability with fee-inclusive frozen RFQ break-even only if both the model's exact frozen receipts and full chronology exist. Never multiply correlated marginal probabilities by default.

## Postmortem learning

Record official results only after their settlement is verified. Distinguish selection failure, game-script surprise, market-price error and unknown causes. Compare forecast calibration, Brier/log loss and net economic return only using timestamp-correct pre-event receipts and settled outcomes. Use walk-forward holdouts and independent challenger validation before any deployment or specialist promotion.

## Live acceptance

One fresh-chat read and one fresh-chat write from each engine using the canonical Action schema. Confirm distinct user-facing operation names, expected shared research visibility and complete `can_execute=false` propagation. Record current deployed SHA, editor schema hash and authenticated receipt IDs. Absence of these receipts means **LIVE_GPT_EDITOR_SYNC=PENDING**, not completed.
