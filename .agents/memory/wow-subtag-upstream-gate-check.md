---
name: WOW sub-tag gates must check upstream reject gates first
description: A new non-terminal sub-tag condition can be structurally unreachable if an earlier hard-reject gate already rejects on the same missing-field condition.
---

When adding a new non-terminal sub-tag (e.g. a data-quality/advisory tag meant to
downgrade a classification rather than hard-reject it), don't just add the new gate
check in isolation — walk every gate that runs *before* it in the same code path and
confirm none of them already reject on the exact condition the new tag is meant to
catch.

**Why:** implemented a `DATA_QUALITY_HOLD` sub-tag meant to fire when a projection
falls back to average-only support (median missing, only average available). The main
daily-scan classifier (`classify_prop()`) handled this correctly, but a second,
independent scoring path (`POST /final-lock`) had an earlier gate that unconditionally
hard-rejected whenever the median was missing — regardless of whether an average was
available. The new sub-tag logic was correct but dead code in that endpoint until the
earlier gate's condition was audited and narrowed to only reject when *no* data at all
was available.

**How to apply:** when a WOW pipeline patch introduces a new advisory/non-terminal gate,
grep every other call site/endpoint that performs equivalent classification (this
codebase has multiple independent scoring paths — the daily-scan job and various
`/final-lock`-style endpoints — that don't share a single gate pipeline) and check each
one's earlier gates for a stricter reject on the same input condition. Verify with a
live curl test that exercises exactly the fallback condition, not just a unit test of
the new logic in isolation.
