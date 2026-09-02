# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-02

Backend/runtime deployment does **not** modify the live Custom GPT editors. Repository skill files are canonical workflow contracts, but the live WOW GPT must also contain the compact skill router in its Instructions field for shorthand commands to persist across chats.

## Current synchronization task

This update changes the WOW instruction layer only. **No Action schema or Render redeploy is required.**

In the live `WOW_BETTING_ENGINE` editor:
1. replace the Instructions field with the complete contents of `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`;
2. keep the existing production V17 Action schema `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml` unchanged;
3. keep API-key/Bearer authentication using the existing production `WOW_ACTION_API_KEY` credential;
4. save the GPT.

Do not paste or expose the credential value, and never install a Supabase service-role key in the editor.

The LLP editor does not require an instruction change solely for this skill-router update. Team/event requests still route from WOW to the existing governed `LLP_TEAM_BETTING_ENGINE` contract.

## Persistent skill router

After synchronization, the live WOW GPT must recognize these commands as controlling shorthand:
- `Run V17 Daily Picks` -> `WOW_V17_DAILY_PICKS`
- `Run V17 Best Props` -> `WOW_V17_BEST_PROPS`
- screenshot/PDF/image/pasted-board review -> `WOW_V17_SCREENSHOT_REVIEW`
- `Run V17 ML Winners` -> `WOW_V17_ML_WINNERS`
- `Build the V17 Core` -> `WOW_V17_PICK_CORE`

Daily, prop, ML and screenshot workflows automatically invoke `WOW_V17_RESEARCH_MARKET_CONTEXT` before final ranking whenever current context is material.

## Research + market context semantics

The research/context stage refreshes, where relevant: schedule/event identity; starters/lineups/rosters; injuries, scratches and team changes; role/workload; recent plus longer-run historical context; opponent/matchup splits; rest/travel; venue/weather; current exact-line prices across credible books; opener/current movement; consensus/no-vig context; and source/as-of provenance.

These rules are controlling:
- research may hydrate a certified fitted input and audit model output, but never substitutes for the fitted model;
- market implied probability, external projections, rankings, records, hit rates and narrative are never governed model probability;
- material evidence must be temporally knowable at the decision timestamp;
- market evidence remains typed `EXACT_LINE`, `ADJACENT_LINE`, or `NO_MARKET`;
- adjacent lines are context only and never exact-line no-vig authority;
- missing market evidence may block economics/value analysis but must not erase completed sporting probability where the backend preserves it;
- no manual double-penalty may be added after the certified model has already consumed the same evidence.

## V17 invariants that must survive editor sync
- `custom_gpt_identity=WOW_BETTING_ENGINE`
- WOW owns player/prop/scalar intelligence; LLP owns team/event winners/favorites/underdogs/upsets
- exactly one controlling specialist per row/event
- Scout/Research are evidence/reconciliation only
- `V17_TERMINAL_REDUCER` is sole global terminal authority
- unsupported fitted routes remain fail closed
- screenshot workflows extract every readable betting row before filtering
- dependency/session/directional/duplicate-thesis exposure affects portfolio qualification, not individual fitted probability
- `can_execute=false`
- no live wager/order execution

## Non-destructive editor verification
After saving, verify in the live WOW GPT:
1. `Governance/health check` reports V17 active, `V17_TERMINAL_REDUCER`, and `can_execute=false`.
2. `Run V17 Daily Picks` is interpreted as the master daily workflow rather than a generic chat response.
3. `Run V17 Best Props` routes to WOW prop discovery and does not collapse to one market family.
4. `Run V17 ML Winners` routes team/event probability through LLP.
5. A screenshot-review request first commits to extracting every visible row before filtering.
6. Research/market context is refreshed before final ranking, but unsupported model routes remain fail closed rather than receiving a market-derived probability.
7. `Build the V17 Core` preserves individual leg probabilities and applies existing portfolio governance.

Do not manufacture a production recommendation merely to test synchronization; governance/host-contract and deliberately unsupported routes are sufficient for safety acceptance.

## Status language
Keep these independent:
- `BACKEND_RUNTIME`: V17 active/live when backend confirms it
- `MODEL_CAPABILITY`: route-specific certified support or governed fail-closed state
- `REPOSITORY_GOVERNANCE`: protected-main/CI governance state
- `LIVE_GPT_EDITOR_SYNC`: this router update is complete only after the live WOW editor saves and verifies the revised instructions
