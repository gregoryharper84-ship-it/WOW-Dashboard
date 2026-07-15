---
name: Governance patch gate wiring patterns
description: Non-obvious wiring rules learned while implementing WOW-PATCH-2026-07-15 gate_engine extensions.
---

## Rule 1: source_ceiling gate must always be stamped

Stamp `row["gates"]["source_ceiling"]` unconditionally whenever the row's `market_source_type` matches a prediction-market value (`prediction_market`, `polymarket`, `kalshi_market`) and no `sportsbook_line`/`consensus_line` is present.

Use a `ceiling_applied: bool` flag inside the gate dict to indicate whether the terminal_label was actually capped — do NOT skip writing the gate entry when the label is already below the ceiling.

**Why:** Callers audit the gate to confirm the ceiling was evaluated. If the gate key is absent, they cannot distinguish "gate did not apply" from "gate was never reached." A conditional stamp caused a test failure where the row already had MODEL_QUALIFIED_HOLD (below the ceiling) so the gate was silently skipped.

**How to apply:** In the per-row pipeline loop, always write the gate dict when source type matches, then conditionally overwrite `terminal_label` only when `ceiling_applied` is True.

---

## Rule 2: slate_validation purges future-dated rows before per-row gates

`slate_validation.run()` runs unconditionally early in the per-row loop and calls `continue` on the row if it sets a `terminal_label`. Any gate wired after slate_validation (market_adverse, source_ceiling, etc.) is silently skipped for that row.

**Why:** Test fixtures that used "tomorrow" as `slate_date` (e.g. "2026-07-16" when today is "2026-07-15") triggered SLATE_PURGE and the row never reached the source_ceiling code path.

**How to apply:** In tests for per-row gates that execute after slate_validation, always use today's date as `slate_date` AND pass `target_date=date.fromisoformat(today_str)` explicitly to `run_pipeline()`. Do not rely on `skip_health_gate` alone — it does not bypass slate_validation.

---

## Rule 3: best_available must NOT feed market_adverse

Do not pass `best_available` from enrichment into `market_adverse.run()`. The market adverse gate checks adversity vs the *reference* sportsbook or consensus line, not the best price obtainable across all books.

**Why:** A row with PP line 25.5 MORE and `best_available=25.0` triggered REJECT_MARKET_ADVERSE_PUSH_LOSS even though `sportsbook_line=25.5` (exact match — not adverse). best_available reflects pricing optimization, not consensus adversity.

**How to apply:** Call `market_adverse.run(row, sportsbook_line=enr.get("sportsbook_line"), consensus_line=enr.get("consensus_line"))` — omit `best_available` entirely.
