# WOW V17 PrizePicks Board-to-Slips Skill

skill_id=WOW_V17_PRIZEPICKS_BOARD_TO_SLIPS
owner=WOW_BETTING_ENGINE
portfolio_owner=V17_SHARED_CORE
can_execute=false
terminal_authority=V17_TERMINAL_REDUCER
probability_authority=GOVERNED_BACKEND_ONLY

## Trigger
Use for requests such as:
- `Use Full Model on this PrizePicks board`
- `Score the entire attached PrizePicks board`
- `Give me the best PrizePicks props from this board`
- `Evaluate both sides and build a verified pool`
- `Build 2-3 leg slips from the verified pool`
- `Run PrizePicks board to slips`

When a PrizePicks board is attached and the user asks for `best picks`, `Full Model`, `highest hit probability`, or equivalent, prefer this skill unless the user clearly requests a narrower prop workflow.

## Objective
Run PrizePicks in two governed phases:

```text
PHASE 1 — VERIFIED POOL
full readable board -> exact-line canonical scoring -> bidirectional best-side evaluation -> governed ranking -> current-board verification

PHASE 2 — SLIP CONSTRUCTION
verified governed pool -> dependency/exposure review -> strongest 2-3 leg structures -> final refresh
```

Do not mix the two phases. Model selection happens before portfolio/slip construction. Phase 2 cannot rescue, invent, or alter a sporting probability.

## Default mode
`POOL_ONLY` is the default when the user supplies a board and asks for best picks. Do not build slips until the verified pool exists unless the user explicitly requests both phases in the same turn.

Use `BUILD_SLIPS` only when:
- the user explicitly asks to build slips/cards; or
- Phase 1 already produced a verified governed pool and the user asks to continue.

If both phases are requested together, complete Phase 1 first, freeze the verified pool, then run Phase 2 only from that pool.

# PHASE 1 — VERIFIED POOL

## 1. Daily preflight
For `Full Model`, `best picks`, or full-board requests, call the governed V17 Daily snapshot path first as required by the host contract. Daily discovery/preflight does not replace row-level scoring.

## 2. Extract the entire readable board
Before ranking or filtering:
- extract every readable PrizePicks player/scalar row;
- preserve sport, event/opponent when available, player, stat/market, exact line, supplied direction if any, platform, capture timestamp, and source page/screenshot;
- deduplicate only identical market identities;
- do not drop a row because it looks weak, unfamiliar, unsupported, or inconvenient.

Unreadable or ambiguous identity stays typed/held. Never guess a player, stat, line, event, or direction.

## 3. Pregame eligibility
This is a pregame workflow.
- Freeze event identity/start time before scoring.
- If the event has started, preserve `EVENT_ALREADY_STARTED` / exact backend terminal semantics and exclude the row from the verified pregame pool.
- Never create, reconstruct, or backfill a pregame probability after event start or settlement.
- Realized results are never evidence for a retroactive model choice.

## 4. Direction handling — binding
For each exact-line prop:

### Explicit side supplied
If the user/source selected `MORE` or `LESS`, score only that direction unless the user explicitly asks for a comparison.

### No side supplied / best-side request
If the exact line is known but no direction is selected:
1. create a canonical `MORE` candidate;
2. create a canonical `LESS` candidate;
3. do this expansion before `/score-pick-request`;
4. score both through the same certified route/artifact/calibrator;
5. never default to `MORE`;
6. never stop merely because direction was omitted;
7. never use `PROP_DIRECTION_UNRESOLVED` as the terminal blocker for a valid best-side exact-line request.

For MLB 1IP, preserve exact certified-line support. Unsupported lines remain OOD/rejected; do not interpolate to manufacture support. If both directions are expected for best-side comparison, a missing side makes the pair incomplete.

## 5. Canonical Action scoring
Use the canonical batch `/score-pick-request` bridge, with no more than 50 canonical rows per batch.

Every canonical direction row must reach exactly one governed outcome or one exact Action-layer failure.

Required semantics:
- Action not attempted -> `LIVE_GPT_ACTION_INVOCATION_BLOCKED`, `scoring_attempted=false`, backend model capability unknown.
- Action selected/called but transport/auth/schema fails before a backend row receipt -> preserve the exact Action failure such as `PROP_ACTION_TRANSPORT_FAILED:<error_type>`; do not relabel it `MODEL_SCORER_FAILED`.
- Backend row receipt says specialist/scorer was not invoked because acquisition/eligibility terminated first -> preserve that exact typed blocker, e.g. `EVENT_ALREADY_STARTED`; do not call it a scorer failure.
- Controlling scorer actually invoked and then throws/times out/returns no valid completion -> preserve `MODEL_SCORER_FAILED` or the exact typed scorer failure.
- Malformed probability package -> `MODEL_OUTPUT_INVALID`.
- Required owned inputs insufficient -> `MODEL_INPUTS_INSUFFICIENT`.
- Exact controlling fitted capability/artifact absent -> `MODEL_UNAVAILABLE`.

Host Action invocation and backend specialist scoring are separate facts. Never infer `specialist_scoring_attempted=true` merely because an Action call was made.

## 6. Evidence and research
For material candidates, invoke `WOW_V17_RESEARCH_MARKET_CONTEXT_SKILL.md` as needed.
- Research may refresh identity, role/status, starters/lineups, workload, opponent context, venue/weather/rest/travel and current market evidence.
- Only certified fitted inputs may change the numeric sporting model package.
- Evidence-only context never becomes a manual numeric penalty or probability.
- For MLB strikeouts, do not double-penalize opponent/contact context already consumed by certified `opponent_context`.
- Keep `EXACT_LINE`, `ADJACENT_LINE`, and `NO_MARKET` distinct.

## 7. Probability qualification
A row is eligible for the official pool only when the backend returns the route-required governed numeric package and publication/rank gates pass.

Where required, capture:
- model probability;
- calibrated probability;
- calibrated lower bound;
- `probability_publishable`;
- `rank_eligible`;
- controlling specialist;
- exact terminal/model status and blockers.

Never relabel sportsbook implied probability, no-vig probability, projection, recent hit rate, narrative judgment, or generic reasoning as governed model probability.

## 8. Rank the official pool
Rank official supported candidates by governed calibrated lower bound where the route contract requires it, then calibrated probability as a tie-breaker unless a stricter specialist contract controls.

Do not force a quota. Unsupported/held/rejected rows stay in diagnostics, not in the official verified pool.

## 9. Current PrizePicks board refresh — mandatory
Before a candidate enters the verified pool:
- refresh the current PrizePicks board;
- verify the exact player/stat/line still exists;
- verify the selected direction is currently offered;
- verify the event remains pregame;
- preserve the refresh as-of timestamp.

A completed sporting probability may survive a market/offer failure when backend rules allow, but the row must not be presented as a current PrizePicks recommendation unless the exact line + chosen direction are verified current.

## 10. Write before display
For any governed recommendation displayed as current/qualified, preserve the immutable pregame receipt and use `recordWowRecommendations` when required by the host contract. Show the recommendation only when the exact row is display-authorized.

## Phase 1 output
Return two useful sections.

### Verified Governed Pool
For each admitted candidate show:
- rank;
- sport/event;
- player;
- stat/market;
- exact line;
- selected direction;
- model probability;
- calibrated probability;
- calibrated lower bound;
- terminal/model status;
- controlling specialist;
- current-board verification + as-of;
- primary supporting evidence;
- material contradiction/risk;
- source board/page when relevant.

### Full Board Reconciliation
Account for every extracted base row. For undirected rows, show the MORE and LESS terminal/package disposition or exact Action-layer failure, selected side if one qualified, and the final pool status/blocker.

Reconciliation must explain:
```text
base rows extracted
canonical direction rows created
Action rows attempted
backend receipts returned
qualified rows
held/rejected/purged/transport-failed rows
omitted rows = 0
```

Do not build slips in `POOL_ONLY` mode.

# PHASE 2 — SLIP CONSTRUCTION

## Preconditions
Use only the Phase 1 verified governed pool.

A leg must have:
- exact player/stat/line/direction identity;
- current pregame board verification;
- valid governed sporting probability package;
- required rank/publication eligibility;
- immutable pregame receipt when required.

If a leg goes live, changes line, disappears, or loses its selected direction before construction, remove it and rerank. Do not substitute an adjacent line.

## Construction objective
Build the strongest practical 2-3 leg PrizePicks slips/cards from the verified pool while preserving each leg's sporting probability exactly.

Prefer:
1. stronger calibrated lower bounds;
2. stronger calibrated probabilities;
3. structural independence;
4. cleaner evidence/refresh state;
5. lower duplicate-thesis/session exposure.

Never add filler merely to reach a requested leg count or number of slips.

## Joint hit probability discipline
Use a certified joint/card probability model if one exists for the exact structure.

If no certified joint model exists:
- for legs cleared as structurally independent, an independence-product estimate may be shown as `INDEPENDENCE_ASSUMPTION_ESTIMATE`, never as governed model probability;
- `independent_joint_estimate = product(calibrated_probability)`;
- `independent_lower_bound_estimate = product(calibrated_lower_bound)`;
- use the lower-bound estimate as the conservative card-ranking signal when appropriate;
- for unresolved same-event/dependent legs, do not fabricate a correlation coefficient or numeric joint probability.

An independence estimate is portfolio math, not a new sporting model package.

## Portfolio governance
Apply `WOW_V17_PICK_CORE_SKILL.md` and preserve:
- `DEPENDENCY_CORRELATION_STRUCTURE`;
- `SESSION_DIRECTIONAL_DUPLICATE_THESIS_EXPOSURE`;
- same-event dependence;
- repeated player/stat thesis exposure;
- duplicate underlying matchup thesis;
- weakest-leg elimination.

Duplicate thesis is a portfolio risk only. Never lower model probability, calibrated probability, or calibrated lower bound because the same thesis appears on multiple cards.

Replace a marginal common hinge with a superior independent candidate when available. If not, shrink the number of slips or legs.

## Final refresh
Immediately before final publication:
- re-check every leg's event remains pregame;
- verify exact line + direction are still on the current board;
- preserve fresh timestamps;
- require each displayed leg's existing governed receipt/display authorization.

If the refresh changes identity/line/direction, the old score cannot be reused; fresh scoring is required.

## Phase 2 output
### Strongest 2-3 Leg Slips
For each proposed slip show:
- legs with exact line + direction;
- each leg's calibrated probability and calibrated lower bound;
- card joint probability only when certified;
- otherwise labeled independence-assumption joint estimate for independent structures;
- dependency/exposure note;
- weakest leg;
- current-board verification timestamp;
- final card status: `QUALIFIED`, `HOLD`, or `SHRUNK`.

### Exposure Summary
Briefly identify repeated thesis exposure across proposed slips and explain any candidate excluded solely for structure/dependency reasons without downgrading its individual sporting probability.

## Default user-facing shorthand
When the user uploads a PrizePicks board and says `Use Full Model`, interpret the request as:

```text
Use Full Model on the entire attached PrizePicks board. Score every readable exact-line pregame prop through the canonical V17 Action path. For rows where I have not selected MORE or LESS, evaluate both directions and choose the stronger governed side. Rank only eligible results by calibrated lower bound. Show model probability, calibrated probability, calibrated lower bound, exact terminal status, and blocker for every candidate. Then refresh the current board, verify the selected direction and exact line are still offered, and give me the strongest independent pool. Do not build slips yet. Preserve can_execute=false.
```

When the user then says `build slips`, interpret it as:

```text
Using that verified pool, build the strongest 2-3 leg slips by card-level hit potential. Minimize duplicate-thesis and correlated exposure. Shrink the card rather than add marginal legs. Preserve every leg's governed probability unchanged and clearly label any independence-assumption joint estimate as non-governed portfolio math.
```

## Non-negotiable invariants
- Exactly one controlling specialist owns each sporting row.
- No sportsbook implied probability, projection, hit rate, or narrative becomes governed probability.
- No default MORE.
- No direction-omission blocker for valid best-side exact-line requests.
- No retroactive pregame scoring.
- No adjacent-line substitution.
- No transport failure mislabeled as model-scorer failure.
- No portfolio probability haircut applied to individual sporting probabilities.
- No filler legs.
- `can_execute=false` always.
- Never place, approve, route, modify, or cancel a wager/order.
