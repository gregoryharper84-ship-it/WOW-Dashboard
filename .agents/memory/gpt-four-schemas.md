---
name: Custom GPT four-schema architecture
description: The WOW Custom GPT requires four separate OpenAPI action schemas; each covers a distinct surface.
---

# Custom GPT Four-Schema Architecture

## The four schemas (all in artifacts/flask-scoring-api/)

| File | Title | Key operationIds | Auth |
|---|---|---|---|
| `gpt-action-schema.yaml` | WOW Scoring API | scoreProp, scorePick, getRequestLog, getStats, getLeaderboard, runDailyScan, getScanSummary, getScanResults | X-API-Key |
| `gpt-action-schema.json` | WOW Scoring API (slim) | scorePick only (/gpt-score) | X-API-Key |
| `gpt-action-schema-kalshi.yaml` | WOW Kalshi Read-Only Proxy | kalshiHealth, kalshiMarkets, kalshiOrderbook, kalshiWeatherHighs, kalshiEvaluateWeather | X-API-Key |
| `gpt-action-schema-gate-engine.yaml` | WOW Gate Engine v2 | **getWowEngineHealth**, **getWowGovernanceStatus**, **runWowGateEngineV2** | public for health/gov; X-API-Key for run |

## Why this matters
When the user deleted their Custom GPT schemas, only three files existed in the repo. The gate-engine schema (`gpt-action-schema-gate-engine.yaml`) did NOT exist in the repo — it was recreated from the live app.py routes. Without it, ChatGPT cannot call `getWowEngineHealth`, `getWowGovernanceStatus`, or `runWowGateEngineV2` and reports `ClientResponseError` for all three (operation not found, not a network error).

## Mandatory call sequence (gate-engine schema)
1. `getWowEngineHealth` — public, no key, confirms worker alive
2. `getWowGovernanceStatus` — public, no key, returns governance_hash
3. `runWowGateEngineV2` — X-API-Key required, pass governance_hash as expected_governance_hash

## Authentication note
When a schema is deleted and restored in a Custom GPT, the API key is NOT stored in the schema file. The user must re-enter `SCORING_API_KEY` as `X-API-Key` in each action's authentication settings after restoring.
