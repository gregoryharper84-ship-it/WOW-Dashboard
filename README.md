# WOW Betting Engine

Canonical repository for the governed WOW Betting Engine.

| Project identity | Current value |
| --- | --- |
| Current generation | **V17 — ACTIVE** |
| Legacy generation | V16 — `LEGACY` / `SUPERSEDED` |
| Canonical repository | `WOW-Dashboard` |
| Production runtime | Existing Render/Supabase governed stack |
| Primary host | Existing `WOW_BETTING_ENGINE` Custom GPT |
| Team/event capability | Existing `LLP_TEAM_BETTING_ENGINE` Custom GPT |
| Global terminal authority | `V17_TERMINAL_REDUCER` |
| Execution authority | `can_execute=false` |

V17 is an in-place generation promotion, not a separate product or second
runtime. Historical V16 artifacts remain available for audit, regression,
rollback, and provenance, but they are not current generation authority. See
[`docs/wow/GENERATION_STATUS.md`](docs/wow/GENERATION_STATUS.md) and
[`artifacts/wow-engine/V17_PRODUCTION_STATUS.md`](artifacts/wow-engine/V17_PRODUCTION_STATUS.md)
for the controlling lifecycle status.

**New contributors:** Start with the
[repository runtime map](docs/wow/REPOSITORY_RUNTIME_MAP.md) for the canonical
production entry point, separate service/workspace manifests, test and CI entry
points, and the independent source-to-production acceptance checklist. The
status documents and `render.yaml` are not live deployment attestations.

Live wager and market-order execution is permanently disabled.