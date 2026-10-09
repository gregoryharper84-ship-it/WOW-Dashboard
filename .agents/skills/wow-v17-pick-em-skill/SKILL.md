---
name: wow-v17-pick-em-skill
description: Run the complete weekly governed NFL Pick'em workflow from an uploaded pick sheet through durable V17 scoring, tiebreaker qualification, exact reconciliation, and format-preserving green-highlighted form completion.
---

# Pick Em Skill

Status: ACTIVE_ON_MERGE
Canonical contract: `artifacts/wow-engine/v17/skills/WOW_V17_PICK_EM_SKILL.md`
Runtime generation: `V17_ACTIVE`
Terminal authority: `V17_TERMINAL_REDUCER`
`can_execute=false`

## Use when
Use this skill whenever the user asks for weekly NFL Pick'em selections, uploads a Pick'em sheet, asks to model-qualify the sheet, asks to fill/highlight the form, or says `Run Pick Em Skill`.

## Binding workflow
Follow the canonical Pick Em Skill contract in full.

In particular:
- extract every displayed game before scoring;
- derive expected game count from the supplied form;
- reconcile against canonical NFL event identity;
- use only durable `submitWowV17NFLPickemBoard` -> `getWowV17NFLPickemRun`;
- preserve the exact NFL fitted specialist probability and all typed failures;
- require a complete zero-blocker board before declaring the sheet submission-ready;
- source the Monday tiebreaker only from the governed NFL total specialist;
- use the current production-authorized Pick'em strategy only;
- never promote a shadow/experimental pool strategy from this skill;
- when a form image is supplied, edit that exact image, preserve its format, highlight/check the selected teams in green, write `GH` by default, enter the qualified tiebreaker, leave Total Correct blank, and verify the image 1:1 against the terminal card.

## Failure discipline
Never replace an unavailable or failed governed row with odds, public picks, another model, or generic reasoning. Preserve Action transport, scorer, hydration, source, memory/resource, malformed-output, and model-capability failures distinctly.

## Safety
This is a research/decision-support workflow only. It never places, routes, approves, modifies, or cancels a wager or market order. `can_execute=false` always.
