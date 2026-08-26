---
name: Moneyline market-snapshot handoff
description: Contract decision — one adapter between odds acquisition and the moneyline scorer, with counters + fail-closed breach.
---

Decision: sportsbook odds must enter the moneyline scorer only through the
shared MoneylineMarketSnapshot contract, built once at the Odds API
acquisition boundary and preserved by event_id (cached) — never re-fetched or
re-interpreted through a second schema downstream.

**Why:** three incompatible odds schemas coexisted and the board scan stripped
the event id, so all books were silently dropped between endpoint and scorer.

**How to apply:**
- Event identity: prefer the upstream event_id everywhere; fuzzy team/date
  keys are fallback only.
- The alias table travels INSIDE the snapshot so every downstream consumer
  resolves participants against the same mapping; once a snapshot is supplied,
  an unusable/partial/misaligned one blocks scoring — never a live re-fetch.
- Fail closed: books fetched but none usable by the scorer — including a
  one-sided/partial market or unresolvable participants — must surface as a
  contract-breach terminal state and block scoring; never a silent zero-book
  score and never a live re-fetch once a snapshot was supplied.
