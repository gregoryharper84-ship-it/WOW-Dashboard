# WOW V17 Pick Em Skill

skill_id=WOW_V17_PICK_EM
display_name=Pick Em Skill
owner=WOW_BETTING_ENGINE
sporting_probability_owner=wow.nfl-game-win-probability-expert
tiebreaker_owner=wow.nfl-total-points-tiebreaker-specialist
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
probability_authority=GOVERNED_BACKEND_ONLY
can_execute=false

## Trigger
Use this skill for requests such as:
- `Run Pick Em Skill`
- `fill out my Pick'em sheet`
- `give me my weekly Pick'em picks`
- `model qualify this NFL pick sheet`
- `highlight my picks on this form`
- `I need to submit my picks today`
- `use WOW to complete this Pick'em form`

When the user uploads an NFL straight-up Pick'em sheet and asks for picks, model qualification, a completed form, or a submission-ready card, prefer this skill over generic ML-winner or screenshot-review workflows.

## Objective
Produce the most complete governed NFL straight-up Pick'em card available for the exact supplied weekly sheet, then optionally annotate the uploaded form without changing its original layout.

The sporting objective is the current production-authorized Pick'em objective. Until a different strategy has completed governed promotion, production remains `MAX_EXPECTED_CORRECT`: choose the side with the higher valid governed NFL win probability for each exact event.

Research-only or shadow pool strategy, opponent popularity, sportsbook odds, public picks, narrative judgment, recent results, external projections, and generic LLM reasoning may never replace or numerically alter the controlling fitted-model probability.

## Input contract
Treat the user's supplied sheet as the canonical submission-layout inventory.

Before scoring:
1. Extract every readable game row in displayed order.
2. Preserve displayed dates, matchup sides, times, special-site labels such as `vs`, bye notes, and the Monday-night tiebreaker row.
3. Derive `expected_game_count` from the form. Never hard-code 16; bye weeks can have fewer games.
4. Derive the required slate dates from the form.
5. Use the user's known timezone when available for the runtime request; otherwise use the sheet timezone when explicit or ask only if the date boundary is genuinely ambiguous.
6. Reconcile every extracted row against the canonical NFL schedule before accepting the board.

Never silently correct, rename, add, remove, reorder, or invent a matchup. If a sheet row cannot reconcile to one exact canonical event, preserve `PICKEM_SHEET_SCHEDULE_MISMATCH` for that row and do not manufacture a pick.

## Governed scoring path
Use the durable Action contract only:

```text
submitWowV17NFLPickemBoard
-> poll getWowV17NFLPickemRun
-> terminal persisted Pick'em receipt
```

The live Custom GPT path is:
- `POST /v17/nfl-pickem-submit` -> `submitWowV17NFLPickemBoard`
- `GET /v17/nfl-pickem-run/{run_id}` -> `getWowV17NFLPickemRun`

Do not use or recreate the obsolete synchronous full-board Action `runWowV17NFLPickemBoard`.

The submit request must bind to:
- the exact extracted slate dates;
- the exact extracted game count;
- the resolved timezone;
- the current production-authorized strategy mode.

Each sporting row must be owned by exactly one fitted specialist. For NFL straight-up win probability, the controlling owner is `wow.nfl-game-win-probability-expert`.

## Full-sheet acceptance gate
A form is submission-ready only when the terminal receipt proves all of the following:

```text
status=PICKEM_BOARD_READY
submission_ready=true
full_sheet_submission_ready=true
discovered_event_count == expected_game_count
ready_pick_count == expected_game_count
blocked_event_count == 0
exactly one Pick'em selection per supplied game
tiebreaker.status=TIEBREAKER_MODEL_QUALIFIED
tiebreaker.suggested_integer_tiebreaker is present
global terminal authority = V17_TERMINAL_REDUCER
can_execute=false
```

Also verify:
- every selected row is `PICKEM_READY`;
- each row's controlling specialist is the NFL fitted game-win specialist;
- every row preserves a valid governed probability package and source identity;
- source terminals are not silently upgraded;
- market probability, sportsbook price, and pool popularity were not used as sporting probability;
- every form row reconciles exactly once and omitted rows = 0.

If any game is blocked, the sheet is not complete. Never invent the missing side merely because a pool requires one pick per game.

## Selection semantics
For `MAX_EXPECTED_CORRECT`, choose the higher valid governed calibrated win probability for each event.

A low lower bound does not authorize a manual flip. It is a risk signal only.

Preserve the runtime review classes:
- `HIGH_CONFIDENCE_HOLD`
- `STANDARD_HOLD` when present
- `MODEL_SIDE_FRAGILITY_REVIEW`
- `TOSS_UP_REVIEW`

Review overlays may annotate risk but cannot change the production pick or probability.

If a future pool-win-equity strategy is separately certified and production-authorized, this skill may use it only after the canonical runtime/manifest identifies it as the active production strategy. Shadow or experiment status is never sufficient.

## Tiebreaker
The weekly total-points tiebreaker must come only from `wow.nfl-total-points-tiebreaker-specialist`.

Require:
- exact Monday-night event identity from the sheet;
- `TIEBREAKER_MODEL_QUALIFIED`;
- `tiebreaker_publishable=true`;
- an integer submission value;
- `can_execute=false`.

Never substitute a sportsbook total, consensus total, generic LLM estimate, or the moneyline model.

If the qualified tiebreaker event does not match the form's Monday-night game, stop with `PICKEM_TIEBREAKER_EVENT_MISMATCH`.

## Reliability and bounded recovery
The durable run is the primary user path.

Preserve typed failures exactly. Examples include:
- `ACTION_TRANSPORT_FAILURE`
- `PICKEM_TEAM_EVENT_TRANSPORT_FAILURE`
- `MODEL_SCORER_FAILED`
- `NFL_FEATURE_ASSEMBLY_FAILED`
- `MODEL_INPUTS_INSUFFICIENT`
- `MODEL_OUTPUT_INVALID`
- `MODEL_UNAVAILABLE`
- schedule/reconciliation failures
- memory/resource deferrals.

Do not collapse transport, repository, source, memory, malformed-output, or hydration failures into `MODEL_UNAVAILABLE`.

For retryable transport/scorer/runtime failures, one bounded fresh retry of the exact same sheet is allowed automatically. Do not blindly loop on deterministic missing-input/model-unavailable/malformed-output failures. If the bounded retry remains incomplete, return the exact blocker rather than synthetic picks.

A later incomplete verification attempt does not erase a previously complete governed board when:
- both runs bind to the same canonical sheet/event inventory;
- no newer material source snapshot supersedes the complete board;
- the later failure is a typed transport/scorer/runtime failure rather than new sporting evidence.

The newest complete reconciled run on the newest material source snapshot is authoritative.

When the user has an imminent submission deadline, prioritize obtaining one complete fresh governed run over experiments, refactors, or shadow-strategy research.

## Deadline mode
If the user says `submit by noon`, `deadline today`, `lock this now`, or equivalent:
1. extract/reconcile the supplied form immediately;
2. run the durable governed board immediately;
3. apply bounded retry only when needed;
4. freeze the newest complete reconciled board;
5. fill the form only after the full-sheet acceptance gate passes;
6. report any remaining blocker immediately if completion is impossible.

Do not promise background work. The user must receive either the complete card/form or the exact current blocker in the same active workflow.

## Form annotation contract
When a Pick'em form image is supplied and the final governed board is complete, edit that exact uploaded image rather than recreating a new layout from memory.

The only permitted visual changes are:
- highlight the selected team name with a realistic semi-transparent green highlighter;
- clearly mark the selected side's existing checkbox;
- write `GH` on the Name line by default unless the user specifies another name/initials;
- write the governed integer tiebreaker on the existing tiebreaker line.

Preserve unchanged:
- page dimensions and aspect ratio;
- title/year;
- day/date headings;
- all team names;
- `at` / `vs` labels;
- times and timezone heading;
- all unselected checkboxes;
- bye-team text;
- footer/logo;
- line spacing, typography, row order, and white background;
- `Total Correct` must remain blank before games are played.

Do not add probability text, confidence labels, legends, tables, watermarks, commentary, extra logos, or redesign elements to the form.

Use image editing on the actual supplied form target. Never annotate an invented or stale image target.

## Image reconciliation gate
Before returning the completed form, verify:
1. number of highlighted selections == `expected_game_count`;
2. exactly one side is marked on each matchup;
3. every highlighted team matches the terminal Pick'em card;
4. no non-selected team is highlighted;
5. the tiebreaker equals the governed integer receipt;
6. the Name field equals the requested initials/name, default `GH`;
7. `Total Correct` remains blank;
8. no source text, time, date, team, or row order changed.

If the edited image does not reconcile 1:1 with the terminal card, regenerate/fix it before returning it. Never call a visually mismatched form complete.

## User-facing output
### If no form image is supplied
Return:
- exact run id;
- full-sheet state;
- one row per game in sheet order with matchup, selected team, calibrated probability, lower bound, confidence/review class;
- tiebreaker;
- any blockers;
- `can_execute=false`.

### If a form image is supplied
Once the image reconciliation gate passes, return the completed annotated form. A short card/receipt may be provided before the image when useful, but the final image itself must be clean and contain only the allowed form annotations.

## Default weekly shorthand
When the user uploads a Pick'em sheet and says `Run Pick Em Skill`, interpret it as:

```text
Extract every game from this exact NFL Pick'em sheet and preserve its displayed order. Reconcile the sheet to the canonical NFL schedule. Run the current production-authorized WOW V17 NFL Pick'em strategy through durable submit/poll transport, preserving exactly one fitted specialist per sporting row and every typed failure. Require a complete submission-ready board with zero blocked games. Use the governed NFL total-points tiebreaker specialist for the Monday tiebreaker. Then edit this exact form without changing its format: green-highlight and check only my selected teams, write GH on Name unless I say otherwise, enter the governed integer tiebreaker, leave Total Correct blank, and verify the finished image 1:1 against the terminal model card. can_execute=false.
```

## Non-negotiable invariants
- Exactly one fitted specialist owns every sporting probability row.
- Scout/Research may gather evidence but cannot produce or replace sporting probability.
- No market, public-pick, pool-popularity, recent-result, projection, or narrative substitution.
- No manual pick flip.
- No synthetic probability.
- No invented matchup.
- No omitted row.
- No synchronous full-board Action path.
- No form annotation before a complete board exists unless the user explicitly requests a partial diagnostic form.
- No image-format redesign.
- No wager/order execution.
- `V17_TERMINAL_REDUCER` remains terminal authority.
- `can_execute=false` always.
