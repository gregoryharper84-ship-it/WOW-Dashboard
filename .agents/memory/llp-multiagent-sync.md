---
name: LLP multi-agent sync
description: How the WOW/LLP betting backend owner coordinates three AI agents, and the recurring "already shipped" trap.
---

# LLP multi-agent coordination

The owner of the WOW/LLP scoring backend (`artifacts/flask-scoring-api/app.py`,
~11k+ lines) drives development across **three agents**: this legacy platform Claude
(builds the actual code), an external ChatGPT thread, and an external Claude
thread. Only the legacy platform agent can see the code; the other two plan from memory.

**Why this matters:** instructions arriving from the user are frequently
*verification checklists or session plans for work that is already shipped*,
because the external agents are reasoning against a stale mental model. Seen
repeatedly: a full F5 "ship it" checklist and a full LLP-Pro 8-task session plan
both arrived AFTER the work was already live.

**How to apply:**
- Before building anything from an external-agent-authored plan, **grep/audit
  the code first** to see what's already shipped. Report a "what's already live"
  table instead of redoing it.
- Keep `artifacts/flask-scoring-api/LLP_GROUND_TRUTH.md` current — it is the
  snapshot the user pastes into the other threads to stop drift. Update it
  whenever a contract, threshold, tag, or step-status changes.
- The source of truth is the code, then that doc. If an external spec disagrees
  with shipped behavior, surface the conflict; don't silently follow the spec.

# Engine invariants worth never breaking

- Additive-only: `model_win_probability − no_vig_implied_probability ==
  sum(model_adjustments)` to 1e-6. Architect proves this each major patch.
- Badge ceilings only ever LOWER a badge, never raise it.
- F5 routing = market *selection*, never bet *approval*. Nothing upgrades to BET
  on F5 availability alone.

# Agreed build order (as of the F5 ship)

F5 (Step 5) → odds-snapshot in-process cron (Step 3) → OpenAI structured
reconciliation + web-search fallback (Steps 6 & 10). OpenAI confirmed in-loop.
