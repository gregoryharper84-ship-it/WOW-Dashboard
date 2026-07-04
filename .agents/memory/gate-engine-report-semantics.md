---
name: gate_engine observability report semantics
description: why market_enrichment_report (and similar per-gate counters) can count a row even when its final terminal label is unrelated to that gate
---

In `gate_engine/pipeline.py`, `run_pipeline()`'s per-row loop calls each gate
(`market_gate.run()`, `outlier_gate.run()`, etc.) unconditionally in sequence.
Only a small, specific set of gates short-circuit the loop with `continue`
(e.g. `data_contract` when not skipped, `source_grade` conflict, house rules).
Everything else — including `market_gate` — still runs and mutates
`row["gates"][...]` / `row["blockers"]` even on rows that are ultimately
terminal-labeled for a totally different reason (e.g. `REJECT_DATA_QUALITY`
for a missing player field).

**Why:** `classify()` picks the final label using its own priority order
(data failure checks first, then market/outlier gates), but that's a
*separate* pass from gate execution. Any observability report built by
scanning `row["gates"]`/`row["blockers"]` (like `market_enrichment_report`)
reflects **gate-level evaluation**, not **final classification** — a row can
show up as "capped by NO_MARKET_AVAILABLE" in such a report while its actual
terminal label is unrelated to the market at all.

**How to apply:** When adding or reviewing any new observability/reporting
field over gate_engine rows, don't assume "row X's counter fired" implies
"row X's final label matches that gate." Write tests for rows that fail an
unrelated gate first — the market/outlier gates still execute on them.
