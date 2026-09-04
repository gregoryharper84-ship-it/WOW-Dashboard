# WOW V17 Postmortems

This directory is the canonical home for V17 incident and model postmortems.

## Purpose

A postmortem records what happened, impact, evidence, root cause, governance implications, and follow-up work. It does not contain the implementation itself unless a small code excerpt is needed as evidence.

## Required separation

- Postmortem = diagnosis and evidence.
- Engineering fix = implementation and verification.
- Every engineering fix that remediates an incident should reference one or more postmortem IDs.
- Every closed postmortem should reference the engineering fix IDs that resolved it.

## ID convention

`PM-YYYY-MM-DD-NNN`

Example: `PM-2026-09-03-001-action-schema-validation.md`

## Suggested categories

- `props/`
- `team-event/`
- `kalshi/`
- `data-source/`
- `infrastructure-actions/`

GitHub creates directories only when they contain files, so category folders should be created when the first postmortem in that category is added.

## Governance requirements

Postmortems must preserve V17 terminal semantics and distinguish runtime health, route/model capability, repository governance, and live GPT editor synchronization. They must never rewrite a scorer/completion failure as `MODEL_UNAVAILABLE` merely to simplify the incident classification.

No postmortem may authorize wager execution. `can_execute=false` remains controlling.

Use `TEMPLATE.md` for all new entries.
