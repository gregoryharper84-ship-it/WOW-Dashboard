# WOW Native Swarm v1 — zero-credit research workers

**Status:** runnable, local-only research component proposed for WOW V17. It requires no Antigravity account, external LLM or token credits. Production integration and verified persisted handoffs are **not** claimed.

## Goal and boundary

Use WOW's existing official-event snapshot and matchup research to execute five focused specialists **inside WOW's own Python runtime**. A bounded coordinator starts waves of workers, waits for each to finish, structurally validates their event- and source-scoped findings, and returns one research-only receipt. This reuses `v17/sport_scout_registry.py`, `v17/sport_research_brief.py`, `v17/matchup_discovery.py`, and `v17/scout_source_policy.py`; it does **not** introduce a second cloud controller, model, source collector, queue, schema, or terminal decision engine.

The five workers have distinct assignments: source provenance, personnel and availability, opponent matchup and game script, historical form, and exact-line market context. The source worker checks screened provenance across domains. The other four own non-overlapping canonical matchup features. Each wave has an in-process barrier and source/receipt validation before the next wave begins. Default parallelism is **2**; permitted values are 1–3.

### What it actually does

- Starts real `ThreadPoolExecutor` Python workers locally using the candidate's pre-existing data, not prompts or simulated agent messages.
- Uses the **canonical** `discover_matchup` and `_screen_row` routines to reject event mismatch, source provenance and freshness failures, conflicts, missing confirmation, and future/nonpregame observations. It re-screens **every row**, even if another row made the feature pass.
- Enforces explicit `CLEARED_FOR_RESEARCH` on each forwarded fact. `REVIEW_REQUIRED` observations are held, not treated as permissions to copy, train or model-score. Structural validation never certifies source truth or actual rights.
- Sends only screened, rights-marked research observations into its **local receipt handoff**. It does **not** call an existing model route or persist to the actual V17 research handoff on its own. Optional governed integration can consume the receipt in the existing Scout pipeline after normal review.
- Never invents a probability, changes calibration or qualification, initiates a wager or trade, calls a paid LLM, writes Supabase rows, or changes a deployed service.

**Important:** These are deterministic, specialist *research processors*, not five independently reasoning language models. The V17 certified fitted specialists remain the only permitted probability producers. A later optional on-device language model could assist with document interpretation if individually tested and source-authorized; none is bundled or required here.

## Run in the WOW checkout

Create a local JSON file from an existing **verified** pregame WOW event candidate. It must have `sport_key`, `official_event_id`, UTC `commence_time`, `can_execute: false`, and any existing `matchup_metric_evidence` / `research_domain_evidence` / market snapshot evidence. A minimal empty-evidence file is supported but intentionally returns a research HOLD.

```json
{
  "sport_key": "basketball_nba",
  "official_event_id": "REPLACE_WITH_VERIFIED_EVENT_ID",
  "commence_time": "2099-01-01T00:00:00Z",
  "can_execute": false,
  "matchup_metric_evidence": {}
}
```

```bash
python artifacts/wow-engine/tools/wow_specialized_swarm.py \
  --candidate /path/to/existing-verified-event.json \
  --max-parallel 2 \
  --output /tmp/wow-native-swarm-research-receipt.json

python -m pytest -q artifacts/wow-engine/tests/test_wow_specialized_swarm.py
```

The example event is only a contract shape; **it is not a real fixture**. Omit `--output` to print the JSON receipt. Do not feed historical post-start games to the pregame CLI; the V17 incident/postmortem lane handles settled research. Data arrives from the existing WOW Scout candidate; the native Swarm does **not** independently crawl the web.

## Cost model

| Resource | Native Swarm v1 |
|---|---|
| Antigravity Swarm | Not used |
| External LLM tokens / paid providers | **Zero requests in this code path** |
| New persistent SaaS, cloud workers, database or cron | None |
| Python process / CPU time | Uses existing host resources; existing infrastructure may still have normal cost |
| Native fitted probability service calls | Zero |
| Live wagers / trading API calls | Zero |

An existing scheduler can invoke the CLI with its own sanctioned event snapshot without an additional orchestration queue. The runner must not be described as a perpetual background agent unless a real scheduled execution and receipt have been observed.

## Acceptance and release

1. Contract tests: event identity, future UTC time, explicit no-execution, 1–3 worker cap, stable local run ID, five canonical briefs, output invariants.
2. Negative tests: cross-event facts, invalid timestamps, stale/unconfirmed siblings, mixed valid+invalid rows, source rights, hidden probabilities, malformed worker IDs, and arbitrary worker exceptions; include a root-launched subprocess test.
3. Exact-head hosted suite, static/security checks, independent review of the real diff and branch protections remain mandatory.
4. Read-only pilot against one verified future event, using existing licensed/permitted source evidence; report actual source-screened coverage, worker holds and feed gaps.
5. Production adoption is a **separate governed integration** in the existing Scout handoff. No newly qualified picks, production SHA/readback, live receipts or accuracy improvement should be reported from the isolated tool alone.

Preserved controls: `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.