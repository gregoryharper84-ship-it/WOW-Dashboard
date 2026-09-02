# WOW V17 production status

Updated: 2026-09-02

This file is the current-status pointer for the governed WOW V17 backend. Historical review packets and earlier proposal/candidate documents remain archival and must not override this file when they describe an older lifecycle state.

## Runtime state

- Runtime: **V17 ACTIVE**
- Production service: `wow-governed-probability-engine`
- Production branch: `main`
- Current production deployment SHA at this update: `589921b0def9f9010e83d766234bb2d5a7139c2d`
- Global terminal authority: `V17_TERMINAL_REDUCER`
- `WOW_CAN_EXECUTE=false`
- `WOW_DRY_RUN_ONLY=true`
- No wager/order execution path is authorized.

V17 activation is additive and governed. It does not convert unsupported sport/stat routes into model-supported routes. Exact fitted-model capability, evidence, calibration, market, portfolio, refresh, and terminal gates remain authoritative.

## Architecture authority

- WOW_BETTING_ENGINE owns player/prop/scalar intelligence.
- LLP_TEAM_BETTING_ENGINE owns team/event winner/favorite/underdog/upset intelligence.
- Scout -> Research is an evidence barrier, not a final decision publisher.
- Exactly one controlling specialist owns each market/event scoring decision.
- Shared core owns calibration, market economics, portfolio/exposure, final refresh, immutable prediction/write reconciliation, and terminal reduction.
- `V17_TERMINAL_REDUCER` is the sole global terminal authority.
- `MODEL_UNAVAILABLE` remains reserved for true controlling-model capability/completion failures; missing downstream market evidence must not erase a completed sporting probability where the governed contract allows the sporting lane to survive.

## 2026-09-02 postmortem package

The September 1 Manaea postmortem is integrated as four targeted changes without adding another model layer:

1. **Pitcher-K suppression (#116/#119).** Opponent strikeout-rate evidence can now move the MLB pitcher-K fitted distribution through the artifact's already-reviewed opponent factor. `STRIKEOUT_RATE_SUPPRESSION` is numerically load-bearing; `OPPONENT_CONTACT_EXTENSION` is explanatory unless/until a fitted coefficient is certified. The same `opponent_k_factor()` function is used for the point estimate and calibration bootstrap, preventing bound drift or double application.
2. **Exact/adjacent market evidence (#117).** Adjacent sportsbook lines may inform distribution context but cannot satisfy exact-line no-vig authority for a different board threshold.
3. **Duplicate-thesis portfolio control (#118).** Reusing the same underlying thesis across cards changes structural/portfolio risk only; it does not rewrite model probability, calibrated probability, or calibrated lower bound. Marginal common hinges are replaced when a superior independent candidate exists; otherwise the card may shrink rather than add filler.
4. **Live opponent-context acquisition (#125).** The certified MLB pitcher-strikeout auto-hydration path now attempts official MLB lineup/handedness split acquisition and populates `opponent_context` when sufficiently supported. If the optional opponent evidence is unavailable, the route remains neutral rather than inventing a rate or creating a false `MODEL_UNAVAILABLE` result.

The runtime Supabase evidence table includes nullable `opponent_context jsonb`, and the governed evidence read RPC returns that field when present.

## MLB 1IP state

The certified MLB 1IP empirical specialist remains governed independently of the pitcher-K postmortem changes. Its final-refresh scheduler remains enabled, `probability_publishable=false` for that governed path, and `can_execute=false`. The previously completed authenticated smoke proved supported exact-line scoring and deterministic exact-support rejection. Natural observation of a genuine provisional row surviving through official-lineup final refresh remains observational evidence, not a V17 activation blocker.

## Release acceptance

For every production release, require all of the following:

- PR-based code change and required CI.
- `wow-engine-verify` green on the final PR head.
- `wow-verify` green on the final PR head.
- Merge to `main`.
- Manual Render deploy because production auto-deploy is disabled.
- Render deploy reports the exact merged SHA and reaches `live`.
- Runtime startup reports `WOW_V17_RUNTIME status=ACTIVE`, `global_terminal_authority=V17_TERMINAL_REDUCER`, and `can_execute=false`.
- Database migrations required by the release are applied and verified before callers are expected to depend on the new schema.

## Remaining external certification item

Repository issue #88 remains the machine-level GitHub governance blocker until `main` branch protection/ruleset enforcement is verified. Required end state: PR-before-merge, required WOW CI checks, blocked force-pushes, blocked branch deletion, and no ordinary bypass that defeats protection.

This is a repository-governance blocker only. It is not a model/runtime capability failure and must not be reported as `MODEL_UNAVAILABLE`.

## Custom GPT editor synchronization

Backend deployment does not edit the live Custom GPT editors. The canonical editor-sync instructions are in `V17_CUSTOM_GPT_EDITOR_SYNC.md`. Until those editor actions are completed externally, classify that product-configuration item as `LIVE_EDITOR_SYNC_EXTERNAL`; do not misstate it as a backend deployment failure.
