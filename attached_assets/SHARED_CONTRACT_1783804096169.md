# WOW v16 Clean Core — Shared Skill Contract

## Authority
This pack extends WOW v16 Clean Core. It does not replace the master specification or active patches. If a skill conflicts with the master specification, the master specification wins.

## Non-negotiable governance
- Kalshi: `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS`.
- `can_execute` is always `false` for Kalshi outputs.
- No skill may fabricate a live price, orderbook, injury, lineup, weather observation, projection, result, or source timestamp.
- A missing required input produces a hold, watch, scout, reject, or unobtainable result—never an invented value.
- Screenshots and user-entered prices are operator-supplied evidence and cannot masquerade as direct live feeds.
- FINAL CLI output is settlement validation unless fresh live market gates pass.
- During Reliability Freeze, structure and exposure caps remain binding.

## Required evidence fields
Every skill result must include:
`skill_id`, `skill_version`, `run_id`, `as_of`, `event_id`, `market_id`, `inputs_used`, `sources`, `source_timestamps`, `data_quality`, `conflicts`, `assumptions`, `calculations`, `findings`, `blockers`, `label`, `confidence`, `can_execute`.

## Source quality
1. Official league/team/market/weather source.
2. Trusted structured data provider.
3. Reputable reporting/beat source.
4. Aggregator or narrative source.
5. User screenshot/operator supplied.

Lower-quality evidence may supplement but may not override higher-quality current evidence without an explicit conflict record.

## Freshness defaults
- Live price/orderbook: 10 minutes maximum.
- Final lineup/starter lock: 30 minutes maximum unless league context requires tighter.
- Injury/status: same calendar day and rechecked near lock.
- Weather observation: 30 minutes; forecast issued time must be recorded.
- Historical statistics: season/date range and retrieval timestamp required.

## Common labels
`READY`, `WATCH`, `SCOUT`, `HOLD`, `REJECT_BAD_RULES`, `REJECT_DATA_QUALITY`, `DATA_UNOBTAINABLE`.
Domain skills may use stricter WOW/LLP/Kalshi labels, but must map to one common label.

## Fail-closed rule
Any failed identity, slate, settlement, price, freshness, source, or required-field check caps the output before probability or EV can upgrade it.
