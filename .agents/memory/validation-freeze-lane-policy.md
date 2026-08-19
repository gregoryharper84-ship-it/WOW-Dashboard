---
name: Validation-freeze lane policy
description: Narrow rules for unblocking the frozen 1IP validation experiment while keeping unsupported lanes fail-closed.
---

The validation freeze permits only a narrowly scoped 1IP integration repair when the existing handoff prevents real rows from reaching the event tree, calibration, and immutable prediction logger. Do not turn manual enrichment, pseudo-Elo, static league priors, or proxy prices into production evidence.

**Why:** The forward-test experiment cannot collect its required settled sample if 1IP rows never reach the logger, while unsupported WNBA, Tennis, MMA, Soccer, and Kalshi lanes must not be made to appear operational through qualitative fallbacks.

**How to apply:** Keep started-event `SLATE_PURGE`, unsupported-specialist `MODEL_UNAVAILABLE`, missing multi-outcome fields `DATA_CONTRACT_FAIL`, unavailable Kalshi inventory `KALSHI_DATA_UNOBTAINABLE`, and below-threshold MLB edge rejection intact. After one real 1IP proof, re-freeze model development and prioritize start-time intake, approved lane hydration, shared outcome-space support, and cohort-based calibration review.