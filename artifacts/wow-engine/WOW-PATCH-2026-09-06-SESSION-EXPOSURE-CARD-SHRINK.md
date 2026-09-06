# WOW-PATCH-2026-09-06-SESSION-EXPOSURE-CARD-SHRINK

## Status

```text
status=ACTIVE_PROJECT_CONTRACT
patch_priority=P0
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Purpose

Prevent card construction from treating repeated or overlapping theses as independent opportunities, and require mandatory card shrink when no strictly stronger independent replacement exists.

This patch governs portfolio/card structure only. It must never alter a row's sporting `model_probability`, `calibrated_probability`, or `calibrated_lower_bound` solely because the thesis appears on multiple cards.

## Binding Workflow Position

```text
valid sporting probability package
→ market/payout objective as applicable
→ dependency/correlation structure
→ session/directional/duplicate-thesis exposure governance
→ weakest-leg replacement/shrink
→ final refresh
→ immutable pregame write where required
→ V17_TERMINAL_REDUCER
```

No card, slip, or multi-card session may bypass the session exposure stage.

## Exact Thesis Identity

Exact session duplicates are keyed by:

```text
event
+ participant
+ market/stat
+ period
+ exact line
+ direction
+ settlement identity
```

The same exact thesis appearing on separate cards is one exposure, not separate diversification.

Regression fixtures include:

```text
Gage Jump — 1IP Pitches MORE 16.5
Gerrit Cole — 1IP Pitches LESS 15.5
```

Repeated copies must be aggregated across the governed session, including previously constructed cards supplied through `prior_session_legs`.

## Exact vs Adjacent Separation

Adjacent thresholds on the same participant/stat/direction are exposure-related but are not the same exact prediction.

Required behavior:

```text
exact duplicate identity != adjacent-line family identity
```

Adjacent-line grouping may inform exposure risk but must not overwrite exact-line identity or settlement semantics.

## Component / Composite Overlap

Known component/composite relationships on the same participant, event, and direction are overlapping exposure.

At minimum:

```text
POINTS + PRA
POINTS + POINTS_REBOUNDS
POINTS + POINTS_ASSISTS
REBOUNDS + PRA
ASSISTS + PRA
```

Example regression:

```text
Caitlin Clark POINTS MORE + Caitlin Clark PRA MORE
=> COMPONENT_COMPOSITE_OVERLAP
=> not independent diversification
```

This patch does not invent arbitrary cross-stat correlations. It recognizes explicit mathematical component overlap.

## Replacement Rule

A repeated/overlapping hinge may be replaced only when the replacement is:

```text
strictly stronger on the governed row-quality basis
independent of retained session exposure
currently valid for the requested slate/board
free of an equal-or-stronger blocker
```

Equal or weaker filler is prohibited.

## Mandatory Shrink Rule

```text
repeated_or_overlapping_hinge
+ no strictly stronger independent replacement
=> remove the hinge
=> shrink the card
```

Requested leg count, payout appearance, Flex/Power shape, or presentation preference may not override shrink.

If the resulting card is below the platform minimum:

```text
portfolio_status=HELD
blocker=INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK
```

Do not backfill with a weaker candidate.

## Same-Event Correlation Gate

For PrizePicks Flex/Power or equivalent multi-leg structures, same-event dependence requires a resolved joint/correlation treatment.

```text
same_event_dependency=true
joint_probability_status not resolved
=> PP_CORRELATION_UNRESOLVED
=> portfolio_qualified=false
```

Independent multiplication is prohibited when dependence is unresolved.

## Probability Immutability

Portfolio/exposure penalties are structural only.

Forbidden:

```text
reduce model_probability because a thesis is duplicated
reduce calibrated_probability because a thesis is duplicated
reduce calibrated_lower_bound because a thesis is duplicated
```

Allowed:

```text
portfolio blocker
critical-leg structural score
replacement/removal
card shrink
session exposure aggregation
```

Required invariant:

```text
probability_fields_mutated=false
```

## Session Carry-Forward

The governed optimizer must accept already-used session exposure:

```text
prior_session_legs
```

Separate card-construction calls must not reset exposure state. The caller/session layer is responsible for supplying prior session legs; the optimizer remains deterministic and stateless.

## Terminal Governance

`V17_TERMINAL_REDUCER` remains sole global terminal authority.

This patch may lower a card/portfolio ceiling but may not upgrade any upstream row, override settlement/market/calibration blockers, or authorize execution.

```text
can_execute=false
```

## Acceptance Tests

1. Repeated Gage Jump MORE 16.5 1IP across cards collapses to one exact session thesis.
2. Repeated Gerrit Cole LESS 15.5 1IP across cards collapses to one exact session thesis.
3. Caitlin Clark POINTS MORE + PRA MORE triggers component/composite overlap.
4. Exact and adjacent thresholds remain distinct identities.
5. A repeated hinge is replaced only by a strictly stronger independent candidate.
6. No qualifying replacement causes mandatory shrink.
7. Shrinking below minimum card size yields `INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK` and HOLD.
8. Same-event Flex/Power without resolved joint treatment yields `PP_CORRELATION_UNRESOLVED`.
9. `prior_session_legs` carries exposure across sequential card builds.
10. Sporting probability fields are unchanged by portfolio penalties.
11. `can_execute=false` remains invariant.

## Definition of Done

Repository implementation is code-complete only when:

```text
optimizer implementation present
host binding present
P0 regression suite passing
full required CI passing
PR merged to main
Render deploy of merged main is live
production version/commit confirmed
```

`FIXED_VERIFIED` additionally requires production verification and replay of the governed scenario. Repository merge or Render deploy alone does not prove `LIVE_GPT_EDITOR_SYNC`.

## One-Line Definition

**WOW-PATCH-2026-09-06-SESSION-EXPOSURE-CARD-SHRINK makes session-level duplicate and component/composite exposure binding, replaces repeated hinges only with strictly stronger independent candidates, and mandates card shrink/hold rather than filler while preserving sporting probability fields.**
