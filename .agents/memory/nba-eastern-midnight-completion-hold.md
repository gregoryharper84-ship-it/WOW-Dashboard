# NBA #2472 Eastern-midnight historical completion — read-only hold

Status: **HOLD — no edits made.** `basketball_event_hydration_runtime.py` is
owned by the PR #2472 source line; overlapping edits require coordination with
its current source owner before any change lands here.

## What the current code does (PR #2472 tip `3531a4804`)

File: `artifacts/wow-engine/basketball_event_hydration_runtime.py`

- Line 210: `last_completed_day = clock.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)`
- Lines 327-347: a completed game is withheld until *after* the Eastern game day
  has passed, and the guard rejects any row whose Eastern `game_date` is
  `>= captured.astimezone(ZoneInfo("America/New_York")).date()`, or whose
  `game_date_time` Eastern date differs from `game_date`, or whose tipoff is in
  the future.

Observed consequence: a game that tips after 00:00Z (i.e. the prior evening in
the Eastern calendar) cannot be treated as historically complete until the
following Eastern day. Backfill/historical-completion therefore lags by one
Eastern day, and any replay window ending at midnight UTC undercounts the
Eastern slate.

## Why this is not fixed here

- The completion semantics decide which games count as historical truth for
  calibration/backfill; changing them is a model/governance decision, not a
  plumbing repair.
- The file is inside PR #2472's changed set, so an edit here would create a
  conflicting, overlapping diff rather than the smallest independent increment.

## Required before editing

1. Confirm with the #2472 source owner whether the rollover withhold is
   intentional (partial-day protection) or a defect.
2. Agree the completion clock/timezone contract as a single testable rule.
3. Land the change with a regression test that pins a post-midnight-UTC Eastern
   slate and proves `rows_in = rows_completed + rows_held + rows_rejected`.

Until then the NBA capture work in this branch only reads/publishes evidence and
stays fail-closed; it makes no completion-state claim.
