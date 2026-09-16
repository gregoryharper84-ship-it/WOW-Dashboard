# WOW V17 production status

Updated: 2026-09-02 runtime baseline; editor-sync reconciliation: 2026-09-16

This file is the current-status pointer for the governed WOW V17 system. Historical review packets and earlier proposal/candidate documents remain archival and must not override this file when they describe an older lifecycle state.

## Current state

- Runtime: **V17 ACTIVE / VERIFIED_ACTIVE**
- Production service: `wow-governed-probability-engine`
- Production branch: `main`
- Current deployed runtime SHA: `f75bbc8622feff19fd5172f6491e4c0d60d07bbf`
- Current repository `main` SHA at this update: `2721f19d23e83a370f8561b6fe5e29364f9ea0db`
- Global terminal authority: `V17_TERMINAL_REDUCER`
- `WOW_CAN_EXECUTE=false`
- `WOW_DRY_RUN_ONLY=true`
- No wager/order execution path is authorized.

Repository and runtime SHAs may differ when later merges are instruction/documentation-only and do not require a Render redeploy. Do not conflate repository generation state with deployed runtime code identity.

V17 activation is additive and governed. It does not convert unsupported sport/stat routes into model-supported routes. Exact fitted-model capability, evidence, calibration, market, portfolio, refresh, and terminal gates remain authoritative.

## Architecture authority

- WOW_BETTING_ENGINE owns player/prop/scalar intelligence.
- LLP_TEAM_BETTING_ENGINE owns team/event winner/favorite/underdog/upset intelligence.
- Scout -> Research is an evidence barrier, not a final decision publisher.
- Exactly one controlling specialist owns each market/event scoring decision.
- Shared core owns calibration, market economics, portfolio/exposure, final refresh, immutable prediction/write reconciliation, and terminal reduction.
- `V17_TERMINAL_REDUCER` is the sole global terminal authority.
- `MODEL_UNAVAILABLE` remains reserved for true controlling-model capability absence on the exact route; selected-model scorer/completion failures retain their typed failure classes. Missing downstream market evidence must not erase a completed sporting probability where the governed contract allows the sporting lane to survive.

## Daily-picks instruction layer

The canonical V17 skill layer is active in the repository. Live Custom GPT distribution state is tracked separately under **Custom GPT editor synchronization** below and must not be inferred from repository state alone.

Supported shorthand includes:
- `Run V17 Daily Picks`
- `Run V17 Best Props`
- screenshot/PDF/image/pasted-board review through `WOW_V17_SCREENSHOT_REVIEW`
- `Run V17 ML Winners`
- `Build the V17 Core`

`WOW_V17_RESEARCH_MARKET_CONTEXT` automatically refreshes material schedule, roster/lineup/status, historical, matchup, rest/travel, venue/weather, market-price/movement, and temporal-provenance context before final ranking when applicable. It has no probability or terminal authority and may never replace a certified fitted model.

## 2026-09-02 postmortem package

The September 1 Manaea postmortem is integrated without adding another model layer:

1. **Pitcher-K suppression (#116/#119).** Opponent strikeout-rate evidence can move the MLB pitcher-K fitted distribution through the artifact's reviewed opponent factor. `STRIKEOUT_RATE_SUPPRESSION` is numerically load-bearing; `OPPONENT_CONTACT_EXTENSION` is explanatory unless/until a fitted coefficient is certified. The same `opponent_k_factor()` function is used for point estimate and calibration bootstrap.
2. **Exact/adjacent market evidence (#117).** Adjacent sportsbook lines may inform context but cannot satisfy exact-line no-vig authority for a different board threshold.
3. **Portfolio exposure (#118/#135).** Duplicate thesis, same-event dependency, and session/directional exposure affect downstream portfolio qualification only; they do not rewrite model probability, calibrated probability, or calibrated lower bound. Unquantifiable dependence is record + hold, never fabricated joint probability.
4. **Live opponent-context acquisition (#125).** The certified MLB pitcher-strikeout auto-hydration path attempts official lineup/handedness split acquisition and populates `opponent_context` when sufficiently supported. If optional opponent evidence is unavailable, the route remains neutral rather than inventing a rate or creating a false model-unavailable result.

The runtime Supabase evidence table includes nullable `opponent_context jsonb`, and the governed evidence read RPC returns that field when present.

## MLB 1IP state

The certified MLB 1IP empirical specialist remains governed independently of the pitcher-K postmortem changes. Its final-refresh scheduler remains enabled, `probability_publishable=false` for that governed path, and `can_execute=false`. The authenticated smoke proved supported exact-line scoring and deterministic exact-support rejection. Natural observation of a genuine provisional row surviving through official-lineup final refresh remains observational evidence, not a V17 activation blocker.

## Release acceptance

For every production code release, require all of the following:

- PR-based code change and required CI.
- `WOW governed probability backend` green on the final PR head.
- `WOW required-three regression` green on the final PR head.
- `WOW additional required regression` green on the final PR head.
- Merge to protected `main`.
- Manual Render deploy when runtime code changes because production auto-deploy is disabled.
- Render deploy reports the exact merged runtime SHA and reaches `live`.
- Runtime startup reports `WOW_V17_RUNTIME status=ACTIVE`, `global_terminal_authority=V17_TERMINAL_REDUCER`, and `can_execute=false`.
- Database migrations required by the release are applied and verified before callers depend on the new schema.

Instruction/documentation-only releases do not require a Render redeploy when the deployed runtime and Action schema are intentionally unchanged.

## Repository governance

**VERIFIED.** Issue #88 is closed. `main` is machine-protected with PR-before-merge, the three required WOW checks, up-to-date branch enforcement, admin bypass disabled, approvals not required for the solo-maintainer workflow, force pushes disabled, and deletion disabled.

Repository-governance state remains separate from model/runtime capability and must never be reported as `MODEL_UNAVAILABLE`.

## Custom GPT editor synchronization

**LIVE_EDITOR_SYNC_PENDING.** The repository source contract is ready, but the current repository does not contain evidence that the production `WOW_BETTING_ENGINE` editor has been saved and reloaded with the current canonical instructions and canonical V17 Action schema. `V17_CUSTOM_GPT_EDITOR_SYNC.md` is authoritative for this layer.

Required live-editor sources and acceptance boundary:

- Instructions: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`
- Action schema: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`
- Action origin: `https://wow-governed-probability-engine.onrender.com`
- Required prop-batch operation: `scoreWowV17PickRequest` on `POST /score-pick-request`
- Authentication: Bearer/API key using `WOW_ACTION_API_KEY` only; never expose credential material in repository artifacts or acceptance logs.
- After save/publish and reload, a still-pregame supported prop row must actually invoke `scoreWowV17PickRequest` and return a canonical row-level Action receipt or the exact typed Action-attempt failure. `scoring_attempted=true` is valid only after an Action call was attempted.

Until that save + reload + canary is evidenced, report `LIVE_GPT_EDITOR_SYNC=PENDING`. Do not collapse this state into `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, or any sporting-model status. Backend runtime, route/model capability, repository governance, and live editor synchronization remain independent.
